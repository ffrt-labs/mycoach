"""Health snapshot endpoints — today, by date, and trends.

Read through the health module: plain dicts, never the health schema (ticket #186).
"""

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.coaching.athlete import athlete_id_for
from mycoach.database import get_db
from mycoach.schemas.health import DailyHealthSnapshotRead

router = APIRouter(prefix="/api/health", tags=["health"])

# MVP: single user, id=1
DEFAULT_USER_ID = 1


@router.get("/today", response_model=DailyHealthSnapshotRead)
async def get_today_health(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get today's health snapshot."""
    snapshot = await health.get_snapshot(
        await athlete_id_for(session, DEFAULT_USER_ID), date.today()
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No health data for today")
    return snapshot


@router.get("/trends", response_model=list[DailyHealthSnapshotRead])
async def get_health_trends(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Get health snapshots for the last N days."""
    since = date.today() - timedelta(days=days)
    return await health.list_snapshots(await athlete_id_for(session, DEFAULT_USER_ID), since)


@router.get("/{snapshot_date}", response_model=DailyHealthSnapshotRead)
async def get_health_by_date(
    snapshot_date: date,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get health snapshot for a specific date."""
    snapshot = await health.get_snapshot(
        await athlete_id_for(session, DEFAULT_USER_ID), snapshot_date
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"No health data for {snapshot_date}")
    return snapshot
