"""Coaching API routes — daily briefing, post-workout analysis, sleep coaching."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
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
