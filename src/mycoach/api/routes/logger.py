"""Companion-logger API — data the offline logger PWA needs (API-key guarded)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.api.deps import require_api_key
from mycoach.database import get_db
from mycoach.models.activity import Activity, GymWorkoutDetail

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
