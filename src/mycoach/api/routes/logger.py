"""Companion-logger API — data the offline logger PWA needs (API-key guarded)."""

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mycoach import health
from mycoach.coaching.athlete import athlete_id_for
from mycoach.database import get_db
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.models.routine import RoutineDay, WorkoutRoutine
from mycoach.plan_linking import unlink_planned_session
from mycoach.schemas.logger import (
    LastPerformedExercise,
    LastPerformedSet,
    PrescribedExercise,
    PrescribedSession,
    TrainingWeek,
)
from mycoach.schemas.routine import WorkoutRoutineRead

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


async def _last_performed(session: AsyncSession) -> list[LastPerformedExercise]:
    """For every exercise in history, the sets from the last time it was trained.

    Read through the health module (``get_last_performed``), the same read coaching uses;
    the Logger adapter only maps the plain dicts onto its wire schema.
    """
    athlete_id = await athlete_id_for(session, DEFAULT_USER_ID)
    return [
        LastPerformedExercise(
            exercise_id=row["exercise_id"],
            title=row["title"],
            date=date.fromisoformat(row["date"]),
            sets=[LastPerformedSet(**s) for s in row["sets"]],
        )
        for row in await health.get_last_performed(athlete_id)
    ]


@router.get(
    "/exercises",
    response_model=ExerciseListResponse,
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
        exercises=[ExerciseListItem(**item) for item in await health.list_exercise_catalogue()],
        last_performed=await _last_performed(session),
    )


@router.get(
    "/routines",
    response_model=WorkoutRoutineRead | None,
)
async def get_active_routine(
    session: AsyncSession = Depends(get_db),
) -> WorkoutRoutine | None:
    """The user's active routine, with its days/exercises, for "Start from routine" (#145).

    Returns null if the user has no active routine. Mirrors
    ``api/routes/routines.py::get_active_routine`` but sits under the
    ``/api/logger`` surface the offline logger reads from.
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


@router.post(
    "/sessions/{session_id}/unlink",
    response_model=PrescribedSession,
)
async def unlink_session(
    session_id: int,
    session: AsyncSession = Depends(get_db),
) -> PrescribedSession:
    """Release a wrongly claimed prescription back to to-do (#130).

    Clears the prescription side only (``activity_external_id``, ``completed``); the
    activity that claimed it is untouched — deleting it is a health-side
    action, out of scope here. POST rather than PATCH/DELETE: the standalone
    logger origin's CORS policy only allows GET/POST cross-origin.
    """
    planned = await unlink_planned_session(session, DEFAULT_USER_ID, session_id)
    if planned is None:
        raise HTTPException(status_code=404, detail="Planned session not found.")
    await session.commit()
    return _prescribed_from_planned(planned)
