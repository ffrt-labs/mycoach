"""Code-built views of Coach notes (prompt v5, #238) for the email templates.

Shared by every email on the structured-output contract; the matching macros are in
``templates/components/_coach_notes.html``. The coach writes only the slot text; the
labels, chips and table layout around it are decided here.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from mycoach.coaching.email_schemas import Block, Tip
from mycoach.email.tones import Chip, tone_chip

M = TypeVar("M", bound=BaseModel)

NOTES_UNAVAILABLE = "Coach notes unavailable today"
"""The neutral Notice an email shows in place of its Coach notes (``notes_fallback``)."""

BLOCK_PHASE_LABEL: Mapping[str, str] = {
    "warmup": "Warm-up",
    "drill": "Drills",
    "main": "Main",
    "pull": "Pull",
    "kick": "Kick",
    "strides": "Strides",
    "cooldown": "Cool-down",
}

NO_VALUE = "—"
"""An empty table cell: a block's, an exercise's or a trend metric's (a tile with no
value is left out instead)."""


def coach_notes(content: Mapping[str, Any] | M | None, model: type[M]) -> M | None:
    """The stored Coach notes (the insight's JSON content) as ``model``, or ``None``
    when there are none to show.

    ``None`` is the engine's fallback; content that does not validate (a v4 analysis,
    anything off-contract) is treated the same way rather than half-rendered.
    """
    if content is None or isinstance(content, model):
        return content
    try:
        return model.model_validate(content)
    except ValidationError:
        return None


@dataclass(frozen=True)
class StoredNotes(Generic[M]):
    """What a stored insight holds, as a page renders it (#249): v5 Coach notes, a v4
    analysis, or neither (the notes fallback).

    At most one of ``notes`` and ``v4`` is set. When #244 retires the v4 rendering,
    ``v4`` goes and a v4 insight is simply content that is not valid v5: unavailable.
    """

    notes: M | None = None
    """The v5 Coach notes, validated as the page's model."""
    v4: Mapping[str, Any] | None = None
    """A pre-v5 analysis, the raw JSON object the v4 templates read. Removed by #244."""

    @property
    def unavailable(self) -> bool:
        """Nothing to show: the page renders its "Coach notes unavailable" state."""
        return self.notes is None and self.v4 is None


def stored_notes(content: str | None, model: type[M]) -> StoredNotes[M]:
    """An insight's stored JSON ``content`` read for a page: the one place pages tell
    v5, v4 and the fallback apart.

    A JSON object that validates as ``model`` is v5; any other JSON object is v4.
    ``null`` (the engine's notes fallback), no content, and anything that is not a JSON
    object are unavailable.
    """
    try:
        data = json.loads(content) if content else None
    except json.JSONDecodeError:
        return StoredNotes()
    if not isinstance(data, dict):
        return StoredNotes()
    notes = coach_notes(data, model)
    return StoredNotes(notes=notes) if notes is not None else StoredNotes(v4=data)


@dataclass(frozen=True)
class BlockRow:
    phase: str
    set: str
    cue: str | None
    target: str
    rest: str


def block_rows(blocks: Sequence[Block]) -> list[BlockRow]:
    """A session's blocks as table rows: "Warm-up | 300 + 4×50 | RPE 3 | 15s"."""
    return [
        BlockRow(
            phase=BLOCK_PHASE_LABEL[block.phase],
            set=block.set,
            cue=block.cue,
            target=block.target or NO_VALUE,
            rest=f"{block.rest_s}s" if block.rest_s is not None else NO_VALUE,
        )
        for block in blocks
    ]


def tip_chip(tip: Tip) -> Chip:
    """A Tip's tone as its chip: "Watch" in the watch tone."""
    return tone_chip(tip.tone, tip.tone.capitalize())


def ready_chip(hours: int) -> Chip:
    """The recovery chip: "Ready again in < 24 h", in the good tone."""
    return Chip(f"Ready again in < {hours} h", "good")
