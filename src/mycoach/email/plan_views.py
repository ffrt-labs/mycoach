"""Code-built views of a weekly plan's Coach notes (prompt v5, #243).

Shared by the plan email and the dashboard's plan page, which both render a cardio
session's stored Blocks and the plan's focus and Guardrails.
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from mycoach.coaching.email_schemas import Block, PlanNotes
from mycoach.email.coach_notes import BlockRow, block_rows, coach_notes

logger = logging.getLogger(__name__)


def plan_block_rows(details: Mapping[str, Any] | None) -> list[BlockRow]:
    """A v5 cardio session's stored Blocks (``details["blocks"]``) as block-table rows.

    A block that does not validate is left out rather than half-rendered; details
    without blocks (a gym session, a v4 cardio session) give none.
    """
    blocks = (details or {}).get("blocks")
    if not isinstance(blocks, list):
        return []
    valid = []
    for raw in blocks:
        try:
            valid.append(Block.model_validate(raw))
        except ValidationError:
            logger.warning("Ignoring an invalid stored block %r", raw)
    return block_rows(valid)


def stored_plan_notes(stored: str | None) -> PlanNotes | None:
    """``WeeklyPlan.coach_notes`` as ``PlanNotes``, or ``None`` when there are none
    (or they are not valid JSON on the contract)."""
    if not stored:
        return None
    try:
        return coach_notes(json.loads(stored), PlanNotes)
    except json.JSONDecodeError:
        return None
