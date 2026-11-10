"""Data source management endpoints — sync triggers and Hevy CSV import."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.api.defaults import DEFAULT_USER_ID
from mycoach.coaching.athlete import athlete_id_for
from mycoach.coaching.workout_log import log_workouts
from mycoach.database import get_db
from mycoach.schemas.data_source import DataSourceStatus
from mycoach.schemas.workout_import import WorkoutImportBatch
from mycoach.sources.garmin.source import GarminSource
from mycoach.sources.hevy.csv_parser import parse_hevy_csv

router = APIRouter(prefix="/api/sources", tags=["sources"])
logger = logging.getLogger(__name__)


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
    health_snapshots_updated: int = 0
    errors: list[str]


class MergeResponse(BaseModel):
    activities_merged: int
    errors: list[str]


class WorkoutImportResponse(BaseModel):
    activities_created: int
    activities_skipped: int
    activities_merged: int
    errors: list[str]


@router.post(
    "/import/workouts",
    response_model=WorkoutImportResponse,
)
async def import_workouts_endpoint(
    batch: WorkoutImportBatch,
    session: AsyncSession = Depends(get_db),
) -> WorkoutImportResponse:
    """Universal workout push endpoint (API-key guarded).

    Accepts a batch of canonical workouts from any external source — the offline
    companion logger, scripts, iOS Shortcuts — deduplicates them, and auto-merges
    with any overlapping Garmin gym activities. Idempotent: re-posting the same
    workouts (matched by external_id, else title+start_time) creates nothing new.
    """
    import_result = await log_workouts(
        session, DEFAULT_USER_ID, batch.to_dataclasses(), source=batch.source
    )

    # Link Garmin onto gym canonical activities (non-destructive; #185).
    merge_result = await health.link_garmin(await athlete_id_for(session, DEFAULT_USER_ID))

    all_errors = list(import_result.errors or [])
    if merge_result.errors:
        all_errors.extend(merge_result.errors)

    return WorkoutImportResponse(
        activities_created=import_result.activities_created,
        activities_skipped=import_result.activities_skipped,
        activities_merged=merge_result.linked,
        errors=all_errors,
    )


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
    import_result = await log_workouts(
        session, DEFAULT_USER_ID, parse_result.workouts, source="hevy"
    )
    import_result.errors = list(parse_result.errors) + (import_result.errors or [])

    # Link Garmin onto gym canonical activities (non-destructive; #185).
    merge_result = await health.link_garmin(await athlete_id_for(session, DEFAULT_USER_ID))

    all_errors = list(import_result.errors or [])
    if merge_result.errors:
        all_errors.extend(merge_result.errors)

    return HevyImportResponse(
        activities_created=import_result.activities_created,
        activities_skipped=import_result.activities_skipped,
        activities_merged=merge_result.linked,
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

    # Fetch, import, link Garmin onto gym activities (non-destructive; #185) and settle
    # what is due, all on the health side. The events it raised are dispatched by the
    # scheduled sync; a manual sync leaves final coaching to the nightly catch-up scan.
    outcome = await health.sync_garmin(
        await athlete_id_for(session, DEFAULT_USER_ID), source, since=since
    )
    result, merge_result = outcome.result, outcome.link

    all_errors = list(result.errors or [])
    if merge_result.errors:
        all_errors.extend(merge_result.errors)

    return GarminSyncResponse(
        activities_created=result.activities_created,
        activities_skipped=result.activities_skipped,
        activities_merged=merge_result.linked,
        health_snapshots_created=result.health_snapshots_created,
        health_snapshots_updated=result.health_snapshots_updated,
        errors=all_errors,
    )


@router.post("/merge", response_model=MergeResponse)
async def merge_activities(
    session: AsyncSession = Depends(get_db),
) -> MergeResponse:
    """Manually link Garmin activities onto overlapping gym canonical activities.

    Non-destructive (#185): both source records are kept; the Garmin-only canonical
    activity is replaced by a link on the gym one. Re-running is a no-op.
    """
    result = await health.link_garmin(await athlete_id_for(session, DEFAULT_USER_ID))
    return MergeResponse(activities_merged=result.linked, errors=list(result.errors or []))


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

    synced = await health.get_last_sync(await athlete_id_for(session, DEFAULT_USER_ID))

    def _when(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    garmin_last_sync = _when(synced["garmin"])
    sources.append(
        DataSourceStatus(
            source_type="garmin",
            enabled=True,
            sync_status="ok" if garmin_last_sync else "never",
            last_sync_at=garmin_last_sync,
        )
    )
    hevy_last_import = _when(synced["hevy"])
    sources.append(
        DataSourceStatus(
            source_type="hevy_csv",
            enabled=True,
            sync_status="ok" if hevy_last_import else "never",
            last_sync_at=hevy_last_import,
        )
    )

    return SourcesStatusResponse(sources=sources)
