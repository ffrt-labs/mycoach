"""Activity endpoints — list and detail with gym workout details."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.schemas.activity import ActivityRead, GymWorkoutDetailRead

router = APIRouter(prefix="/api/activities", tags=["activities"])

# MVP: single user, id=1
DEFAULT_USER_ID = 1


class PaginatedActivities(BaseModel):
    items: list[ActivityRead]
    total: int
    page: int
    per_page: int


@router.get("", response_model=PaginatedActivities)
async def list_activities(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    sport: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> PaginatedActivities:
    """List activities with pagination and optional sport filter."""
    base_query = select(Activity).where(Activity.user_id == DEFAULT_USER_ID)
    count_query = select(func.count(Activity.id)).where(Activity.user_id == DEFAULT_USER_ID)

    if sport:
        base_query = base_query.where(Activity.sport == sport)
        count_query = count_query.where(Activity.sport == sport)

    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * per_page
    result = await session.execute(
        base_query.order_by(Activity.start_time.desc()).offset(offset).limit(per_page)
    )
    activities = list(result.scalars().all())

    items = []
    for activity in activities:
        item = ActivityRead.model_validate(activity)
        if activity.sport == "gym":
            details_result = await session.execute(
                select(GymWorkoutDetail)
                .where(GymWorkoutDetail.activity_id == activity.id)
                .order_by(GymWorkoutDetail.set_index)
            )
            item.gym_details = [
                GymWorkoutDetailRead.model_validate(d) for d in details_result.scalars().all()
            ]
        items.append(item)

    return PaginatedActivities(items=items, total=total, page=page, per_page=per_page)


@router.get("/{activity_id}", response_model=ActivityRead)
async def get_activity(
    activity_id: int,
    session: AsyncSession = Depends(get_db),
) -> ActivityRead:
    """Get a single activity with gym workout details if applicable."""
    result = await session.execute(
        select(Activity).where(
            Activity.id == activity_id,
            Activity.user_id == DEFAULT_USER_ID,
        )
    )
    activity = result.scalar_one_or_none()
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    item = ActivityRead.model_validate(activity)
    if activity.sport == "gym":
        details_result = await session.execute(
            select(GymWorkoutDetail)
            .where(GymWorkoutDetail.activity_id == activity.id)
            .order_by(GymWorkoutDetail.set_index)
        )
        item.gym_details = [
            GymWorkoutDetailRead.model_validate(d) for d in details_result.scalars().all()
        ]

    return item
