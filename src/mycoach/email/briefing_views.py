"""Code-built views of the daily briefing on the v5 contract (#241).

The coach writes the Lead, the options' steps and why, the guardrail, the sleep
note and tonight's tips (``DailyBriefingEmail``). Everything around them is decided
here: the verdict from ``readiness``, each option's session from Open this week by
id, and last night's tiles from the day's health snapshot.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mycoach.coaching.context import OpenSession, OpenThisWeek
from mycoach.coaching.email_schemas import Lead, Readiness, SessionOption, Tip
from mycoach.email.tones import Chip, tone_chip
from mycoach.email.workout_stats import StatGrid, Tile, cell_width, gutters, stat_grid
from mycoach.models.plan import INTENT_LABEL

logger = logging.getLogger(__name__)

VERDICT: Mapping[Readiness, str] = {
    "go_hard": "Go hard",
    "moderate": "Moderate",
    "active_recovery": "Active recovery",
    "rest": "Rest",
}
"""The verdict the briefing shows for a ``readiness``: mapped by code, never written
by the coach."""


@dataclass(frozen=True)
class HeadlineView:
    verdict: str
    chip: Chip
    summary: str


def headline_view(readiness: Readiness, lead: Lead) -> HeadlineView:
    """ "Go hard" [Good] and the coach's one-sentence summary."""
    return HeadlineView(
        verdict=VERDICT[readiness],
        chip=tone_chip(lead.tone, lead.tone.capitalize()),
        summary=lead.summary,
    )


# --- Do today · pick one --------------------------------------------------------------

RECOMMENDED_CHIP = Chip("Recommended", "accent")
STACK_NOTE = "Don't stack both."
"""Under two options: they are alternatives."""


@dataclass(frozen=True)
class OptionView:
    title: str
    steps: list[str]
    why: str
    recommended: bool
    minutes: str | None = None
    """ "75 min", from the open session."""
    context: str | None = None
    """ "Open since Mon · gym · strength", from the open session."""
    open_session_id: int | None = None


@dataclass(frozen=True)
class OptionsView:
    options: list[OptionView]

    @property
    def from_open_sessions(self) -> bool:
        return any(o.open_session_id is not None for o in self.options)

    @property
    def stack_note(self) -> str | None:
        return STACK_NOTE if len(self.options) > 1 else None

    @property
    def pick(self) -> int | None:
        """The recommended option's open session: "Today's pick" in Open this week."""
        return next((o.open_session_id for o in self.options if o.recommended), None)


def _session_context(s: OpenSession) -> str:
    """ "Open since Mon · gym · strength", "Planned today · …" or "Planned Thu · …"."""
    day = s["day"][:3]
    when = {"overdue": f"Open since {day}", "today": "Planned today"}.get(
        s["status"], f"Planned {day}"
    )
    intent = INTENT_LABEL.get(s["intent"], s["intent"]) if s["intent"] else None
    return " · ".join(part for part in (when, s["sport"], intent) if part)


def options_view(
    options: Sequence[SessionOption], open_week: OpenThisWeek | None
) -> OptionsView | None:
    """The coach's options resolved against Open this week, or None (none left).

    An option linked by id is named from its open session; an id that is not open
    this week is dropped with a warning. An option with neither id nor title is
    dropped. Only the first recommended option keeps its chip.
    """
    sessions = {s["id"]: s for s in open_week["sessions"]} if open_week else {}
    views: list[OptionView] = []
    for option in options:
        recommended = option.recommended and not any(v.recommended for v in views)
        steps, why = list(option.steps), option.why
        if option.open_session_id is not None:
            session = sessions.get(option.open_session_id)
            if session is None:
                logger.warning(
                    "Daily briefing: option for open session %s dropped, not open this week",
                    option.open_session_id,
                )
                continue
            minutes = session["duration_minutes"]
            views.append(
                OptionView(
                    title=session["title"],
                    steps=steps,
                    why=why,
                    recommended=recommended,
                    minutes=f"{minutes} min" if minutes else None,
                    context=_session_context(session),
                    open_session_id=session["id"],
                )
            )
        elif option.title:
            views.append(OptionView(option.title, steps, why, recommended))
        else:
            logger.warning("Daily briefing: option with neither session id nor title dropped")
    return OptionsView(views) if views else None


# --- Last night -----------------------------------------------------------------------


@dataclass(frozen=True)
class Stage:
    """One sleep stage: "Deep 133m · 24%", its bar ``width`` a whole percentage."""

    label: str
    minutes: int
    share: int
    width: int
    css: str


@dataclass(frozen=True)
class LastNightView:
    tiles: list[Tile]
    stages: list[Stage]

    @property
    def grid(self) -> StatGrid | None:
        return stat_grid(self.tiles)


def _sleep_score_tile(score: int) -> Tile:
    """The sleep score with Garmin's qualifier, in its tone: "97 Excellent"."""
    if score >= 90:
        sub, tone = "Excellent", "good"
    elif score >= 80:
        sub, tone = "Good", "good"
    elif score >= 60:
        sub, tone = "Fair", "watch"
    else:
        sub, tone = "Poor", "alert"
    return Tile("Sleep score", str(score), sub=sub, tone=tone)


def _hours(minutes: int) -> str:
    """ "9h03"."""
    hours, rest = divmod(minutes, 60)
    return f"{hours}h{rest:02d}"


_STAGES = (
    ("Deep", "sleep_deep_minutes", "sl-deep"),
    ("REM", "sleep_rem_minutes", "sl-rem"),
    ("Light", "sleep_light_minutes", "sl-light"),
)


def _stages(snapshot: Mapping[str, object]) -> list[Stage]:
    """Deep, REM and light sleep as shares of the night (awake time included)."""
    minutes = [_minutes(snapshot.get(key)) for _, key, _ in _STAGES]
    slept = sum(m or 0 for m in minutes)
    if None in minutes or not slept:
        return []
    night = slept + (_minutes(snapshot.get("sleep_awake_minutes")) or 0)
    return [
        Stage(label, m, share=round(100 * m / night), width=round(100 * m / slept), css=css)
        for (label, _, css), m in zip(_STAGES, minutes, strict=True)
        if m is not None
    ]


def _minutes(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def last_night_view(snapshot: Mapping[str, object] | None) -> LastNightView | None:
    """Last night's sleep and resting HR from the day's health snapshot, or None
    without any of them. Every number here is read from the data, never the coach."""
    if not snapshot:
        return None
    tiles = []
    score = snapshot.get("sleep_score")
    if isinstance(score, int):
        tiles.append(_sleep_score_tile(score))
    duration = snapshot.get("sleep_duration_minutes")
    if isinstance(duration, int) and duration > 0:
        tiles.append(Tile("Sleep", _hours(duration)))
    rhr = snapshot.get("resting_hr")
    if isinstance(rhr, int):
        tiles.append(Tile("Resting HR", str(rhr), unit="bpm"))
    stages = _stages(snapshot)
    if not tiles and not stages:
        return None
    return LastNightView(tiles, stages)


# --- Tonight --------------------------------------------------------------------------


@dataclass(frozen=True)
class TipView:
    text: str
    chip: Chip | None
    """The tip's tone as a chip; a neutral tip has none."""
    width: str
    """Its cell's share of the row, in percent: "33.33"."""
    gutter: str
    """Its cell's side padding: the tips sit in a row like the stat grid's tiles."""


def tonight_view(tips: Sequence[Tip]) -> list[TipView]:
    if not tips:
        return []
    width, sides = cell_width(len(tips)), gutters(len(tips))
    return [
        TipView(
            t.text,
            None if t.tone == "neutral" else tone_chip(t.tone, t.tone.capitalize()),
            width,
            side,
        )
        for t, side in zip(tips, sides, strict=True)
    ]
