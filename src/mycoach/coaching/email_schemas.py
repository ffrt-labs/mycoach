"""The structured-output contract (prompt v5): Coach notes as typed, length-limited slots.

The coach writes only into these slots (CONTEXT.md: Coach note); every observed or
derived number around them is rendered by code. The shared slots come first; each
email's output model follows and is the ``response_model`` of its operation's
``OperationSpec`` with ``coach_notes=True``. Pydantic is the gate: a provider that
ignores the schema's ``maxLength`` still has its answer validated here.

The v4 models in ``response_parser`` stay until stored v4 insights age out.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Tone = Literal["good", "neutral", "watch", "alert"]
"""A Coach note's tone, one of the ``TONES`` the email colours (``email/tones.py``)."""

Short = Annotated[str, Field(max_length=40)]


class Slot(BaseModel):
    """A Coach notes model: unknown keys are an error, as the strict schema says."""

    model_config = ConfigDict(extra="forbid")


class Lead(Slot):
    """The headline sentence; code supplies the verdict (briefing, recap)."""

    tone: Tone
    summary: str = Field(max_length=140)


class Headline(Lead):
    """Post-workout only: the coach also names the verdict."""

    verdict: str = Field(max_length=28)


class Action(Slot):
    """One thing to do: imperative, starts with a verb."""

    do: str = Field(max_length=70)
    why: str | None = Field(None, max_length=90)


class Guardrail(Slot):
    """When a stated signal appears, then adjust (CONTEXT.md: Guardrail)."""

    when: str = Field(max_length=60)
    then: str = Field(max_length=60)


class Tip(Slot):
    tone: Tone
    text: str = Field(max_length=90)


BlockPhase = Literal["warmup", "drill", "main", "pull", "kick", "strides", "cooldown"]


class Block(Slot):
    """One row of a prescribed session: its set and target are prescribed numbers."""

    phase: BlockPhase
    set: str = Field(max_length=24)
    cue: str | None = Field(None, max_length=48)
    target: str | None = Field(None, max_length=24)
    rest_s: int | None = None


# --- Post-workout ---------------------------------------------------------------


class PostWorkoutEmail(Slot):
    headline: Headline
    actions: list[Action] = Field(min_length=1, max_length=3)
    zone_note: Tip | None = None
    trend_note: str = Field(max_length=100)
    next_session: list[Block] = Field(max_length=6)
    guardrails: list[Guardrail] = Field(max_length=2)
    week_addition: str | None = Field(None, max_length=60)
    ready_in_hours: int = Field(ge=0, le=96)
    recovery: str = Field(max_length=70)
    analysis_md: str
