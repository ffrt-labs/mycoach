"""Coaching API routes — daily briefing, post-workout analysis, sleep coaching, weekly recap."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.engine import CoachingEngine
from mycoach.database import get_db
from mycoach.models.coaching import CoachingInsight
from mycoach.schemas.coaching import CoachingInsightRead

router = APIRouter(prefix="/api/coaching", tags=["coaching"])

USER_ID = 1  # Single-user MVP


@router.get("/today", response_model=CoachingInsightRead)
async def get_today_briefing(
    session: AsyncSession = Depends(get_db),
) -> CoachingInsight:
    """Get today's daily coaching briefing.

    Returns the existing briefing if already generated today.
    """
    today = date.today()
    stmt = select(CoachingInsight).where(
        CoachingInsight.user_id == USER_ID,
        CoachingInsight.insight_date == today,
        CoachingInsight.insight_type == "daily_briefing",
    )
    result = await session.execute(stmt)
    insight = result.scalar_one_or_none()
    if insight is None:
        raise HTTPException(
            status_code=404, detail="No daily briefing for today. Generate one first."
        )
    return insight


@router.post("/today/generate", response_model=CoachingInsightRead)
async def generate_today_briefing(
    session: AsyncSession = Depends(get_db),
) -> CoachingInsight:
    """Generate today's daily coaching briefing.

    Gathers health + activity data, calls the LLM, and stores the result.
    Returns 409 if a briefing already exists for today.
    """
    engine = CoachingEngine()
    try:
        insight = await engine.generate_daily_briefing(session, USER_ID)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    return insight


@router.get("/sleep", response_model=CoachingInsightRead)
async def get_sleep_coaching(
    session: AsyncSession = Depends(get_db),
) -> CoachingInsight:
    """Get today's sleep coaching analysis.

    Returns the existing sleep coaching if already generated today.
    """
    today = date.today()
    stmt = select(CoachingInsight).where(
        CoachingInsight.user_id == USER_ID,
        CoachingInsight.insight_date == today,
        CoachingInsight.insight_type == "sleep",
    )
    result = await session.execute(stmt)
    insight = result.scalar_one_or_none()
    if insight is None:
        raise HTTPException(
            status_code=404, detail="No sleep coaching for today. Generate one first."
        )
    return insight


@router.post("/sleep/generate", response_model=CoachingInsightRead)
async def generate_sleep_coaching(
    session: AsyncSession = Depends(get_db),
) -> CoachingInsight:
    """Generate sleep coaching analysis based on 14-day sleep trends.

    Analyzes sleep patterns, consistency, architecture, and provides
    personalized recommendations including bedtime and hygiene tips.
    Returns 409 if sleep coaching already exists for today.
    """
    engine = CoachingEngine()
    try:
        insight = await engine.generate_sleep_coaching(session, USER_ID)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    return insight


@router.get("/weekly-recap", response_model=CoachingInsightRead)
async def get_weekly_recap(
    week_start: date | None = Query(
        default=None, description="Monday of the week (defaults to last week)"
    ),
    session: AsyncSession = Depends(get_db),
) -> CoachingInsight:
    """Get the weekly recap for a given week.

    Defaults to last week (most recent completed Monday) if week_start is not provided.
    """
    if week_start is None:
        today = date.today()
        # Last Monday = this Monday minus 7 days
        week_start = today - timedelta(days=today.weekday() + 7)
    if week_start.weekday() != 0:
        raise HTTPException(status_code=422, detail="week_start must be a Monday")
    stmt = select(CoachingInsight).where(
        CoachingInsight.user_id == USER_ID,
        CoachingInsight.insight_date == week_start,
        CoachingInsight.insight_type == "weekly_recap",
    )
    result = await session.execute(stmt)
    insight = result.scalar_one_or_none()
    if insight is None:
        raise HTTPException(
            status_code=404, detail="No weekly recap for this week. Generate one first."
        )
    return insight


@router.post("/weekly-recap/generate", response_model=CoachingInsightRead)
async def generate_weekly_recap(
    week_start: date = Query(description="Monday of the week to recap"),
    session: AsyncSession = Depends(get_db),
) -> CoachingInsight:
    """Generate a weekly training recap.

    Analyzes plan adherence, activities, health trends, and mesocycle progress.
    Returns 409 if a recap already exists for the given week.
    """
    engine = CoachingEngine()
    try:
        insight = await engine.generate_weekly_recap(session, USER_ID, week_start)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    return insight
