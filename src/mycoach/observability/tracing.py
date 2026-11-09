"""Langfuse tracing of coaching runs.

One trace per coaching run (``Tracer.run``) and one generation per LLM attempt
inside it (``Tracer.generation``). The engine talks only to the ``Tracer``
interface; ``NoopTracer`` stands in when Langfuse is not configured.

Failure policy: no Langfuse failure ever reaches a coaching run. Every SDK call
is guarded and a failure is logged at warn level. Exceptions raised by the run
itself pass through unchanged.
"""

from __future__ import annotations

import base64
import logging
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from langfuse import Langfuse, propagate_attributes
from langfuse._version import __version__ as langfuse_version
from langfuse.model import PromptClient
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from mycoach.observability.trace_ids import (
    daily_briefing_seed,
    post_workout_seed,
    regen_seed,
    trace_id,
    week_session_id,
    weekly_plan_seed,
    weekly_recap_seed,
)

if TYPE_CHECKING:
    from mycoach.coaching.llm_client import LLMResponse
    from mycoach.config import Settings

logger = logging.getLogger(__name__)

# Langfuse's rule for an environment name.
_ENVIRONMENT = re.compile(r"^(?!langfuse)[a-z0-9_-]{1,40}$")

# The SDK's own default API and export timeout, in seconds.
_EXPORT_TIMEOUT_SECONDS = 5

Level = Literal["DEFAULT", "WARNING", "ERROR"]


# --- What gets traced -------------------------------------------------------


@dataclass(frozen=True)
class RunSpec:
    """One coaching run: which trace it is and which week session it joins.

    ``about`` is the date the run is about (the briefing's day, the plan's or
    recap's Monday, the activity's day); it picks the week session.
    ``supersedes`` is the trace ID a forced regeneration replaces.
    """

    name: str
    seed: str
    user_id: int
    about: date
    supersedes: str | None = None

    @property
    def trace_id(self) -> str:
        return trace_id(self.seed)

    @property
    def session_id(self) -> str:
        return week_session_id(self.user_id, self.about)


def _insight_run(
    name: str, seed: str, user_id: int, about: date, regenerated_at: datetime | None
) -> RunSpec:
    if regenerated_at is None:
        return RunSpec(name=name, seed=seed, user_id=user_id, about=about)
    return RunSpec(
        name=name,
        seed=regen_seed(seed, regenerated_at),
        user_id=user_id,
        about=about,
        supersedes=trace_id(seed),
    )


def weekly_plan_run(plan_id: int, user_id: int, week_start: date) -> RunSpec:
    """A forced plan gets a new plan ID, hence a new trace, so no regen seed."""
    return RunSpec(
        name="weekly-plan", seed=weekly_plan_seed(plan_id), user_id=user_id, about=week_start
    )


def daily_briefing_run(
    user_id: int, day: date, *, regenerated_at: datetime | None = None
) -> RunSpec:
    return _insight_run(
        "daily-briefing", daily_briefing_seed(user_id, day), user_id, day, regenerated_at
    )


def post_workout_run(
    activity_id: int, user_id: int, activity_date: date, *, regenerated_at: datetime | None = None
) -> RunSpec:
    return _insight_run(
        "post-workout", post_workout_seed(activity_id), user_id, activity_date, regenerated_at
    )


def weekly_recap_run(
    user_id: int, week_start: date, *, regenerated_at: datetime | None = None
) -> RunSpec:
    return _insight_run(
        "weekly-recap",
        weekly_recap_seed(user_id, week_start),
        user_id,
        week_start,
        regenerated_at,
    )


@dataclass(frozen=True)
class GenerationStart:
    """One LLM attempt, as known before the call.

    ``input`` is the prompt's variable map, so a trace can become a dataset
    item. ``previous_error`` is the parse error of the attempt a retry follows.
    ``metadata`` carries anything else worth recording on the generation.
    ``prompt`` is the Langfuse prompt the generation is linked to, if any.
    """

    operation: str
    attempt: int
    provider: str
    model: str
    max_tokens: int
    input: Mapping[str, str]
    previous_error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    prompt: PromptClient | None = None


class GenerationHandle(Protocol):
    def succeeded(self, response: LLMResponse) -> None:
        """The attempt returned a response that parsed."""

    def failed(
        self,
        *,
        error: str,
        exception: BaseException | None = None,
        response: LLMResponse | None = None,
    ) -> None:
        """The attempt raised (no ``response``) or its response did not parse."""


class Tracer(Protocol):
    @property
    def enabled(self) -> bool: ...

    def run(self, spec: RunSpec) -> AbstractContextManager[None]:
        """Open the run's trace; generations started inside belong to it."""
        ...

    def generation(self, start: GenerationStart) -> GenerationHandle:
        """Start a generation under the current run. End it through the handle."""
        ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


# --- Disabled ---------------------------------------------------------------


class _NoopGeneration:
    def succeeded(self, response: LLMResponse) -> None:
        pass

    def failed(
        self,
        *,
        error: str,
        exception: BaseException | None = None,
        response: LLMResponse | None = None,
    ) -> None:
        pass


_NOOP_GENERATION = _NoopGeneration()


class NoopTracer:
    """Langfuse off: every call does nothing."""

    enabled = False

    @contextmanager
    def run(self, spec: RunSpec) -> Iterator[None]:
        yield

    def generation(self, start: GenerationStart) -> GenerationHandle:
        return _NOOP_GENERATION

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


# --- Langfuse ---------------------------------------------------------------


def _warn(action: str) -> None:
    logger.warning("Langfuse: %s failed; the coaching run continues", action, exc_info=True)


def response_fields(response: LLMResponse) -> dict[str, Any]:
    """What a generation records from a provider response.

    The single mapping from ``LLMResponse`` to generation fields, used for
    successful and failed attempts alike.

    Cost is always explicit, so Langfuse never infers API prices from its
    model table: zero unless the provider is ``paid``, whose real cost is sent
    unchanged.
    """
    cost = response.estimated_cost_usd if response.billing == "paid" else 0
    return {
        "model": response.model,
        "output": response.content,
        "usage_details": {"input": response.input_tokens, "output": response.output_tokens},
        "cost_details": {"total": cost},
        "metadata": {
            "stop_reason": response.stop_reason,
            "latency_ms": response.latency_ms,
            "billing": response.billing,
            "quota_usage": response.quota_usage,
        },
    }


def failure_level(response: LLMResponse | None) -> Level:
    """A provider error (no response) or a truncated response is an error.

    A complete response that merely failed to parse is a warning: the retry
    sibling may still rescue the run.
    """
    if response is None or response.stop_reason == "max_tokens":
        return "ERROR"
    return "WARNING"


class _LangfuseGeneration:
    def __init__(self, observation: Any) -> None:
        self._obs = observation

    def succeeded(self, response: LLMResponse) -> None:
        try:
            self._obs.update(**response_fields(response))
            self._obs.end()
        except Exception:
            _warn("ending a generation")

    def failed(
        self,
        *,
        error: str,
        exception: BaseException | None = None,
        response: LLMResponse | None = None,
    ) -> None:
        try:
            fields = response_fields(response) if response is not None else {}
            metadata = {
                **fields.pop("metadata", {}),
                "error_type": type(exception).__name__ if exception is not None else None,
                "truncated": response is not None and response.stop_reason == "max_tokens",
            }
            self._obs.update(
                **fields,
                metadata=metadata,
                level=failure_level(response),
                status_message=error,
            )
            self._obs.end()
        except Exception:
            _warn("ending a failed generation")


class LangfuseTracer:
    """Tracer backed by a Langfuse client."""

    enabled = True

    def __init__(self, client: Langfuse) -> None:
        self._lf = client

    @property
    def client(self) -> Langfuse:
        """The Langfuse client, shared with the score sink and prompt management
        (one client per key)."""
        return self._lf

    @contextmanager
    def run(self, spec: RunSpec) -> Iterator[None]:
        stack = ExitStack()
        root: Any = None
        try:
            root = stack.enter_context(
                self._lf.start_as_current_observation(
                    name=spec.name,
                    as_type="span",
                    trace_context={"trace_id": spec.trace_id},
                    metadata={"seed": spec.seed},
                )
            )
            trace_metadata = {"seed": spec.seed}
            if spec.supersedes is not None:
                trace_metadata["supersedes"] = spec.supersedes
            stack.enter_context(
                propagate_attributes(
                    user_id=str(spec.user_id),
                    session_id=spec.session_id,
                    trace_name=spec.name,
                    metadata=trace_metadata,
                )
            )
        except Exception:
            _warn(f"opening the {spec.name} trace")
        try:
            yield
        except BaseException as exc:
            if root is not None:
                try:
                    root.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
                except Exception:
                    _warn(f"marking the {spec.name} trace failed")
            raise
        finally:
            try:
                stack.close()
            except Exception:
                _warn(f"closing the {spec.name} trace")

    def generation(self, start: GenerationStart) -> GenerationHandle:
        try:
            observation = self._lf.start_observation(
                name=start.operation,
                as_type="generation",
                model=start.model,
                input=dict(start.input),
                model_parameters={"max_tokens": start.max_tokens},
                prompt=start.prompt,
                metadata={
                    "provider": start.provider,
                    "attempt": start.attempt,
                    "previous_error": start.previous_error,
                    **start.metadata,
                },
            )
        except Exception:
            _warn(f"starting a {start.operation} generation")
            return _NOOP_GENERATION
        return _LangfuseGeneration(observation)

    def flush(self) -> None:
        try:
            self._lf.flush()
        except Exception:
            _warn("flushing")

    def shutdown(self) -> None:
        try:
            self._lf.shutdown()
        except Exception:
            _warn("shutting down")


def langfuse_client(tracer: Tracer) -> Langfuse | None:
    """The Langfuse client behind ``tracer``, or ``None`` when Langfuse is off.

    The one way the score sink and the prompt store reach the client, so
    scores, prompts and traces are on exactly together.
    """
    return tracer.client if isinstance(tracer, LangfuseTracer) else None


class WarnOnExportFailure(SpanExporter):
    """Wraps the span exporter so every failed export is logged at warn level.

    Exports run on the SDK's background thread, so a failure never reaches a
    coaching run; this makes sure it is not silent either.
    """

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception:
            logger.warning("Langfuse: exporting %d spans raised", len(spans), exc_info=True)
            return SpanExportResult.FAILURE
        if result is not SpanExportResult.SUCCESS:
            logger.warning("Langfuse: exporting %d spans failed", len(spans))
        return result

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception:
            _warn("shutting down the span exporter")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:
            _warn("flushing the span exporter")
            return False


def _otlp_exporter(base_url: str, public_key: str, secret_key: str) -> OTLPSpanExporter:
    """The exporter the SDK would build itself, headers included.

    Built here only so it can be wrapped in ``WarnOnExportFailure``: a custom
    exporter does not get the SDK's headers, and Langfuse v4 needs
    ``x-langfuse-ingestion-version: 4`` to show OTel data promptly.
    """
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    return OTLPSpanExporter(
        endpoint=f"{base_url.rstrip('/')}/api/public/otel/v1/traces",
        headers={
            "Authorization": f"Basic {auth}",
            "x-langfuse-sdk-name": "python",
            "x-langfuse-sdk-version": langfuse_version,
            "x-langfuse-public-key": public_key,
            "x-langfuse-ingestion-version": "4",
        },
        timeout=_EXPORT_TIMEOUT_SECONDS,
        compression=Compression.Gzip,
    )


# --- Configuration ----------------------------------------------------------


def _missing_vars(settings: Settings) -> list[str]:
    required = {
        "LANGFUSE_BASE_URL": settings.langfuse_base_url,
        "LANGFUSE_PUBLIC_KEY": settings.langfuse_public_key,
        "LANGFUSE_SECRET_KEY": settings.langfuse_secret_key,
    }
    return [name for name, value in required.items() if not value.strip()]


def build_tracer(
    settings: Settings,
    *,
    span_exporter: SpanExporter | None = None,
    tracer_provider: TracerProvider | None = None,
) -> Tracer:
    """A Langfuse tracer if all three variables are set, else a no-op one.

    ``span_exporter`` and ``tracer_provider`` replace the OTLP exporter and
    the global provider (tests use an in-memory exporter).
    """
    if _missing_vars(settings):
        return NoopTracer()
    base_url = settings.langfuse_base_url.strip()
    public_key = settings.langfuse_public_key.strip()
    secret_key = settings.langfuse_secret_key.strip()
    exporter = span_exporter or _otlp_exporter(base_url, public_key, secret_key)
    env = settings.env.lower()
    try:
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            environment=env if _ENVIRONMENT.match(env) else None,
            span_exporter=WarnOnExportFailure(exporter),
            tracer_provider=tracer_provider,
        )
    except Exception:
        _warn("starting the client")
        return NoopTracer()
    return LangfuseTracer(client)


def announce_langfuse_config(settings: Settings) -> None:
    """The boot log line saying whether Langfuse is on. Never logs the keys."""
    missing = _missing_vars(settings)
    if not missing:
        logger.info("Langfuse enabled: %s", settings.langfuse_base_url.strip())
    elif len(missing) == 3:
        logger.info("Langfuse disabled: missing %s", ", ".join(missing))
    else:
        # Partly configured is almost certainly a deploy mistake.
        logger.warning("Langfuse disabled: missing %s", ", ".join(missing))


_tracer: Tracer | None = None
_tracer_lock = threading.Lock()


def get_tracer() -> Tracer:
    """The process-wide tracer, built from settings on first use.

    Built lazily: the Langfuse client starts threads and may install the
    global OTel provider, which must not happen at import time.
    """
    global _tracer
    with _tracer_lock:
        if _tracer is None:
            from mycoach.config import get_settings

            _tracer = build_tracer(get_settings())
        return _tracer


def set_tracer(tracer: Tracer | None) -> None:
    """Replace the process-wide tracer (``None`` rebuilds it on next use)."""
    global _tracer
    with _tracer_lock:
        _tracer = tracer


def shutdown_tracer() -> None:
    """Flush pending spans and stop the client, if one was ever started."""
    with _tracer_lock:
        tracer = _tracer
    if tracer is not None:
        tracer.shutdown()
