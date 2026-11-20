"""Seed Langfuse with the bundled coaching prompts (#174, #196).

Creates version 1 of each ``mycoach/*`` chat prompt from the bundled prompts
(``prompts/<bundled_version(operation)>``), labelled ``production``, converted to
Langfuse's ``{{var}}`` syntax, with ``config.source = "bundled:<version>"``.
For a prompt that already exists, the latest version (whatever its labels) is
compared with the compiled bundled messages: when they match, the prompt is
skipped; when they differ and the latest version was itself seeded, a new
``production`` version with the bundled text is published. A latest version
edited in the Langfuse UI (no ``bundled:`` source) is never overwritten: the
tool logs a warning and skips it. So running it again is a no-op, and running
it after a new bundled version (e.g. one adding a variable) publishes it.
Uses the LANGFUSE_* variables the app uses:

    python -m mycoach.tools.seed_langfuse_prompts
"""

import logging
import sys
from typing import Any

from langfuse import Langfuse
from langfuse.model import ChatMessageDict

from mycoach.coaching.prompt_builder import Operation, bundled_version
from mycoach.coaching.prompt_store import (
    LANGFUSE_PROMPT_NAMES,
    PRODUCTION_LABEL,
    langfuse_messages,
)

logger = logging.getLogger(__name__)


def _messages(operation: Operation) -> list[ChatMessageDict]:
    return [
        ChatMessageDict(role=m["role"], content=m["content"]) for m in langfuse_messages(operation)
    ]


SEEDED_PREFIX = "bundled:"
"""``config.source`` of a version this tool published; a UI edit carries no such source."""


def _latest(client: Langfuse, name: str, versions: list[int]) -> Any:
    return client.get_prompt(name, version=max(versions), type="chat", cache_ttl_seconds=0)


def _matches(latest: Any, wanted: list[ChatMessageDict]) -> bool:
    current = [(m.get("role"), m.get("content")) for m in latest.prompt]
    return current == [(m["role"], m["content"]) for m in wanted]


def _was_seeded(latest: Any) -> bool:
    source = (latest.config or {}).get("source")
    return isinstance(source, str) and source.startswith(SEEDED_PREFIX)


def seed_prompts(client: Langfuse) -> list[str]:
    """Publish each prompt that is missing, or whose latest version was itself seeded
    and differs from the bundled text; returns the names published.

    A latest version edited in the Langfuse UI is never overwritten: it is logged as a
    warning and skipped, so port the bundled change by hand there.
    """
    published = []
    for operation, name in LANGFUSE_PROMPT_NAMES.items():
        messages = _messages(operation)
        version = bundled_version(operation)
        # The list endpoint sees every version and label; a get by label
        # would miss a prompt with no production version.
        listed = client.api.prompts.list(name=name).data
        versions = [v for meta in listed for v in meta.versions]
        if versions:
            latest = _latest(client, name, versions)
            if _matches(latest, messages):
                logger.info("%s unchanged; skipped", name)
                continue
            if not _was_seeded(latest):
                logger.warning(
                    "%s: latest version %d was not seeded (edited in the Langfuse UI?); "
                    "skipped, port prompts/%s into it by hand",
                    name,
                    max(versions),
                    version,
                )
                continue
        client.create_prompt(
            name=name,
            type="chat",
            prompt=list(messages),
            labels=[PRODUCTION_LABEL],
            config={"source": f"{SEEDED_PREFIX}{version}"},
            commit_message=f"Seeded from prompts/{version}",
        )
        logger.info("%s %s", name, "updated" if versions else "created")
        published.append(name)
    return published


def main() -> int:
    from mycoach.config import get_settings

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    if not (
        settings.langfuse_base_url and settings.langfuse_public_key and settings.langfuse_secret_key
    ):
        logger.error("Set LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY")
        return 1
    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
        tracing_enabled=False,
    )
    published = seed_prompts(client)
    logger.info("Published %d prompt(s)", len(published))
    return 0


if __name__ == "__main__":
    sys.exit(main())
