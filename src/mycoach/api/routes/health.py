"""Health snapshot endpoints — today, by date, and trends."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.health import DailyHealthSnapshot
from mycoach.schemas.health import DailyHealthSnapshotRead

router = APIRouter(prefix="/api/health", tags=["health"])

# MVP: single user, id=1
DEFAULT_USER_ID = 1


@router.get("/today", response_model=DailyHealthSnapshotRead)
async def get_today_health(
    session: AsyncSession = Depends(get_db),
) -> DailyHealthSnapshot:
    """Get today's health snapshot."""
    today = date.today()
    result = await session.execute(
        select(DailyHealthSnapshot).where(
            DailyHealthSnapshot.user_id == DEFAULT_USER_ID,
            DailyHealthSnapshot.snapshot_date == today,
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No health data for today")
    return snapshot


@router.get("/trends", response_model=list[DailyHealthSnapshotRead])
async def get_health_trends(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_db),
) -> list[DailyHealthSnapshot]:
    """Get health snapshots for the last N days."""
    since = date.today() - timedelta(days=days)
    result = await session.execute(
        select(DailyHealthSnapshot)
        .where(
            DailyHealthSnapshot.user_id == DEFAULT_USER_ID,
            DailyHealthSnapshot.snapshot_date >= since,
        )
        .order_by(DailyHealthSnapshot.snapshot_date.desc())
    )
    return list(result.scalars().all())


@router.get("/{snapshot_date}", response_model=DailyHealthSnapshotRead)
async def get_health_by_date(
    snapshot_date: date,
    session: AsyncSession = Depends(get_db),
) -> DailyHealthSnapshot:
    """Get health snapshot for a specific date."""
    result = await session.execute(
        select(DailyHealthSnapshot).where(
            DailyHealthSnapshot.user_id == DEFAULT_USER_ID,
            DailyHealthSnapshot.snapshot_date == snapshot_date,
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No health data for {snapshot_date}")
    return snapshot
