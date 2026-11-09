"""Seed Langfuse with the bundled coaching prompts (#174).

Creates version 1 of each ``mycoach/*`` chat prompt from ``prompts/v2``,
labelled ``production``, converted to Langfuse's ``{{var}}`` syntax. A prompt
that already exists, with any version or label, is skipped, so running it
again is a no-op. Uses the LANGFUSE_* variables the app uses:

    python -m mycoach.tools.seed_langfuse_prompts
"""

import logging
import sys

from langfuse import Langfuse
from langfuse.model import ChatMessageDict

from mycoach.coaching.prompt_builder import BUNDLED_VERSION
from mycoach.coaching.prompt_store import (
    LANGFUSE_PROMPT_NAMES,
    PRODUCTION_LABEL,
    langfuse_messages,
)

logger = logging.getLogger(__name__)


def seed_prompts(client: Langfuse) -> list[str]:
    """Create each prompt that does not exist yet; returns the names created."""
    created = []
    for operation, name in LANGFUSE_PROMPT_NAMES.items():
        # The list endpoint sees every version and label; a get by label
        # would miss a prompt with no production version and duplicate it.
        if client.api.prompts.list(name=name).data:
            logger.info("%s exists; skipped", name)
            continue
        client.create_prompt(
            name=name,
            type="chat",
            prompt=[
                ChatMessageDict(role=m["role"], content=m["content"])
                for m in langfuse_messages(operation)
            ],
            labels=[PRODUCTION_LABEL],
            config={"source": f"bundled:{BUNDLED_VERSION}"},
            commit_message=f"Seeded from prompts/{BUNDLED_VERSION}",
        )
        logger.info("%s created", name)
        created.append(name)
    return created


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
    created = seed_prompts(client)
    logger.info("Created %d prompt(s)", len(created))
    return 0


if __name__ == "__main__":
    sys.exit(main())
