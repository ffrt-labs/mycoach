"""Code-built views of a weekly plan: its Coach notes (prompt v5, #243), and the plan
email's day cards, week strip and notices.

The Coach notes views are shared by the plan email and the dashboard's plan page,
which both render a cardio session's stored Blocks and the plan's focus and Guardrails.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError

from mycoach.coaching.email_schemas import Block, PlanNotes
from mycoach.coaching.life_context import DayEffect, Episode, LifeContext
from mycoach.config import Settings
from mycoach.email.coach_notes import (
    NO_VALUE,
    NOTES_UNAVAILABLE,
    BlockRow,
    block_rows,
    stored_notes,
)
from mycoach.email.life_views import EQUIPMENT_LABEL
from mycoach.email.links import default_schedule_url
from mycoach.email.tones import LIFE_CONTEXT_STYLE, QUALITY_INTENTS
from mycoach.models.life_context import EpisodeKind, IllnessSeverity
from mycoach.models.plan import INTENT_LABEL, PlannedSession, Track, WeeklyPlan
from mycoach.models.routine import ROUTINE_ERROR_PLANNED_SETS, is_routine_error
from mycoach.sport_display import sport_display

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
    return stored_notes(stored, PlanNotes).notes


def plan_notes_unavailable(plan: WeeklyPlan, sessions: Iterable[PlannedSession]) -> bool:
    """The plan's v5 cardio prompt ran and left no Coach notes: the notes fell back.

    The plan email and the plan page say so with the same rule (#249). The cardio
    prompt was served (``cardio_prompt_version``) yet no ``PlanNotes`` were stored and
    no cardio session generated: a parsed v5 cardio plan always stores its notes, so a
    plan from before Coach notes, whose cardio sessions did generate, shows nothing.
    """
    if not plan.cardio_prompt_version or stored_plan_notes(plan.coach_notes) is not None:
        return False
    return not any(s.track == Track.CARDIO and s.generation_error is None for s in sessions)


def _routine_error_sets(ex: Mapping[str, Any]) -> int | None:
    """The routine's set count of an exercise planned around a routine error, else None.

    The weekly plan stores such an exercise as ``ROUTINE_ERROR_PLANNED_SETS`` with the
    routine's count in ``routine_sets``; a plan stored before that carries it in
    ``sets``.
    """
    sets = ex.get("routine_sets", ex.get("sets"))
    return sets if is_routine_error(sets) else None


@dataclass(frozen=True)
class ExerciseRow:
    """One row of a gym day card's exercise table: "Squat (Barbell) | 6 × 5 | 7 | log"."""

    name: str
    sets_reps: str
    rpe: str
    load: str
    routine_error: bool


LOAD_TO_LOG = "log"
"""The Load cell of an exercise with no target load yet: the athlete logs what they lift."""


def _rep_range(reps: object) -> str:
    """ "8-10" as "8–10"."""
    return str(reps if reps is not None else "?").replace("-", "–")


def exercise_rows(details: Mapping[str, Any] | None) -> list[ExerciseRow]:
    """A gym session's exercises (routine + gym adjustment) as exercise-table rows.

    The Load column reads "log" until the gym adjustment sets a target load. A
    routine-error set count shows as the planned "3*", the asterisk pointing at the
    card's footnote and the Routine error notice.
    """
    exercises = (details or {}).get("exercises")
    if not isinstance(exercises, list):
        return []
    rows = []
    for ex in exercises:
        if not isinstance(ex, dict):
            continue
        sets = ex.get("sets", "?")
        error = _routine_error_sets(ex) is not None
        if error:
            sets = f"{ROUTINE_ERROR_PLANNED_SETS}*"
        kg = ex.get("target_weight_kg")
        rows.append(
            ExerciseRow(
                name=str(ex.get("name", "Exercise")),
                sets_reps=f"{sets} × {_rep_range(ex.get('reps'))}",
                rpe=str(ex["rpe"]) if ex.get("rpe") is not None else NO_VALUE,
                load=f"{kg:g} kg" if isinstance(kg, (int, float)) and kg else LOAD_TO_LOG,
                routine_error=error,
            )
        )
    return rows


def _routine_error_footnote(details: Mapping[str, Any] | None) -> str | None:
    """ "* Routine says 36 / 32 sets — assumed 3 until fixed.", under the exercise table."""
    exercises = (details or {}).get("exercises")
    if not isinstance(exercises, list):
        return None
    counts: list[int] = []
    for ex in exercises:
        sets = _routine_error_sets(ex) if isinstance(ex, dict) else None
        if sets is not None and sets not in counts:
            counts.append(sets)
    if not counts:
        return None
    said = " / ".join(str(n) for n in counts)
    return f"* Routine says {said} sets — assumed {ROUTINE_ERROR_PLANNED_SETS} until fixed."


def plan_card(session: Mapping[str, Any], settings: Settings) -> dict[str, Any]:
    """A day card's view: header, focus line, exercise or block table, foot (#243).

    A session that failed to generate (``generation_error``) is an app problem, not
    coaching (spec principle 5): its placeholder notes give way to a Plan failed notice
    linking to the plan page, where the plan is regenerated.
    """
    raw = session.get("details")
    details: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    exercises = exercise_rows(details)
    foot = details.get("foot")
    failed = session.get("generation_error") is not None
    return {
        **session,
        "sport_style": sport_display(session.get("sport")).chip,
        "focus": None if failed else session.get("notes"),
        "notice": {
            "label": "Plan failed",
            "tone": "watch",
            # A gym session falls back to its bare routine; a cardio one has nothing.
            "text": "The load and RPE adjustment failed to generate: this is the base routine."
            if exercises
            else "This session failed to generate.",
            "link_url": f"{settings.app_base_url}/plan",
            "link_text": "Regenerate the plan →",
        }
        if failed
        else None,
        "exercises": exercises,
        "blocks": [] if exercises else plan_block_rows(details),
        "foot": _routine_error_footnote(details)
        if exercises
        else (foot if isinstance(foot, str) else None),
    }


def _plan_duration(minutes: int) -> str:
    """ "4 h 30 min", "2 h" or "45 min"."""
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def plan_totals(sessions: list[dict]) -> dict[str, str]:  # type: ignore[type-arg]
    """The hero's count and total time, computed from the planned sessions."""
    n = len(sessions)
    minutes = sum(s.get("duration_minutes") or 0 for s in sessions)
    return {
        "count": f"{n} session{'' if n == 1 else 's'}",
        "total": _plan_duration(minutes) if minutes else "",
    }


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _plan_day_index(session: dict) -> int | None:  # type: ignore[type-arg]
    """The session's weekday (0 = Monday), from ``day_of_week`` or else its ``day_name``."""
    day = session.get("day_of_week")
    if isinstance(day, int) and 0 <= day <= 6:
        return day
    name = str(session.get("day_name") or "").strip().lower()
    return next((i for i, d in enumerate(_WEEKDAYS) if d.lower() == name), None)


def week_strip(
    sessions: list[dict],  # type: ignore[type-arg]
    travel_days: set[int],
) -> list[dict]:  # type: ignore[type-arg]
    """Seven cells, Monday first: each day's sessions (sport, minutes, intent label), whether
    a quality intent is planned that day, and whether a trip covers it."""
    days: list[dict] = [  # type: ignore[type-arg]
        {"abbr": name[:3].upper(), "sessions": [], "quality": False, "travel": i in travel_days}
        for i, name in enumerate(_WEEKDAYS)
    ]
    for s in sessions:
        i = _plan_day_index(s)
        if i is None:
            continue
        sport = sport_display(s.get("sport"))
        intent = s.get("intent")
        days[i]["sessions"].append(
            {
                "sport": sport.short,
                "style": sport.chip,
                "minutes": s.get("duration_minutes"),
                "intent": INTENT_LABEL.get(intent) if intent else None,
            }
        )
        if intent in QUALITY_INTENTS:
            days[i]["quality"] = True
    return days


def _trip_days(trip: Episode, week_start: date) -> list[int]:
    """The weekdays (0 = Monday) of ``week_start``'s week the trip covers; an open trip
    runs to the end of the week."""
    week_end = week_start + timedelta(days=6)
    first = max(trip.started_on, week_start)
    last = min(trip.ended_on or week_end, week_end)
    return [(first - week_start).days + i for i in range((last - first).days + 1)]


def _trip_notice(trip: Episode, week_start: date) -> str:
    """ "Trip Wed–Fri · hotel gym · 6 h ahead": what the athlete logged, nothing inferred."""
    days = _trip_days(trip, week_start)
    starts_in_week = trip.started_on >= week_start
    ends_in_week = trip.ended_on is not None and trip.ended_on <= week_start + timedelta(days=6)
    first, last = _WEEKDAYS[days[0]][:3], _WEEKDAYS[days[-1]][:3]
    if starts_in_week and ends_in_week:
        span = first if first == last else f"{first}–{last}"
    elif starts_in_week:
        span = f"from {first}"
    elif ends_in_week:
        span = f"until {last}"
    else:
        span = "all week"
    parts = [f"Trip {span}"]
    if trip.equipment is not None:
        parts.append(EQUIPMENT_LABEL[trip.equipment].lower())
    if trip.tz_shift_hours:
        side = "behind" if trip.tz_shift_hours < 0 else "ahead"
        parts.append(f"{abs(trip.tz_shift_hours):g} h {side}")
    if trip.no_training:
        parts.append("no training possible")
    if trip.ended_on is None:
        parts.append("return date not set")
    return " · ".join(parts)


def _illness_notice(illness: Episode) -> str:
    """ "Ill since Fri 13 Nov · mild"."""
    text = f"Ill since {illness.started_on.strftime('%a %-d %b')}"
    return f"{text} · {illness.severity}" if illness.severity else text


def life_context_notices(life: LifeContext | None, week_start: date) -> list[dict[str, str]]:
    """A notice per trip touching the planned week, and per open illness. Facts only: how
    the plan handles them is the coach's to say, not the notice's."""
    if life is None:
        return []
    week_end = week_start + timedelta(days=6)
    notices = []
    for e in life.episodes:
        if e.kind == EpisodeKind.TRAVEL and e.started_on <= week_end:
            if e.ended_on is None or e.ended_on >= week_start:
                effect = DayEffect.TRAVEL_NO_TRAINING if e.no_training else DayEffect.TRAVEL
                text = _trip_notice(e, week_start)
            else:
                continue
        elif e.kind == EpisodeKind.ILLNESS and e.is_open:
            effect = DayEffect.MILD if e.severity == IllnessSeverity.MILD else DayEffect.SICK
            text = _illness_notice(e)
        else:
            continue
        notices.append({"label": "Life context", "tone": LIFE_CONTEXT_STYLE[effect], "text": text})
    return notices


def travel_days(life: LifeContext | None, week_start: date) -> set[int]:
    if life is None:
        return set()
    week_end = week_start + timedelta(days=6)
    return {
        day
        for e in life.episodes
        if e.kind == EpisodeKind.TRAVEL
        and e.started_on <= week_end
        and (e.ended_on is None or e.ended_on >= week_start)
        for day in _trip_days(e, week_start)
    }


def default_schedule_notices(
    availability_source: str | None, settings: Settings
) -> list[dict[str, str]]:
    """The week was planned against the standing schedule, not something the athlete set."""
    if availability_source != "default":
        return []
    return [
        {
            "label": "Default schedule",
            "tone": "neutral",
            "text": "Planned against your standing schedule — you didn't set this week, "
            "so it was filled in from your default.",
            "link_url": default_schedule_url(settings),
            "link_text": "Change it →",
        }
    ]


def _and_join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def routine_error_notices(
    sessions: list[dict],  # type: ignore[type-arg]
    settings: Settings,
) -> list[dict[str, str]]:
    """A Routine error notice per day whose routine lists more than ``ROUTINE_MAX_SETS``
    sets of an exercise: "Friday lists Deadlift and Pull Up at 36 sets, Split Squat at
    32. Planned as 3." It is an app problem, so it links to the routine to fix it."""
    notices = []
    for s in sessions:
        details = s.get("details")
        exercises = details.get("exercises") if isinstance(details, dict) else None
        if not isinstance(exercises, list):
            continue
        by_sets: dict[int, list[str]] = {}
        for ex in exercises:
            sets = _routine_error_sets(ex) if isinstance(ex, dict) else None
            if sets is not None:
                by_sets.setdefault(sets, []).append(str(ex.get("name", "Exercise")))
        if not by_sets:
            continue
        groups = [f"{_and_join(names)} at {n}" for n, names in by_sets.items()]
        groups[0] += " sets"
        notices.append(
            {
                "label": "Routine error",
                "tone": "watch",
                "text": f"{s.get('day_name')} lists {', '.join(groups)}. "
                f"Planned as {ROUTINE_ERROR_PLANNED_SETS}.",
                "link_url": f"{settings.app_base_url}/routine",
                "link_text": "Fix routine →",
            }
        )
    return notices


def notes_unavailable_notices(unavailable: bool) -> list[dict[str, str]]:
    if not unavailable:
        return []
    return [{"label": "Coach", "tone": "neutral", "text": f"{NOTES_UNAVAILABLE}."}]
