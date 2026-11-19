"""Database queries that gather context data for prompt building."""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.coaching.athlete import athlete_id_for
from mycoach.coaching.body import BodySummary, summarize_body
from mycoach.coaching.life_context import episodes_between, excusing_days, travel_on
from mycoach.coaching.prescriptions import NO_PRESCRIPTION, prescriptions_for
from mycoach.coaching.substitution import (
    DEFAULT_RULES,
    TRAVEL_RULES,
    RulesForDay,
    SubstitutionRules,
    can_substitute,
    classify_activity_intent,
)
from mycoach.config import get_settings
from mycoach.models.availability import DefaultAvailability, WeeklyAvailability
from mycoach.models.coaching import MesocycleConfig
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.routine import RoutineDay, WorkoutRoutine
from mycoach.models.sport_profile import SportProfile

logger = logging.getLogger(__name__)








async def get_availability_for_week(
    session: AsyncSession, user_id: int, week_start: date
) -> list[dict[str, Any]]:
    """Get availability slots for a given week as a list of dicts."""
    stmt = (
        select(WeeklyAvailability)
        .where(
            WeeklyAvailability.user_id == user_id,
            WeeklyAvailability.week_start == week_start,
        )
        .order_by(WeeklyAvailability.day_of_week)
    )
    result = await session.execute(stmt)
    slots = result.scalars().all()
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return [
        {
            "day_of_week": s.day_of_week,
            "day_name": day_names[s.day_of_week],
            "sport": s.sport,
        }
        for s in slots
    ]


async def get_default_availability(
    session: AsyncSession, user_id: int
) -> list[DefaultAvailability]:
    """Get the user's standing weekly schedule, ordered by day. May be empty."""
    stmt = (
        select(DefaultAvailability)
        .where(DefaultAvailability.user_id == user_id)
        .order_by(DefaultAvailability.day_of_week)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_mesocycle_context(session: AsyncSession, user_id: int) -> str | None:
    """Build a human-readable mesocycle context string from all configured mesocycles.

    Returns None if no mesocycles are configured.
    """
    stmt = (
        select(MesocycleConfig)
        .where(MesocycleConfig.user_id == user_id)
        .order_by(MesocycleConfig.sport)
    )
    result = await session.execute(stmt)
    configs = result.scalars().all()

    if not configs:
        return None

    parts = []
    for cfg in configs:
        is_deload = cfg.current_week >= cfg.block_length_weeks
        line = (
            f"- {cfg.sport}: Week {cfg.current_week}/{cfg.block_length_weeks} "
            f"({cfg.phase} phase, started {cfg.start_date})"
        )
        if is_deload:
            line += " — DELOAD WEEK: reduce volume ~40%, reduce intensity"
        if cfg.progression_rules:
            line += f"\n  Progression rules: {cfg.progression_rules}"
        parts.append(line)

    return "Current mesocycle status:\n" + "\n".join(parts)


SessionStatus = Literal[
    "done",
    "substituted",
    "adapted_travel",
    "not_done",
    "not_done_travelling",
    "excused_ill",
    "excused_travel",
]

OWED_STATUSES: frozenset[str] = frozenset({"not_done", "not_done_travelling"})
"""Statuses of a session still owed this week (open in the briefing and the Logger)."""


class SubstituteActivity(TypedDict):
    """The unplanned activity that filled a leftover session by intent (#216)."""

    external_id: str
    sport: str
    title: str
    # The classified intent it delivered (a SessionIntent value); None only when a
    # relaxed rule accepted an activity the classifier can't read.
    intent: str | None
    # Key metrics, for judging the stimulus delivered (#217).
    duration_minutes: int | None
    avg_hr: int | None
    training_effect_aerobic: float | None


class ExtraActivity(TypedDict):
    """An in-week activity that filled no planned session (#216)."""

    external_id: str
    sport: str
    title: str
    day: str
    duration_minutes: int | None
    intent: str | None  # classified intent; None when unclassifiable


class SessionAdherence(TypedDict):
    """One planned session of the week and whether it was done.

    ``completed`` is week-scoped (ADR 0005) and true for ``status`` "done",
    "substituted" and "adapted_travel" (#221: substituted under the relaxed travel
    rules on a travel day). "excused_ill" (#220) and "excused_travel" (a "no
    training possible" travel day) are not-done sessions an episode excused: out
    of the week's denominator. "not_done_travelling" is not done, planned on a
    travel day, and still in the denominator. ``actual_day`` names the day of the activity that
    completed it and ``on_planned_day`` says whether that was the planned day;
    both are ``None`` when no activity completed it (not done, or ticked by hand).
    On planned day is informational, never a penalty. ``substitute`` is set
    only when ``status`` is "substituted" or "adapted_travel".
    """

    id: int
    day: str
    sport: str
    title: str
    intent: str | None
    completed: bool
    status: SessionStatus
    on_planned_day: bool | None
    actual_day: str | None
    substitute: SubstituteActivity | None


class PlanAdherence(TypedDict):
    """How much of a week's plan was done (``get_plan_adherence_for_week``).

    ``completed_sessions`` counts substituted sessions too; ``substituted_sessions``
    says how many of them were ("4/5 done · 1 substituted"), ``adapted_sessions``
    how many were adapted on a trip (#221). ``extras`` are the week's activities
    that filled no session.

    ``total_sessions`` is the denominator: the planned sessions minus the
    ``excused_sessions`` (#220), which life context episodes excused.
    ``excused_days`` counts the week's excusing days by the status they give
    (``{"excused_ill": 3}``), however many sessions they found to excuse.
    """

    plan_id: int
    plan_summary: str | None
    total_sessions: int
    completed_sessions: int
    substituted_sessions: int
    adapted_sessions: int
    excused_sessions: int
    excused_days: dict[str, int]
    adherence_pct: float
    sessions: list[SessionAdherence]
    extras: list[ExtraActivity]


def _intent_value(activity: dict[str, Any]) -> str | None:
    intent = classify_activity_intent(activity)
    return None if intent is None else intent.value


async def get_plan_adherence_for_week(
    session: AsyncSession,
    user_id: int,
    week_start: date,
    rules_for_day: RulesForDay | None = None,
    today: date | None = None,
) -> PlanAdherence | None:
    """Get plan adherence data for a given week.

    Returns a dict with the plan_id it measured (the week's latest plan),
    total_sessions, completed_sessions, adherence_pct, per-session breakdown
    and the week's extra activities. Returns None if no plan exists for the week.

    ``rules_for_day`` picks the substitution rules for a session by its planned
    date. By default a travel day (#221) gets ``substitution.TRAVEL_RULES`` and
    every other day ``DEFAULT_RULES``; a substitution under the travel rules is
    Adapted (travel), and a session left on a travel day is Not done (travelling).

    Life context episodes (#220) then excuse leftover sessions: each excusing
    day (a sick day) excuses at most one not-done session, its own day's first,
    then the earliest one planned on or before it, never a later one. ``today`` (default: the server's) bounds an open
    episode: it covers days up to today, never the future.
    """
    today = today or date.today()
    stmt = (
        select(WeeklyPlan)
        .where(
            WeeklyPlan.user_id == user_id,
            WeeklyPlan.week_start == week_start,
        )
        .order_by(WeeklyPlan.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    plan = result.scalar_one_or_none()
    if plan is None:
        return None

    sessions_stmt = (
        select(PlannedSession)
        .where(PlannedSession.plan_id == plan.id)
        .order_by(PlannedSession.day_of_week)
    )
    sessions_result = await session.execute(sessions_stmt)
    sessions = sessions_result.scalars().all()

    # Cross-reference actual activities to detect completions missed by post-workout flow.
    # The facts come from the health module, keyed by external id (never SQL on health).
    linked_ids = {s.activity_external_id for s in sessions if s.activity_external_id is not None}
    facts = await health.get_activity_facts(
        await athlete_id_for(session, user_id), week_start, sorted(linked_ids)
    )
    by_id = {f["external_id"]: f for f in facts}
    in_week = [f for f in facts if f["in_week"]]

    # Claim guard (#129): a gym activity with no performed sets never fulfils a
    # prescription. Other sports count as before.
    def fulfils(external_id: str, sport: str) -> bool:
        return sport != "gym" or by_id[external_id]["has_performed_sets"]

    # Pass 1: a session whose linked activity still fulfils it consumes exactly
    # that activity, so it can't also fill a different session.
    done: set[int] = set()
    consumed: set[str] = set()
    done_on: dict[int, date] = {}  # session id -> day of the activity that completed it
    for s in sessions:
        if s.activity_external_id is None:
            continue
        fact = by_id.get(s.activity_external_id)
        if (
            fact is not None
            and s.activity_external_id not in consumed
            and fulfils(s.activity_external_id, fact["sport"])
        ):
            done.add(s.id)
            consumed.add(s.activity_external_id)
            done_on[s.id] = fact["start_date"]

    # Manual completes: no linked activity, nothing consumed.
    for s in sessions:
        if s.completed and s.activity_external_id is None:
            done.add(s.id)

    # Pass 2: match remaining eligible activities to leftover sessions by sport
    # within the week, not by day (ADR 0005): a session done on another day was
    # still done. One activity fills one session, so extras never inflate
    # completion. A session whose link no longer fulfils it is unlinked here:
    # its activity returns to the pool.
    #
    # Unlinked Logger activities feed the pool too (#214, revisiting #146): not
    # picking the plan in the Logger says the athlete didn't follow the
    # prescription to the set, not that the session wasn't done. Set-level
    # adherence scores still need the explicit link (``plan_linking``).
    pool = sorted(
        (
            a
            for a in in_week
            if a["external_id"] not in consumed and fulfils(a["external_id"], a["sport"])
        ),
        key=lambda a: (a["start_date"], a["external_id"]),
    )
    # Day is a soft preference: activities on a session's own day go to it
    # first, then the rest fill leftover sessions in day order.
    for same_day_only in (True, False):
        for s in sessions:
            if s.id in done:
                continue
            planned_on = week_start + timedelta(days=s.day_of_week)
            match = next(
                (
                    a
                    for a in pool
                    if a["sport"] == s.sport
                    and (not same_day_only or a["start_date"] == planned_on)
                ),
                None,
            )
            if match is not None:
                pool.remove(match)
                consumed.add(match["external_id"])
                done.add(s.id)
                done_on[s.id] = match["start_date"]

    # Pass 3 (#216): a leftover session with an intent can be filled by an
    # unplanned activity that delivered that intent and enough of its volume:
    # Substituted. Same-sport matches above go first, and one activity still
    # fills at most one session. The substitute pool is every in-week activity
    # not yet credited; the claim guard is a same-sport rule, and an empty gym
    # session never classifies as strength anyway.
    episodes = await episodes_between(session, user_id, week_start, week_start + timedelta(days=6))

    def travelling(day: date) -> bool:
        return travel_on(episodes, day, today=today) is not None

    if rules_for_day is None:

        def rules_for_day(day: date) -> SubstitutionRules:
            return TRAVEL_RULES if travelling(day) else DEFAULT_RULES

    substitutes = sorted(
        (a for a in in_week if a["external_id"] not in consumed),
        key=lambda a: (a["start_date"], a["external_id"]),
    )
    substituted: dict[int, dict[str, Any]] = {}
    for same_day_only in (True, False):
        for s in sessions:
            if s.id in done:
                continue
            planned_on = week_start + timedelta(days=s.day_of_week)
            rules = rules_for_day(planned_on)
            match = next(
                (
                    a
                    for a in substitutes
                    if (not same_day_only or a["start_date"] == planned_on)
                    and can_substitute(a, s.intent, s.duration_minutes, rules)
                ),
                None,
            )
            if match is not None:
                substitutes.remove(match)
                done.add(s.id)
                done_on[s.id] = match["start_date"]
                substituted[s.id] = match

    # Excusal (#220): after every way of being done, so a session done while ill
    # is done, never excused. A day excuses once however many episodes cover it.
    excusing = excusing_days(episodes, week_start, today=today)
    excused: dict[int, str] = {}
    leftover_days: list[tuple[date, str]] = []
    for day, excused_status in excusing.items():
        own = next(
            (
                s
                for s in sessions
                if s.id not in done
                and s.id not in excused
                and week_start + timedelta(days=s.day_of_week) == day
            ),
            None,
        )
        if own is not None:
            excused[own.id] = excused_status
        else:
            leftover_days.append((day, excused_status))
    # A sick day with nothing of its own excuses the earliest not-done session
    # planned on or before it: owed work it kept the athlete from, never a later one.
    for day, excused_status in leftover_days:
        other = next(
            (
                s
                for s in sessions
                if s.id not in done
                and s.id not in excused
                and week_start + timedelta(days=s.day_of_week) <= day
            ),
            None,
        )
        if other is not None:
            excused[other.id] = excused_status

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    session_list: list[SessionAdherence] = []
    statuses: list[str] = []
    completed = 0
    for s in sessions:
        is_done = s.id in done
        if is_done:
            completed += 1
        actual = done_on.get(s.id)
        sub = substituted.get(s.id)
        on_trip = travelling(week_start + timedelta(days=s.day_of_week))
        status: SessionStatus
        if sub is not None:
            status = "adapted_travel" if on_trip else "substituted"
        elif is_done:
            status = "done"
        else:
            status = excused.get(s.id, "not_done_travelling" if on_trip else "not_done")  # type: ignore[assignment]
        statuses.append(status)
        session_list.append(
            {
                "id": s.id,
                "day": day_names[s.day_of_week],
                "sport": s.sport,
                "title": s.title,
                "intent": s.intent,
                "completed": is_done,
                "status": status,
                "on_planned_day": (
                    None if actual is None else actual == week_start + timedelta(days=s.day_of_week)
                ),
                "actual_day": None if actual is None else day_names[actual.weekday()],
                "substitute": (
                    None
                    if sub is None
                    else {
                        "external_id": sub["external_id"],
                        "sport": sub["sport"],
                        "title": sub["title"],
                        "intent": _intent_value(sub),
                        "duration_minutes": sub["duration_minutes"],
                        "avg_hr": sub["avg_hr"],
                        "training_effect_aerobic": sub["training_effect_aerobic"],
                    }
                ),
            }
        )

    extras: list[ExtraActivity] = []
    for a in substitutes:
        extras.append(
            {
                "external_id": a["external_id"],
                "sport": a["sport"],
                "title": a["title"],
                "day": day_names[a["start_date"].weekday()],
                "duration_minutes": a["duration_minutes"],
                "intent": _intent_value(a),
            }
        )

    total = len(sessions) - len(excused)
    adherence_pct = round(completed / total * 100, 1) if total > 0 else 0.0
    excused_days: dict[str, int] = {}
    for excused_status in excusing.values():
        excused_days[excused_status] = excused_days.get(excused_status, 0) + 1

    return {
        "plan_id": plan.id,
        "plan_summary": plan.summary,
        "total_sessions": total,
        "completed_sessions": completed,
        "substituted_sessions": statuses.count("substituted"),
        "adapted_sessions": statuses.count("adapted_travel"),
        "excused_sessions": len(excused),
        "excused_days": excused_days,
        "adherence_pct": adherence_pct,
        "sessions": session_list,
        "extras": extras,
    }



class OpenSession(TypedDict):
    """A planned session of the week not yet completed (``get_open_this_week``).

    ``status`` places its planned day against today: ``overdue`` (planned day
    passed), ``today`` or ``upcoming``. The planned day is a default, not a
    constraint (ADR 0005), so an overdue session is still on offer.
    """

    id: int
    day: str
    day_of_week: int
    status: Literal["overdue", "today", "upcoming"]
    sport: str
    track: str | None
    title: str
    duration_minutes: int | None
    details: str | None
    notes: str | None


class OpenThisWeek(TypedDict):
    """The week's work still owed, and the days left to do it in (today included)."""

    sessions: list[OpenSession]
    days_remaining: int


async def get_open_this_week(
    session: AsyncSession, user_id: int, today: date
) -> OpenThisWeek | None:
    """The planned sessions of ``today``'s week not yet completed, in planned-day order.

    Completion is the week-scoped one from ``get_plan_adherence_for_week``, so a
    session done on another day is not open and one whose day passed undone is.
    None when the week has no plan.
    """
    week_start = today - timedelta(days=today.weekday())
    adherence = await get_plan_adherence_for_week(session, user_id, week_start, today=today)
    if adherence is None:
        return None

    # An excused session (#220) is no longer owed; one on a trip (#221) still is.
    open_ids = [s["id"] for s in adherence["sessions"] if s["status"] in OWED_STATUSES]
    rows = (
        (
            await session.execute(
                select(PlannedSession)
                .where(PlannedSession.id.in_(open_ids))
                .order_by(PlannedSession.day_of_week, PlannedSession.id)
            )
        )
        .scalars()
        .all()
        if open_ids
        else []
    )
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday = today.weekday()
    return {
        "sessions": [
            {
                "id": ps.id,
                "day": day_names[ps.day_of_week],
                "day_of_week": ps.day_of_week,
                "status": (
                    "overdue"
                    if ps.day_of_week < weekday
                    else "today"
                    if ps.day_of_week == weekday
                    else "upcoming"
                ),
                "sport": ps.sport,
                "track": ps.track,
                "title": ps.title,
                "duration_minutes": ps.duration_minutes,
                "details": ps.details,
                "notes": ps.notes,
            }
            for ps in rows
        ],
        "days_remaining": 7 - weekday,
    }


async def _planned_session_linked_to(
    session: AsyncSession, activity: dict[str, Any], user_id: int
) -> dict[str, Any] | None:
    """The planned session already pointing at this activity, if any."""
    external_id = activity.get("external_id")
    if external_id is None:
        return None

    stmt = (
        select(PlannedSession)
        .join(WeeklyPlan, PlannedSession.plan_id == WeeklyPlan.id)
        .where(PlannedSession.activity_external_id == external_id, WeeklyPlan.user_id == user_id)
    )
    planned = (await session.execute(stmt)).scalars().first()
    return _planned_session_to_dict(planned) if planned is not None else None


def _planned_session_to_dict(planned: PlannedSession) -> dict[str, Any]:
    return {
        "id": planned.id,
        "title": planned.title,
        "sport": planned.sport,
        "day_of_week": planned.day_of_week,
        "duration_minutes": planned.duration_minutes,
        "details": planned.details,
        "notes": planned.notes,
        "completed": planned.completed,
    }


async def find_matching_planned_session(
    session: AsyncSession, activity: dict[str, Any], user_id: int
) -> dict[str, Any] | None:
    """Find the planned session this activity answers.

    An explicit link wins. A session logged in ``/logger`` carries the
    prescription's id on the wire, and ``sources.importer`` has already written
    it onto ``PlannedSession.activity_external_id`` by the time post-workout runs — so
    the plan row itself says which prescription was answered, whatever day the
    lift actually happened on.

    Only when nothing is linked does this fall back to matching by day of week
    and sport. That fallback is still the whole story for Garmin and Hevy, which
    carry no prescription reference and never will; but it is guesswork once the
    week is unpinned from days, so it never overrides what the athlete stated.

    A logger activity could have carried a prescription id and didn't, meaning
    the athlete chose not to follow one (a routine day or a blank "Start
    session"). So an unlinked logger activity gets no fallback guess either.
    """
    linked = await _planned_session_linked_to(session, activity, user_id)
    if linked is not None:
        return linked

    if "logger" in (activity.get("sources") or []):
        return None

    start_time_str = activity.get("start_time")
    if not start_time_str:
        return None

    from datetime import datetime as dt

    try:
        activity_dt = dt.fromisoformat(start_time_str)
    except (ValueError, TypeError):
        return None

    activity_date = activity_dt.date()
    day_of_week = activity_date.weekday()  # 0=Monday

    # Find the Monday of the activity's week
    week_start = activity_date - timedelta(days=day_of_week)

    # Find active plan for that week
    plan_stmt = select(WeeklyPlan).where(
        WeeklyPlan.user_id == user_id,
        WeeklyPlan.week_start == week_start,
        WeeklyPlan.status == "active",
    )
    plan_result = await session.execute(plan_stmt)
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        return None

    # Find matching session by day_of_week and sport
    sport = activity.get("sport", "")

    # Claim guard (#129): an empty gym activity never answers a prescription,
    # so the day-and-sport fallback must skip it same as an explicit claim.
    # Other sports are unaffected — they carry no performed-sets concept.
    if sport == "gym":
        external_id = activity.get("external_id")
        if external_id is None or not await health.has_performed_sets(
            await athlete_id_for(session, user_id), external_id
        ):
            return None

    session_stmt = select(PlannedSession).where(
        PlannedSession.plan_id == plan.id,
        PlannedSession.day_of_week == day_of_week,
        PlannedSession.sport == sport,
    )
    session_result = await session.execute(session_stmt)
    planned = session_result.scalar_one_or_none()
    if planned is None:
        return None

    return _planned_session_to_dict(planned)




async def get_active_routine(session: AsyncSession, user_id: int) -> dict[str, Any] | None:
    """Get the active workout routine as a nested dict, or None if no active routine."""
    from sqlalchemy.orm import selectinload

    stmt = (
        select(WorkoutRoutine)
        .where(WorkoutRoutine.user_id == user_id, WorkoutRoutine.is_active.is_(True))
        .options(selectinload(WorkoutRoutine.days).selectinload(RoutineDay.exercises))
    )
    result = await session.execute(stmt)
    routine = result.scalar_one_or_none()
    if routine is None:
        return None

    return {
        "id": routine.id,
        "name": routine.name,
        "days": [
            {
                "id": day.id,
                "name": day.name,
                "day_of_week": day.day_of_week,
                "order_index": day.order_index,
                "exercises": [
                    {
                        "exercise_id": ex.exercise_id,
                        "exercise_name": ex.exercise_name,
                        "sets": ex.sets,
                        "rep_range": ex.rep_range,
                        "notes": ex.notes,
                        "superset_group": ex.superset_group,
                    }
                    for ex in day.exercises
                ],
            }
            for day in routine.days
        ],
    }










async def get_sport_profiles(session: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    """Get all sport profiles for a user as a list of dicts."""
    stmt = select(SportProfile).where(SportProfile.user_id == user_id).order_by(SportProfile.sport)
    result = await session.execute(stmt)
    return [
        {
            "sport": p.sport,
            "skill_level": p.skill_level,
            "goals": p.goals,
            "preferences": p.preferences,
            "benchmarks": p.benchmarks,
        }
        for p in result.scalars().all()
    ]


async def get_today_planned_sessions(
    session: AsyncSession, user_id: int, today: date | None = None
) -> list[dict[str, Any]]:
    """Get planned sessions for today from the active weekly plan."""
    today = today or date.today()
    day_of_week = today.weekday()
    week_start = today - timedelta(days=day_of_week)

    plan_stmt = select(WeeklyPlan).where(
        WeeklyPlan.user_id == user_id,
        WeeklyPlan.week_start == week_start,
        WeeklyPlan.status == "active",
    )
    plan_result = await session.execute(plan_stmt)
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        return []

    session_stmt = (
        select(PlannedSession)
        .where(
            PlannedSession.plan_id == plan.id,
            PlannedSession.day_of_week == day_of_week,
        )
        .order_by(PlannedSession.id)
    )
    session_result = await session.execute(session_stmt)
    sessions = [
        {
            "title": s.title,
            "sport": s.sport,
            "duration_minutes": s.duration_minutes,
            "details": s.details,
            "track": s.track,
            "notes": s.notes,
            "generation_error": s.generation_error,
        }
        for s in session_result.scalars().all()
    ]

    # Cross-validate against current availability — filter out stale sessions
    avail_stmt = select(WeeklyAvailability).where(
        WeeklyAvailability.user_id == user_id,
        WeeklyAvailability.week_start == week_start,
        WeeklyAvailability.day_of_week == day_of_week,
    )
    avail_result = await session.execute(avail_stmt)
    avail_sports = {a.sport for a in avail_result.scalars().all()}

    if not avail_sports:
        if sessions:
            logger.warning(
                "No availability for day %d but %d planned sessions exist — filtering all",
                day_of_week,
                len(sessions),
            )
        return []

    filtered = [s for s in sessions if s["sport"] in avail_sports]
    if len(filtered) < len(sessions):
        dropped = [s["sport"] for s in sessions if s["sport"] not in avail_sports]
        logger.warning(
            "Filtered %d planned sessions due to availability mismatch: %s",
            len(dropped),
            dropped,
        )
    return filtered






async def get_recent_plan_summaries(
    session: AsyncSession,
    user_id: int,
    weeks: int = 4,
    before_date: date | None = None,
) -> list[dict[str, Any]]:
    """Get summaries of recent weekly plans for trend analysis."""
    before_date = before_date or date.today()
    stmt = (
        select(WeeklyPlan)
        .where(
            WeeklyPlan.user_id == user_id,
            WeeklyPlan.week_start < before_date,
        )
        .order_by(WeeklyPlan.week_start.desc())
        .limit(weeks)
    )
    result = await session.execute(stmt)
    plans = result.scalars().all()

    summaries: list[dict[str, Any]] = []
    for plan in plans:
        sessions_stmt = select(PlannedSession).where(PlannedSession.plan_id == plan.id)
        sessions_result = await session.execute(sessions_stmt)
        sessions = sessions_result.scalars().all()

        total = len(sessions)
        completed = sum(1 for s in sessions if s.completed)
        adherence_pct = round(completed / total * 100, 1) if total > 0 else 0.0

        summaries.append(
            {
                "week_start": str(plan.week_start),
                "summary": plan.summary,
                "total_sessions": total,
                "completed_sessions": completed,
                "adherence_pct": adherence_pct,
            }
        )

    return summaries


# --- Coaching context from factual health queries -----------------------------------------
#
# The Observed health facts come from the health module (``mycoach.health``), as plain dicts,
# never from SQL against the health schema (map #85, ticket #186). These adapters resolve the
# user's Athlete ID, ask health, and (for set-level reads) join in coaching's own prescription
# fulfillments. Health never returns prompt-shaped data; shaping stays here and in
# ``prompt_builder``.


async def get_today_health(
    session: AsyncSession, user_id: int, today: date | None = None
) -> dict[str, Any]:
    """Get today's health snapshot as a dict (empty dict if none)."""
    return await health.get_today_health(await athlete_id_for(session, user_id), today)


async def get_health_trends(
    session: AsyncSession, user_id: int, days: int = 3, today: date | None = None
) -> list[dict[str, Any]]:
    """Get recent health snapshots (excluding today) as a list of dicts."""
    return await health.get_health_trends(await athlete_id_for(session, user_id), days, today)


async def get_health_trends_averaged(
    session: AsyncSession, user_id: int, days: int = 7, today: date | None = None
) -> dict[str, Any]:
    """Get a 7-day averaged health summary instead of individual daily snapshots."""
    return await health.get_health_trends_averaged(
        await athlete_id_for(session, user_id), days, today
    )


BODY_HISTORY_DAYS = 120
"""How far back the body summary reads: past the 28-day rate and 6-week waist windows, with
room for the EMA to settle (a 90-day-old reading keeps ~0.01% of its weight)."""


async def get_body_summary(
    session: AsyncSession, user_id: int, now: datetime | None = None
) -> BodySummary:
    """The athlete's weight and waist summary as of ``now`` (aware; default now), #198."""
    athlete_id = await athlete_id_for(session, user_id)
    now = now or datetime.now(UTC)
    since = (now - timedelta(days=BODY_HISTORY_DAYS)).astimezone(UTC).replace(tzinfo=None)
    measurements = await health.list_body_measurements(athlete_id, since=since, limit=None)
    return summarize_body(
        measurements,
        height_cm=await health.get_athlete_height(athlete_id),
        now=now,
        tz=ZoneInfo(get_settings().timezone),
    )


async def get_recent_activities(
    session: AsyncSession, user_id: int, days: int = 3, today: date | None = None
) -> list[dict[str, Any]]:
    """Get recent activities as a list of dicts."""
    return await health.get_recent_activities(await athlete_id_for(session, user_id), days, today)


async def get_activities_for_week(
    session: AsyncSession, user_id: int, week_start: date
) -> list[dict[str, Any]]:
    """Get all activities within a specific week (Monday to Sunday)."""
    return await health.get_activities_for_week(await athlete_id_for(session, user_id), week_start)


def _with_prescriptions(
    activity_external_id: str | None,
    sets: list[dict[str, Any]],
    fulfilled: dict[tuple[str, str, int], Any],
) -> list[dict[str, Any]]:
    """Join coaching's prescription (what the plan called for) onto performed sets."""
    out = []
    for d in sets:
        own = fulfilled.get(
            (activity_external_id or "", d["exercise_title"], d["set_index"]), NO_PRESCRIPTION
        )
        out.append({**d, "prescribed_weight_kg": own.weight_kg, "prescribed_reps": own.reps})
    return out


async def get_activity_with_details(
    session: AsyncSession, activity_external_id: str, user_id: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Get an activity and its gym workout details as dicts, keyed by external id.

    Returns (activity_dict, gym_details_list). gym_details_list is empty for non-gym activities
    and carries each set's prescription from coaching's fulfillments.
    Raises ValueError if activity not found.
    """
    activity, sets = await health.get_activity_with_details(
        await athlete_id_for(session, user_id), activity_external_id
    )
    fulfilled = await prescriptions_for(session, [activity["external_id"]])
    return activity, _with_prescriptions(activity["external_id"], sets, fulfilled)


async def get_similar_activities(
    session: AsyncSession,
    user_id: int,
    sport: str,
    exclude_external_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Get recent activities of the same sport for trend comparison."""
    return await health.get_similar_activities(
        await athlete_id_for(session, user_id), sport, exclude_external_id, limit
    )


async def get_last_week_gym_performance(
    session: AsyncSession, user_id: int, exercise_ids: list[str], week_start: date
) -> list[dict[str, Any]]:
    """Get last week's gym workout details for specific exercises."""
    return await health.get_last_week_gym_performance(
        await athlete_id_for(session, user_id), exercise_ids, week_start
    )


async def get_last_week_all_activities(
    session: AsyncSession, user_id: int, week_start: date
) -> list[dict[str, Any]]:
    """ALL activities from the previous week (all sports); gym ones carry 'gym_details'."""
    activities = await health.get_last_week_all_activities(
        await athlete_id_for(session, user_id), week_start
    )
    fulfilled = await prescriptions_for(session, [a["external_id"] for a in activities])
    for a in activities:
        if "gym_details" in a:
            a["gym_details"] = _with_prescriptions(a["external_id"], a["gym_details"], fulfilled)
    return activities


async def get_last_week_cardio_performance(
    session: AsyncSession, user_id: int, week_start: date
) -> list[dict[str, Any]]:
    """Get swimming and running activities from the previous week."""
    return await health.get_last_week_cardio_performance(
        await athlete_id_for(session, user_id), week_start
    )


async def get_gym_details_for_week(
    session: AsyncSession, user_id: int, week_start: date
) -> list[dict[str, Any]]:
    """Get all gym set/rep/weight details for every gym session in the given week."""
    return await health.get_gym_details_for_week(await athlete_id_for(session, user_id), week_start)


async def get_gym_performance_history(
    session: AsyncSession, user_id: int, week_start: date, weeks: int = 3
) -> list[dict[str, Any]]:
    """Get per-exercise aggregated performance for N weeks before week_start (health-owned)."""
    return await health.get_gym_performance_history(
        await athlete_id_for(session, user_id), week_start, weeks
    )
