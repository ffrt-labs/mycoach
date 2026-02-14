"""Data source management endpoints — sync triggers and Hevy CSV import."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.activity import Activity
from mycoach.models.health import DailyHealthSnapshot
from mycoach.schemas.data_source import DataSourceStatus
from mycoach.sources.garmin.source import GarminSource
from mycoach.sources.hevy.csv_parser import parse_hevy_csv
from mycoach.sources.hevy.mappers import import_hevy_workouts
from mycoach.sources.merger import merge_garmin_hevy

router = APIRouter(prefix="/api/sources", tags=["sources"])
logger = logging.getLogger(__name__)

# MVP: single user, id=1
DEFAULT_USER_ID = 1


class HevyImportResponse(BaseModel):
    activities_created: int
    activities_skipped: int
    activities_merged: int
    rows_parsed: int
    rows_skipped: int
    errors: list[str]


class GarminSyncResponse(BaseModel):
    activities_created: int
    activities_skipped: int
    activities_merged: int
    health_snapshots_created: int
    errors: list[str]


class MergeResponse(BaseModel):
    activities_merged: int
    errors: list[str]


@router.post("/import/hevy", response_model=HevyImportResponse)
async def import_hevy_csv(
    file: UploadFile,
    session: AsyncSession = Depends(get_db),
) -> HevyImportResponse:
    """Upload and import a Hevy CSV workout export.

    Parses the CSV, deduplicates against existing workouts, and creates
    Activity + GymWorkoutDetail records.
    """
    content = await file.read()
    csv_text = content.decode("utf-8-sig")  # handle BOM from some exports

    parse_result = parse_hevy_csv(csv_text)
    import_result = await import_hevy_workouts(session, DEFAULT_USER_ID, parse_result)

    # Auto-merge with any existing Garmin gym activities
    merge_result = await merge_garmin_hevy(session, DEFAULT_USER_ID)
    await session.commit()

    all_errors = list(import_result.errors or [])
    if merge_result.errors:
        all_errors.extend(merge_result.errors)

    return HevyImportResponse(
        activities_created=import_result.activities_created,
        activities_skipped=import_result.activities_skipped,
        activities_merged=merge_result.merged,
        rows_parsed=parse_result.rows_parsed,
        rows_skipped=parse_result.rows_skipped,
        errors=all_errors,
    )


@router.post("/sync/garmin", response_model=GarminSyncResponse)
async def sync_garmin(
    session: AsyncSession = Depends(get_db),
    days: int = Query(default=7, ge=1, le=90),
) -> GarminSyncResponse:
    """Trigger a manual Garmin Connect sync.

    Fetches health snapshots and activities for the last N days (default 7).
    Deduplicates against existing data.
    """
    source = GarminSource()

    if not await source.authenticate():
        raise HTTPException(
            status_code=503,
            detail="Failed to authenticate with Garmin Connect. Check credentials.",
        )

    since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    since = since - timedelta(days=days)

    result = await source.fetch_and_import(session, DEFAULT_USER_ID, since=since)

    # Auto-merge with any existing Hevy gym activities
    merge_result = await merge_garmin_hevy(session, DEFAULT_USER_ID)
    await session.commit()

    all_errors = list(result.errors or [])
    if merge_result.errors:
        all_errors.extend(merge_result.errors)

    return GarminSyncResponse(
        activities_created=result.activities_created,
        activities_skipped=result.activities_skipped,
        activities_merged=merge_result.merged,
        health_snapshots_created=result.health_snapshots_created,
        errors=all_errors,
    )


@router.post("/merge", response_model=MergeResponse)
async def merge_activities(
    session: AsyncSession = Depends(get_db),
) -> MergeResponse:
    """Manually trigger merging of Garmin + Hevy gym activities.

    Finds overlapping gym activities from both sources and merges them,
    keeping Hevy exercise details and adding Garmin HR/training data.
    """
    merge_result = await merge_garmin_hevy(session, DEFAULT_USER_ID)
    await session.commit()

    return MergeResponse(
        activities_merged=merge_result.merged,
        errors=merge_result.errors or [],
    )


class SourcesStatusResponse(BaseModel):
    sources: list[DataSourceStatus]


@router.get("/status", response_model=SourcesStatusResponse)
async def get_sources_status(
    session: AsyncSession = Depends(get_db),
) -> SourcesStatusResponse:
    """Get connection status of all data sources.

    Returns status for each known source type (garmin, hevy_csv) based on
    actual imported data in the database.
    """
    sources: list[DataSourceStatus] = []

    # Garmin status: check health snapshots and activities from garmin
    garmin_latest_health = await session.scalar(
        select(func.max(DailyHealthSnapshot.created_at)).where(
            DailyHealthSnapshot.user_id == DEFAULT_USER_ID,
            DailyHealthSnapshot.data_source == "garmin",
        )
    )
    garmin_latest_activity = await session.scalar(
        select(func.max(Activity.created_at)).where(
            Activity.user_id == DEFAULT_USER_ID,
            Activity.data_source.in_(["garmin", "merged"]),
        )
    )
    garmin_last_sync = max(
        filter(None, [garmin_latest_health, garmin_latest_activity]),
        default=None,
    )
    sources.append(
        DataSourceStatus(
            source_type="garmin",
            enabled=True,
            sync_status="ok" if garmin_last_sync else "never",
            last_sync_at=garmin_last_sync,
        )
    )

    # Hevy status: check activities from hevy
    hevy_last_import = await session.scalar(
        select(func.max(Activity.created_at)).where(
            Activity.user_id == DEFAULT_USER_ID,
            Activity.data_source.in_(["hevy", "merged"]),
        )
    )
    sources.append(
        DataSourceStatus(
            source_type="hevy_csv",
            enabled=True,
            sync_status="ok" if hevy_last_import else "never",
            last_sync_at=hevy_last_import,
        )
    )

    return SourcesStatusResponse(sources=sources)
