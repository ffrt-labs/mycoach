"""Companion-logger API — data the offline logger PWA needs (API-key guarded)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mycoach.api.deps import require_api_key
from mycoach.database import get_db
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.routine import RoutineDay, WorkoutRoutine
from mycoach.schemas.routine import WorkoutRoutineRead

router = APIRouter(prefix="/api/logger", tags=["logger"])

# MVP: single user, id=1
DEFAULT_USER_ID = 1


class ExerciseListResponse(BaseModel):
    exercises: list[str]


@router.get(
    "/exercises",
    response_model=ExerciseListResponse,
    dependencies=[Depends(require_api_key)],
)
async def list_exercises(
    session: AsyncSession = Depends(get_db),
) -> ExerciseListResponse:
    """Distinct exercise titles from the user's gym history.

    Cached locally by the offline logger for free-text autocomplete.
    """
    stmt = (
        select(distinct(GymWorkoutDetail.exercise_title))
        .join(Activity, GymWorkoutDetail.activity_id == Activity.id)
        .where(Activity.user_id == DEFAULT_USER_ID)
        .order_by(GymWorkoutDetail.exercise_title)
    )
    result = await session.execute(stmt)
    return ExerciseListResponse(exercises=list(result.scalars().all()))


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
