"""Code-built views of Coach notes (prompt v5, #238) for the email templates.

Shared by every email on the structured-output contract; the matching macros are in
``templates/components/_coach_notes.html``. The coach writes only the slot text; the
labels, chips and table layout around it are decided here.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from mycoach.coaching.email_schemas import Block, Tip
from mycoach.email.tones import Chip, tone_chip

M = TypeVar("M", bound=BaseModel)

NOTES_UNAVAILABLE = "Coach notes unavailable today"
"""The neutral Notice an email shows in place of its Coach notes (``notes_fallback``);
also the dashboard briefing's."""

# What the pages say for a stored insight without Coach notes to show (#249, #244).
# The fallback (``NotesKind.FALLBACK``) and a legacy insight (``NotesKind.LEGACY``)
# each get a short, neutral line per context. Exposed to the templates as ``copy``.
NOTES_UNAVAILABLE_THIS_WEEK = "Coach notes unavailable for this week"
"""A weekly recap's or a plan's notes fallback."""
NOTES_UNAVAILABLE_SHORT = "Coach notes unavailable"
"""A post-workout's notes fallback: History's badge and its insight page."""
LEGACY_BRIEFING = "This briefing predates Coach notes"
LEGACY_RECAP = "This recap predates Coach notes"
LEGACY_ANALYSIS = "Analysis predates Coach notes"
"""A legacy post-workout: History's badge and its insight page."""
LEGACY_SESSION_DETAILS = "Details predate Coach notes"
"""A legacy cardio session on the plan page."""

PAGE_COPY: Mapping[str, str] = {
    "notes_unavailable": NOTES_UNAVAILABLE,
    "notes_unavailable_this_week": NOTES_UNAVAILABLE_THIS_WEEK,
    "notes_unavailable_short": NOTES_UNAVAILABLE_SHORT,
    "legacy_briefing": LEGACY_BRIEFING,
    "legacy_recap": LEGACY_RECAP,
    "legacy_analysis": LEGACY_ANALYSIS,
    "legacy_session_details": LEGACY_SESSION_DETAILS,
}
"""The lines above by name, as the page templates read them (``copy.legacy_recap``)."""

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


class NotesKind(StrEnum):
    """Which of the three things a stored insight holds (#249, #244)."""

    COACH_NOTES = "coach_notes"
    """v5 Coach notes, rendered."""
    LEGACY = "legacy"
    """An insight from before Coach notes (prompt v4): no longer rendered, only named."""
    FALLBACK = "fallback"
    """The engine's notes fallback (content ``null``): the prompt ran, no notes came."""


@dataclass(frozen=True)
class StoredNotes(Generic[M]):
    """What a stored insight holds, as a page renders it (#249, #244): its ``kind``,
    and the v5 Coach notes when that kind is ``COACH_NOTES``."""

    kind: NotesKind
    notes: M | None = None
    """The v5 Coach notes, validated as the page's model; ``None`` for the other kinds."""

    @property
    def legacy(self) -> bool:
        return self.kind is NotesKind.LEGACY

    @property
    def fell_back(self) -> bool:
        return self.kind is NotesKind.FALLBACK


def stored_notes(content: str | None, model: type[M]) -> StoredNotes[M]:
    """An insight's stored JSON ``content`` read for a page: the one place pages tell
    v5 Coach notes, a legacy insight and the fallback apart.

    A JSON object that validates as ``model`` is v5; any other JSON object is legacy.
    ``null`` (the engine's notes fallback), no content, and anything that is not a JSON
    object are the fallback.
    """
    try:
        data = json.loads(content) if content else None
    except json.JSONDecodeError:
        return StoredNotes(NotesKind.FALLBACK)
    if not isinstance(data, dict):
        return StoredNotes(NotesKind.FALLBACK)
    notes = coach_notes(data, model)
    if notes is None:
        return StoredNotes(NotesKind.LEGACY)
    return StoredNotes(NotesKind.COACH_NOTES, notes)


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
