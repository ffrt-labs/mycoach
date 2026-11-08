"""In-memory Langfuse capture for tracing tests."""

import json
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import pytest
from langfuse._client.resource_manager import LangfuseResourceManager
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mycoach.config import Settings
from mycoach.observability.scores import Score
from mycoach.observability.tracing import LangfuseTracer, Tracer, build_tracer


def langfuse_settings() -> Settings:
    return Settings(
        llm_provider="anthropic",
        claude_api_key="test",
        env="test",
        langfuse_base_url="http://langfuse.invalid",
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
    )


def tracer_with(exporter: SpanExporter) -> Tracer:
    """A real Langfuse tracer whose spans go to ``exporter``, never the network.

    Scores never reach the network either: the client's ``create_score`` is
    replaced by a recorder, its calls kept in ``tracer.client.score_calls``.
    """
    LangfuseResourceManager.reset()
    tracer = build_tracer(
        langfuse_settings(), span_exporter=exporter, tracer_provider=TracerProvider()
    )
    assert isinstance(tracer, LangfuseTracer)
    calls: list[dict[str, Any]] = []
    client: Any = tracer.client
    client.score_calls = calls
    client.create_score = lambda **kwargs: calls.append(kwargs)
    return tracer


@dataclass
class Captured:
    tracer: Tracer
    exporter: InMemorySpanExporter

    def spans(self) -> list[ReadableSpan]:
        self.tracer.flush()
        return list(self.exporter.get_finished_spans())

    def roots(self) -> list[ReadableSpan]:
        return [s for s in self.spans() if attr(s, "langfuse.internal.as_root")]

    def generations(self) -> list[ReadableSpan]:
        spans = [s for s in self.spans() if attr(s, "langfuse.observation.type") == "generation"]
        return sorted(spans, key=lambda s: s.start_time or 0)


def attr(span: ReadableSpan, key: str) -> Any:
    return (span.attributes or {}).get(key)


def json_attr(span: ReadableSpan, key: str) -> Any:
    value = attr(span, key)
    return None if value is None else json.loads(value)


def trace_hex(span: ReadableSpan) -> str:
    assert span.context is not None
    return format(span.context.trace_id, "032x")


def span_hex(span: ReadableSpan) -> str:
    assert span.context is not None
    return format(span.context.span_id, "016x")


def parent_hex(span: ReadableSpan) -> str | None:
    return None if span.parent is None else format(span.parent.span_id, "016x")


@pytest.fixture
def captured() -> Generator[Captured, None, None]:
    exporter = InMemorySpanExporter()
    tracer = tracer_with(exporter)
    yield Captured(tracer, exporter)
    LangfuseResourceManager.reset()


class RecordingScoreSink:
    """The fake ``ScoreSink``: keeps every emitted score, in order."""

    def __init__(self) -> None:
        self.scores: list[Score] = []

    def emit(self, score: Score) -> None:
        self.scores.append(score)

    def by_id(self) -> dict[str, Score]:
        return {s.id: s for s in self.scores}

    def values(self) -> dict[str, float]:
        """Score ID to value, the usual thing to assert on."""
        return {s.id: s.value for s in self.scores}
