"""Companion-logger API — data the offline logger PWA needs (API-key guarded)."""

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mycoach.api.deps import require_api_key
from mycoach.database import get_db
from mycoach.exercise_catalogue import load_exercise_catalogue
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.routine import RoutineDay, WorkoutRoutine
from mycoach.schemas.logger import PrescribedExercise, PrescribedSession, TrainingWeek
from mycoach.schemas.routine import WorkoutRoutineRead

router = APIRouter(prefix="/api/logger", tags=["logger"])

# MVP: single user, id=1
DEFAULT_USER_ID = 1


class ExerciseListItem(BaseModel):
    id: str
    name: str


class ExerciseListResponse(BaseModel):
    exercises: list[ExerciseListItem]


@router.get(
    "/exercises",
    response_model=ExerciseListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_exercises(
    session: AsyncSession = Depends(get_db),
) -> ExerciseListResponse:
    """The stable exercise catalogue cached by the offline logger.

    The dependency is retained so this route shares the logger API's normal
    lifecycle even though the pinned catalogue itself is file-backed.
    """
    del session
    return ExerciseListResponse(
        exercises=[
            ExerciseListItem(id=exercise.id, name=exercise.name)
            for exercise in load_exercise_catalogue()
        ]
    )


@router.get(
    "/routines",
    response_model=WorkoutRoutineRead | None,
    dependencies=[Depends(require_api_key)],
)
async def get_active_routine(
    session: AsyncSession = Depends(get_db),
) -> WorkoutRoutine | None:
    """The user's active routine, with its days/exercises, for the logger to prefill.

    Returns null if the user has no active routine. Mirrors
    ``api/routes/routines.py::get_active_routine`` but sits under the
    API-key-guarded ``/api/logger`` surface the offline logger authenticates against.
    """
    stmt = (
        select(WorkoutRoutine)
        .where(WorkoutRoutine.user_id == DEFAULT_USER_ID, WorkoutRoutine.is_active.is_(True))
        .options(selectinload(WorkoutRoutine.days).selectinload(RoutineDay.exercises))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


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
    sessions = (
        [_prescribed_from_routine_day(day) for day in routine.days] if routine else []
    )
    return TrainingWeek(week_start=monday, sessions=sessions)
