"""Seed Langfuse with the bundled coaching prompts (#174, #196).

Creates version 1 of each ``mycoach/*`` chat prompt from ``prompts/v2``,
labelled ``production``, converted to Langfuse's ``{{var}}`` syntax. For a
prompt that already exists, the latest version (whatever its labels) is
compared with the compiled bundled messages: when they differ, a new version
with the bundled text is published and labelled ``production``; when they
match, the prompt is skipped. So running it again is a no-op, and running it
after editing ``prompts/v2`` (e.g. adding a variable) publishes that edit.
Note that a newer version edited in the Langfuse UI is superseded by the
bundled text. Uses the LANGFUSE_* variables the app uses:

    python -m mycoach.tools.seed_langfuse_prompts
"""

import logging
import sys

from langfuse import Langfuse
from langfuse.model import ChatMessageDict

from mycoach.coaching.prompt_builder import BUNDLED_VERSION, Operation
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


def _latest_matches(
    client: Langfuse, name: str, versions: list[int], wanted: list[ChatMessageDict]
) -> bool:
    latest = client.get_prompt(name, version=max(versions), type="chat", cache_ttl_seconds=0)
    current = [(m.get("role"), m.get("content")) for m in latest.prompt]
    return current == [(m["role"], m["content"]) for m in wanted]


def seed_prompts(client: Langfuse) -> list[str]:
    """Publish each prompt that is missing or whose latest version differs
    from the bundled text; returns the names published."""
    published = []
    for operation, name in LANGFUSE_PROMPT_NAMES.items():
        messages = _messages(operation)
        # The list endpoint sees every version and label; a get by label
        # would miss a prompt with no production version.
        listed = client.api.prompts.list(name=name).data
        versions = [v for meta in listed for v in meta.versions]
        if versions and _latest_matches(client, name, versions, messages):
            logger.info("%s unchanged; skipped", name)
            continue
        client.create_prompt(
            name=name,
            type="chat",
            prompt=list(messages),
            labels=[PRODUCTION_LABEL],
            config={"source": f"bundled:{BUNDLED_VERSION}"},
            commit_message=f"Seeded from prompts/{BUNDLED_VERSION}",
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
