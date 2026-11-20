"""The structured-output contract (prompt v5): Coach notes as typed, length-limited slots.

The coach writes only into these slots (CONTEXT.md: Coach note); every observed or
derived number around them is rendered by code. The shared slots come first; each
email's output model follows and is the ``response_model`` of its operation's
``OperationSpec`` with ``coach_notes=True``. Pydantic is the gate: a provider that
ignores the schema's ``maxLength`` still has its answer validated here.

The v4 models in ``response_parser`` stay until stored v4 insights age out.
"""

import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mycoach.coaching.response_parser import DailyBriefingKeyMetrics
from mycoach.models.plan import SessionIntent

logger = logging.getLogger(__name__)

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


# --- Weekly recap ---------------------------------------------------------------


class SessionNote(Slot):
    """A note on one planned session, joined to its row by ``session_id``."""

    session_id: int
    text: str = Field(max_length=80)


class LiftNote(Slot):
    """A trend note on one catalogue exercise, joined by its Exercise ID."""

    exercise_id: str
    tone: Tone
    trend: str = Field(max_length=24)


class WeeklyRecapEmail(Slot):
    headline: Lead
    actions: list[Action] = Field(min_length=1, max_length=3)
    guardrail: Guardrail | None = None
    session_notes: list[SessionNote] = Field(max_length=5)
    recovery_note: str = Field(max_length=100)
    lift_notes: list[LiftNote] = Field(max_length=6)
    load_note: str = Field(max_length=100)
    block_suggestion: str | None = Field(None, max_length=110)
    analysis_md: str


# --- Weekly plan (#243) ---------------------------------------------------------
# The cardio plan's answer. Gym sessions stay the gym adjustment's answer
# (``GymAdjustmentResponse``): the routine and its adjustment are code-rendered.


class CardioSessionV5(Slot):
    day_of_week: int = Field(ge=0, le=6)
    sport: str
    intent: SessionIntent | None = None
    title: str = Field(max_length=40)
    duration_minutes: int
    focus: str = Field(max_length=100)
    blocks: list[Block] = Field(min_length=1, max_length=6)
    foot: str | None = Field(None, max_length=100)

    @field_validator("intent", mode="before")
    @classmethod
    def _unknown_intent_is_none(cls, value: Any) -> Any:
        """Drop an intent outside the enum rather than fail the whole plan (#215)."""
        if value is None or value in {i.value for i in SessionIntent}:
            return value
        logger.warning("Ignoring unknown session intent %r from the cardio plan", value)
        return None


class PlanNotes(Slot):
    """The plan-level Coach notes, stored on the ``WeeklyPlan``: the email's lead
    sentence, its Guardrails, and the goal assessment (dashboard only)."""

    focus: str = Field(max_length=120)
    guardrails: list[Guardrail] = Field(max_length=3)
    goal_assessment: str


class CardioPlanV5(Slot):
    sessions: list[CardioSessionV5] = Field(min_length=1)
    focus: str = Field(max_length=120)
    guardrails: list[Guardrail] = Field(max_length=3)
    goal_assessment: str

    def plan_notes(self) -> PlanNotes:
        return PlanNotes(
            focus=self.focus, guardrails=self.guardrails, goal_assessment=self.goal_assessment
        )


# --- Daily briefing ---------------------------------------------------------------

Readiness = Literal["go_hard", "moderate", "active_recovery", "rest"]
"""The briefing's readiness; code maps it to the verdict the email shows."""


class SessionOption(Slot):
    """One way to train today. Linked to an open session by id, code renders its
    title, sport, intent, minutes and planned day; otherwise ``title`` names it."""

    open_session_id: int | None = None
    title: str | None = Field(None, max_length=32)
    steps: list[Short] = Field(min_length=1, max_length=3)
    why: str = Field(max_length=70)
    recommended: bool


class DailyBriefingEmail(Slot):
    readiness: Readiness
    headline: Lead
    options: list[SessionOption] = Field(min_length=1, max_length=2)
    guardrail: Guardrail | None = None
    sleep_note: str = Field(max_length=60)
    tonight: list[Tip] = Field(max_length=3)
    key_metrics: DailyBriefingKeyMetrics
    """Kept for the prompt log; the email renders its numbers from the DB."""
    analysis_md: str
