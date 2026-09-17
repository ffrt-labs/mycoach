"""Companion-logger API — data the offline logger PWA needs (API-key guarded)."""

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mycoach.api.deps import require_api_key
from mycoach.database import get_db
from mycoach.exercise_catalogue import load_exercise_catalogue
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.routine import RoutineDay, WorkoutRoutine
from mycoach.schemas.logger import (
    LastPerformedExercise,
    LastPerformedSet,
    PrescribedExercise,
    PrescribedSession,
    TrainingWeek,
)

router = APIRouter(prefix="/api/logger", tags=["logger"])

# MVP: single user, id=1
DEFAULT_USER_ID = 1


class ExerciseListItem(BaseModel):
    id: str
    name: str


class ExerciseListResponse(BaseModel):
    """The catalogue, plus the reference row for anything actually trained.

    ``exercises`` is the pinned catalogue (#56). ``last_performed`` is a
    *sibling* list rather than a field on each catalogue item: history covers a
    handful of exercises against 876 catalogue records, and a custom exercise
    (``exercise_id`` null) is not in the catalogue at all, so it would have
    nowhere to hang. See the resolution on #70.
    """

    exercises: list[ExerciseListItem]
    last_performed: list[LastPerformedExercise]


# An exercise is identified by its exercise_id (#55). A custom exercise has
# none, so it falls back to its display title — the only handle it has. The
# title is never a join key for a catalogued exercise.
ExerciseKey = tuple[str | None, str | None]


def _key(exercise_id: str | None, title: str) -> ExerciseKey:
    return (exercise_id, None) if exercise_id else (None, title)


async def _last_performed(session: AsyncSession) -> list[LastPerformedExercise]:
    """For every exercise in history, the sets from the last time it was trained.

    Two passes so the second only ever reads the winning sessions: first the
    (exercise, session) pairs to find each exercise's most recent activity,
    then the sets belonging to those activities alone.
    """
    pairs_stmt = (
        select(
            GymWorkoutDetail.exercise_id,
            GymWorkoutDetail.exercise_title,
            GymWorkoutDetail.activity_id,
            Activity.start_time,
        )
        .join(Activity, Activity.id == GymWorkoutDetail.activity_id)
        .where(Activity.user_id == DEFAULT_USER_ID)
        .distinct()
    )
    latest: dict[ExerciseKey, tuple[datetime, int, str]] = {}
    for exercise_id, title, activity_id, start_time in await session.execute(pairs_stmt):
        key = _key(exercise_id, title)
        current = latest.get(key)
        # Ties on start_time break on the higher activity id — the later import.
        if current is None or (start_time, activity_id) > (current[0], current[1]):
            latest[key] = (start_time, activity_id, title)

    if not latest:
        return []

    sets_stmt = (
        select(GymWorkoutDetail)
        .where(GymWorkoutDetail.activity_id.in_({v[1] for v in latest.values()}))
        .order_by(GymWorkoutDetail.set_index, GymWorkoutDetail.id)
    )
    sets_by_key: dict[ExerciseKey, list[LastPerformedSet]] = {}
    for detail in (await session.execute(sets_stmt)).scalars():
        key = _key(detail.exercise_id, detail.exercise_title)
        won = latest.get(key)
        if won is None or won[1] != detail.activity_id:
            continue  # a different exercise that happens to share this session
        sets_by_key.setdefault(key, []).append(
            LastPerformedSet(
                set_index=detail.set_index,
                set_type=detail.set_type,
                weight_kg=detail.weight_kg,
                reps=detail.reps,
            )
        )

    rows = [
        LastPerformedExercise(
            exercise_id=key[0],
            title=title,
            date=start_time.date(),
            sets=sets_by_key.get(key, []),
        )
        for key, (start_time, _activity_id, title) in latest.items()
    ]
    # Sorted explicitly: the logger caches this payload wholesale, and a list
    # that reshuffles per request is a cache that churns for no reason.
    rows.sort(key=lambda row: row.title)
    return rows


@router.get(
    "/exercises",
    response_model=ExerciseListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_exercises(
    session: AsyncSession = Depends(get_db),
) -> ExerciseListResponse:
    """The stable exercise catalogue cached by the offline logger, plus history.

    The catalogue itself is file-backed and pinned; ``last_performed`` is the
    database half — what the athlete actually lifted last time, for the grey
    reference row. Both ride one response because the logger already pulls this
    endpoint wholesale after every sync, and it already covers *every* exercise
    in history, including one improvised mid-session (#48).
    """
    return ExerciseListResponse(
        exercises=[
            ExerciseListItem(id=exercise.id, name=exercise.name)
            for exercise in load_exercise_catalogue()
        ],
        last_performed=await _last_performed(session),
    )


def _prescribed_from_planned(ps: PlannedSession) -> PrescribedSession:
    """Turn one stored PlannedSession into a wire prescribed session.

    Gym sessions unpack the merged ``details`` blob into typed exercises;
    non-gym sessions pass ``details`` through untouched and are display-only.
    """
    loggable = ps.track == "gym"
    parsed = json.loads(ps.details) if ps.details else None

    exercises: list[PrescribedExercise] = []
    passthrough = None
    if loggable:
        raw = parsed.get("exercises", []) if isinstance(parsed, dict) else []
        exercises = [
            PrescribedExercise(
                exercise_id=ex.get("exercise_id"),
                name=ex.get("name", ""),
                sets=ex.get("sets"),
                rep_range=ex.get("reps"),
                target_weight_kg=ex.get("target_weight_kg"),
                target_rpe=ex.get("rpe"),
                rest_seconds=ex.get("rest_seconds"),
                superset_group=ex.get("superset_group"),
                notes=ex.get("notes"),
            )
            for ex in raw
        ]
    else:
        passthrough = parsed  # cardio's LLM-shaped blob, unvalidated

    return PrescribedSession(
        id=ps.id,
        title=ps.title,
        sport=ps.sport,
        track=ps.track,
        duration_minutes=ps.duration_minutes,
        notes=ps.notes,
        loggable=loggable,
        done=ps.completed,
        exercises=exercises,
        details=passthrough,
    )


def _prescribed_from_routine_day(day: RoutineDay) -> PrescribedSession:
    """Build a gym session from a routine day when no plan exists — weights null."""
    return PrescribedSession(
        id=None,
        title=day.name,
        sport="gym",
        track="gym",
        duration_minutes=None,
        notes=None,
        loggable=True,
        done=False,
        exercises=[
            PrescribedExercise(
                exercise_id=ex.exercise_id,
                name=ex.exercise_name,
                sets=ex.sets,
                rep_range=ex.rep_range,
                target_weight_kg=None,
                target_rpe=None,
                rest_seconds=None,
                superset_group=ex.superset_group,
                notes=ex.notes,
            )
            for ex in day.exercises
        ],
    )


@router.get(
    "/week",
    response_model=TrainingWeek,
    dependencies=[Depends(require_api_key)],
)
async def get_training_week(
    session: AsyncSession = Depends(get_db),
) -> TrainingWeek:
    """The current Monday–Sunday, all sports, merged server-side into one shape.

    Where the coach has prescribed a plan for this week, its sessions ride with
    weights filled. Where there is none, the active routine is rebuilt into the
    same shape with weights null — the logger has no second code path. Only gym
    sessions are ``loggable``; the rest are display-only.
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday())

    plan_stmt = (
        select(WeeklyPlan)
        .where(
            WeeklyPlan.user_id == DEFAULT_USER_ID,
            WeeklyPlan.week_start == monday,
            WeeklyPlan.status == "active",
        )
        .order_by(WeeklyPlan.created_at.desc())
    )
    plan = (await session.execute(plan_stmt)).scalars().first()

    if plan is not None:
        ps_stmt = (
            select(PlannedSession)
            .where(PlannedSession.plan_id == plan.id)
            .order_by(PlannedSession.day_of_week, PlannedSession.id)
        )
        planned = (await session.execute(ps_stmt)).scalars().all()
        return TrainingWeek(
            week_start=monday,
            sessions=[_prescribed_from_planned(ps) for ps in planned],
        )

    # No plan for this week — fall back to the active routine, weights null.
    routine_stmt = (
        select(WorkoutRoutine)
        .where(WorkoutRoutine.user_id == DEFAULT_USER_ID, WorkoutRoutine.is_active.is_(True))
        .options(selectinload(WorkoutRoutine.days).selectinload(RoutineDay.exercises))
    )
    routine = (await session.execute(routine_stmt)).scalar_one_or_none()
    sessions = [_prescribed_from_routine_day(day) for day in routine.days] if routine else []
    return TrainingWeek(week_start=monday, sessions=sessions)
