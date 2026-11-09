"""Where a coaching prompt comes from: Langfuse, or the bundled files.

``PromptStore.serve(operation, variables)`` returns the compiled system and
user messages plus where they came from. With Langfuse configured the
``production`` version of the operation's chat prompt is served; the bundled
``prompts/v2`` files, compiled by ``compile_prompt``, are the fallback. The
fallback is never an error path: a coaching run always gets a prompt.

See docs/adr/0003-langfuse-source-of-truth-for-coaching-prompts.md.
"""

from __future__ import annotations

import logging
import re
import string
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from langfuse import Langfuse
from langfuse.api import NotFoundError
from langfuse.model import ChatPromptClient

from mycoach.coaching.prompt_builder import (
    Operation,
    _load_template,
    compile_prompt,
    get_system_prompt,
)

logger = logging.getLogger(__name__)

BUNDLED_VERSION = "v2"
"""The bundled template set served as the fallback (and seeded as version 1)."""

PRODUCTION_LABEL = "production"

LANGFUSE_PROMPT_NAMES: Mapping[Operation, str] = {
    Operation.DAILY_BRIEFING: "mycoach/daily-briefing",
    Operation.GYM_ADJUSTMENT: "mycoach/gym-adjustment",
    Operation.CARDIO_PLAN: "mycoach/cardio-plan",
    Operation.POST_WORKOUT: "mycoach/post-workout",
    Operation.WEEKLY_RECAP: "mycoach/weekly-recap",
}

# A fetch that fails is one short attempt, then not retried for a while, so
# Langfuse being down never adds the SDK's ~15 s of retries to a coaching call.
FETCH_TIMEOUT_SECONDS = 2
FAILURE_BACKOFF_SECONDS = 60

PromptSource = Literal["langfuse", "bundled"]


@dataclass(frozen=True)
class ServedPrompt:
    """A compiled prompt and its provenance.

    ``version`` is the Langfuse version number (``None`` when bundled).
    ``link`` is the Langfuse prompt object a generation is linked to, or
    ``None`` when bundled.
    """

    system: str
    user: str
    source: PromptSource
    name: str
    version: int | None = None
    link: Any = None

    @property
    def artifact_version(self) -> str:
        """What an artifact records: ``lf:<n>`` or ``bundled:v2``."""
        if self.version is None:
            return f"bundled:{BUNDLED_VERSION}"
        return f"lf:{self.version}"


class PromptStore(Protocol):
    def serve(self, operation: Operation, variables: Mapping[str, str]) -> ServedPrompt:
        """Compile ``operation``'s prompt with its variable map. Never raises
        for a Langfuse problem; ``KeyError`` for a variable the bundled
        template needs and the map lacks, as ``compile_prompt`` does."""
        ...


def bundled_prompt(operation: Operation, variables: Mapping[str, str]) -> ServedPrompt:
    system, user = compile_prompt(operation, variables, version=BUNDLED_VERSION)
    return ServedPrompt(
        system=system, user=user, source="bundled", name=LANGFUSE_PROMPT_NAMES[operation]
    )


class BundledPrompts:
    """Langfuse off: always the bundled files."""

    def serve(self, operation: Operation, variables: Mapping[str, str]) -> ServedPrompt:
        return bundled_prompt(operation, variables)


# --- Langfuse template syntax ----------------------------------------------


def to_langfuse_template(template: str) -> str:
    """A bundled ``str.format`` template in Langfuse's ``{{var}}`` syntax.

    ``{name}`` becomes ``{{name}}`` and the escaped ``{{``/``}}`` become
    literal single braces.
    """
    out: list[str] = []
    for literal, field_name, format_spec, conversion in string.Formatter().parse(template):
        out.append(literal)
        if field_name is not None:
            if format_spec or conversion:
                raise ValueError(f"unsupported placeholder {{{field_name}}} in template")
            out.append("{{" + field_name + "}}")
    return "".join(out)


def langfuse_messages(operation: Operation) -> list[dict[str, str]]:
    """The bundled prompt as a Langfuse chat prompt: system, then user."""
    template = _load_template(f"{operation.value}.txt", BUNDLED_VERSION)
    return [
        {"role": "system", "content": to_langfuse_template(get_system_prompt(BUNDLED_VERSION))},
        {"role": "user", "content": to_langfuse_template(template)},
    ]


_VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class InvalidPromptError(ValueError):
    pass


def compile_langfuse(prompt: ChatPromptClient, variables: Mapping[str, str]) -> tuple[str, str]:
    """Compile a Langfuse chat prompt strictly.

    The SDK never fails a compile: it leaves an unknown ``{{name}}`` in the
    text. Here the prompt must be exactly [system, user] messages and use
    only the variable map's names, or ``InvalidPromptError`` is raised. A
    prompt may drop a variable (an experiment leaving context out); that is
    logged at info level, not rejected.
    """
    messages = prompt.prompt
    shape = [(m.get("type"), m.get("role")) for m in messages]
    if shape != [("message", "system"), ("message", "user")]:
        raise InvalidPromptError(f"expected [system, user] messages, got {shape}")
    used: set[str] = set()
    for message in messages:
        content = str(message.get("content", ""))
        used.update(_VARIABLE.findall(content))
        if "{{" in _VARIABLE.sub("", content):
            raise InvalidPromptError("a {{ ... }} that is not a variable name")
    if unknown := used - set(variables):
        raise InvalidPromptError(f"unknown variables {sorted(unknown)}")
    if dropped := set(variables) - used:
        logger.info(
            "Langfuse prompt %s v%s drops variables %s",
            prompt.name,
            prompt.version,
            sorted(dropped),
        )
    system, user = (dict(m) for m in prompt.compile(**variables))
    return str(system["content"]), str(user["content"])


# --- Serving from Langfuse ---------------------------------------------------


@dataclass
class _Entry:
    prompt: ChatPromptClient | None
    fresh_until: float


class LangfusePrompts:
    """Serves each operation's ``production`` prompt from Langfuse.

    Cached in memory for ``ttl_seconds``. A failed refresh keeps the cached
    version (stale-on-error); a prompt that is gone (404, e.g. its label was
    removed) is dropped. With nothing cached, a failed fetch serves the
    bundled prompt and is not retried for ``FAILURE_BACKOFF_SECONDS``, nor is
    a failed refresh. A template that fails the strict compile serves the
    bundled prompt too. Thread-safe; fetches are synchronous.
    """

    def __init__(
        self,
        client: Langfuse,
        *,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lf = client
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def serve(self, operation: Operation, variables: Mapping[str, str]) -> ServedPrompt:
        name = LANGFUSE_PROMPT_NAMES[operation]
        prompt = self._prompt(name)
        if prompt is None:
            return bundled_prompt(operation, variables)
        try:
            system, user = compile_langfuse(prompt, variables)
        except Exception as exc:
            logger.warning(
                "Langfuse prompt %s v%s does not compile (%s); serving the bundled prompt",
                name,
                prompt.version,
                exc,
            )
            return bundled_prompt(operation, variables)
        return ServedPrompt(
            system=system,
            user=user,
            source="langfuse",
            name=name,
            version=prompt.version,
            link=prompt,
        )

    def _prompt(self, name: str) -> ChatPromptClient | None:
        with self._lock:
            now = self._clock()
            entry = self._cache.get(name)
            if entry is not None and now < entry.fresh_until:
                return entry.prompt
            stale = entry.prompt if entry is not None else None
            try:
                fetched = self._lf.get_prompt(
                    name,
                    label=PRODUCTION_LABEL,
                    type="chat",
                    cache_ttl_seconds=0,  # this class is the cache
                    max_retries=0,
                    fetch_timeout_seconds=FETCH_TIMEOUT_SECONDS,
                )
            except NotFoundError:
                logger.warning(
                    "Langfuse prompt %s has no %s version; serving the bundled prompt",
                    name,
                    PRODUCTION_LABEL,
                )
                self._cache[name] = _Entry(None, now + FAILURE_BACKOFF_SECONDS)
                return None
            except Exception as exc:
                logger.warning(
                    "Langfuse: fetching prompt %s failed (%s); serving the %s prompt",
                    name,
                    exc,
                    "cached" if stale is not None else "bundled",
                )
                self._cache[name] = _Entry(stale, now + FAILURE_BACKOFF_SECONDS)
                return stale
            if not isinstance(fetched, ChatPromptClient):
                logger.warning("Langfuse prompt %s is not a chat prompt; ignoring it", name)
                fetched_chat = None
            else:
                fetched_chat = fetched
            self._cache[name] = _Entry(fetched_chat, now + self._ttl)
            return fetched_chat


# --- Process-wide store --------------------------------------------------------

_store: PromptStore | None = None
_store_lock = threading.Lock()


def build_prompt_store(client: Langfuse | None, ttl_seconds: int) -> PromptStore:
    if client is None:
        return BundledPrompts()
    return LangfusePrompts(client, ttl_seconds=ttl_seconds)


def get_prompt_store() -> PromptStore:
    """The process-wide store: Langfuse's if the tracer has a client.

    Shares the tracer's Langfuse client, so prompts are on exactly when
    tracing is.
    """
    global _store
    with _store_lock:
        if _store is None:
            from mycoach.config import get_settings
            from mycoach.observability.tracing import get_tracer

            client = getattr(get_tracer(), "client", None)
            _store = build_prompt_store(client, get_settings().langfuse_prompt_cache_ttl)
        return _store


def set_prompt_store(store: PromptStore | None) -> None:
    """Replace the process-wide store (``None`` rebuilds it on next use)."""
    global _store
    with _store_lock:
        _store = store
