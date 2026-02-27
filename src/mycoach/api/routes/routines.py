"""Gym routine API routes — manage workout routines with nested days/exercises."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mycoach.database import get_db
from mycoach.models.routine import RoutineDay, RoutineExercise, WorkoutRoutine
from mycoach.schemas.routine import WorkoutRoutineCreate, WorkoutRoutineRead

router = APIRouter(prefix="/api/routines", tags=["routines"])

USER_ID = 1  # Single-user MVP


async def _load_routine(session: AsyncSession, routine_id: int) -> WorkoutRoutine:
    stmt = (
        select(WorkoutRoutine)
        .where(WorkoutRoutine.id == routine_id, WorkoutRoutine.user_id == USER_ID)
        .options(selectinload(WorkoutRoutine.days).selectinload(RoutineDay.exercises))
    )
    result = await session.execute(stmt)
    routine = result.scalar_one_or_none()
    if routine is None:
        raise HTTPException(status_code=404, detail="Routine not found")
    return routine


@router.post("", response_model=WorkoutRoutineRead, status_code=201)
async def create_routine(
    body: WorkoutRoutineCreate,
    session: AsyncSession = Depends(get_db),
) -> WorkoutRoutine:
    """Create a new workout routine with nested days and exercises.

    Deactivates any existing active routines for the user.
    """
    # Deactivate existing active routines
    existing = await session.execute(
        select(WorkoutRoutine).where(
            WorkoutRoutine.user_id == USER_ID,
            WorkoutRoutine.is_active.is_(True),
        )
    )
    for r in existing.scalars().all():
        r.is_active = False

    routine = WorkoutRoutine(user_id=USER_ID, name=body.name)
    for day_data in body.days:
        day = RoutineDay(
            name=day_data.name,
            day_of_week=day_data.day_of_week,
            order_index=day_data.order_index,
        )
        for ex_data in day_data.exercises:
            ex = RoutineExercise(
                exercise_name=ex_data.exercise_name,
                sets=ex_data.sets,
                rep_range=ex_data.rep_range,
                order_index=ex_data.order_index,
                notes=ex_data.notes,
                superset_group=ex_data.superset_group,
            )
            day.exercises.append(ex)
        routine.days.append(day)

    session.add(routine)
    await session.commit()
    return await _load_routine(session, routine.id)


@router.get("/active", response_model=WorkoutRoutineRead | None)
async def get_active_routine(
    session: AsyncSession = Depends(get_db),
) -> WorkoutRoutine | None:
    """Get the active workout routine, or null if none."""
    stmt = (
        select(WorkoutRoutine)
        .where(WorkoutRoutine.user_id == USER_ID, WorkoutRoutine.is_active.is_(True))
        .options(selectinload(WorkoutRoutine.days).selectinload(RoutineDay.exercises))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


@router.put("/{routine_id}", response_model=WorkoutRoutineRead)
async def replace_routine(
    routine_id: int,
    body: WorkoutRoutineCreate,
    session: AsyncSession = Depends(get_db),
) -> WorkoutRoutine:
    """Replace a routine entirely (full update with new days/exercises)."""
    routine = await _load_routine(session, routine_id)

    # Clear existing days (cascade deletes exercises)
    routine.days.clear()
    routine.name = body.name

    for day_data in body.days:
        day = RoutineDay(
            name=day_data.name,
            day_of_week=day_data.day_of_week,
            order_index=day_data.order_index,
        )
        for ex_data in day_data.exercises:
            ex = RoutineExercise(
                exercise_name=ex_data.exercise_name,
                sets=ex_data.sets,
                rep_range=ex_data.rep_range,
                order_index=ex_data.order_index,
                notes=ex_data.notes,
                superset_group=ex_data.superset_group,
            )
            day.exercises.append(ex)
        routine.days.append(day)

    await session.commit()
    return await _load_routine(session, routine.id)


@router.delete("/{routine_id}", status_code=204)
async def deactivate_routine(
    routine_id: int,
    session: AsyncSession = Depends(get_db),
) -> None:
    """Deactivate a routine (soft delete)."""
    routine = await _load_routine(session, routine_id)
    routine.is_active = False
    await session.commit()
