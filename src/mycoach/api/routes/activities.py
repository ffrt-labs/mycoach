"""Activity endpoints — list, detail, and post-workout analysis."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.coaching.athlete import athlete_id_for
from mycoach.coaching.engine import CoachingEngine
from mycoach.coaching.exceptions import PipelineSkip
from mycoach.database import get_db
from mycoach.models.coaching import CoachingInsight
from mycoach.schemas.activity import ActivityRead
from mycoach.schemas.coaching import CoachingInsightRead

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
    found = await health.list_activities(
        await athlete_id_for(session, DEFAULT_USER_ID), sport, page, per_page
    )
    return PaginatedActivities(
        items=[ActivityRead.model_validate(item) for item in found["items"]],
        total=found["total"],
        page=page,
        per_page=per_page,
    )


@router.get("/{activity_external_id}", response_model=ActivityRead)
async def get_activity(
    activity_external_id: str,
    session: AsyncSession = Depends(get_db),
) -> ActivityRead:
    """Get a single activity, named by its external id, with gym workout details if any."""
    activity = await health.get_activity(
        await athlete_id_for(session, DEFAULT_USER_ID), activity_external_id
    )
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return ActivityRead.model_validate(activity)


@router.get("/{activity_external_id}/analysis", response_model=CoachingInsightRead)
async def get_activity_analysis(
    activity_external_id: str,
    session: AsyncSession = Depends(get_db),
) -> CoachingInsight:
    """Get the post-workout analysis for an activity."""
    result = await session.execute(
        select(CoachingInsight)
        .where(
            CoachingInsight.activity_external_id == activity_external_id,
            CoachingInsight.user_id == DEFAULT_USER_ID,
            CoachingInsight.insight_type == "post_workout",
        )
        .order_by(CoachingInsight.revision.desc())
        .limit(1)
    )
    insight = result.scalar_one_or_none()
    if insight is None:
        raise HTTPException(
            status_code=404, detail="No post-workout analysis found for this activity"
        )
    return insight


@router.post("/{activity_external_id}/analyze", response_model=CoachingInsightRead)
async def analyze_activity(
    activity_external_id: str,
    force: bool = Query(default=False),
    session: AsyncSession = Depends(get_db),
) -> CoachingInsight:
    """Trigger post-workout analysis for a completed activity."""
    engine = CoachingEngine()
    try:
        insight = await engine.generate_post_workout_analysis(
            session, DEFAULT_USER_ID, activity_external_id, force=force
        )
    except (PipelineSkip, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    return insight
