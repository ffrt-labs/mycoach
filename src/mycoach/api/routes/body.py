"""Body API: weight and waist readings and the athlete's height (ticket #198).

Body measurements are Observed health facts entered manually; the health side is their
source of truth. Writes go through health commands, reads through health queries (the
boundary of #186): this module never touches the health schema.
"""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.api.defaults import DEFAULT_USER_ID
from mycoach.coaching.athlete import athlete_id_for
from mycoach.config import get_settings
from mycoach.database import get_db
from mycoach.localtime import to_naive_utc
from mycoach.schemas.body import (
    BodyMeasurementCreate,
    BodyMeasurementRead,
    HeightRead,
    HeightUpdate,
)

router = APIRouter(prefix="/api/body", tags=["body"])


@router.post("/measurements", response_model=BodyMeasurementRead, status_code=201)
async def create_measurement(
    body: BodyMeasurementCreate,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Log a weight and/or waist reading.

    The reading is committed before anything talks to Garmin: the one-way weight push
    (#199) runs after the response, and a Garmin outage only leaves it for the sync's retry.
    """
    athlete_id = await athlete_id_for(session, DEFAULT_USER_ID)
    recorded = await health.record_body_measurement(
        athlete_id,
        measured_at=to_naive_utc(body.measured_at, get_settings().timezone),
        weight_kg=body.weight_kg,
        waist_cm=body.waist_cm,
        note=body.note.strip() if body.note else None,
    )
    if recorded.garmin_push_status == health.GarminPushStatus.PENDING:
        background.add_task(health.push_weight_to_garmin, athlete_id, recorded.id)
    return asdict(recorded)


@router.get("/measurements", response_model=list[BodyMeasurementRead])
async def list_measurements(session: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    """The last 10 readings, newest first."""
    return await health.list_body_measurements(
        await athlete_id_for(session, DEFAULT_USER_ID), limit=10
    )


@router.delete("/measurements/{measurement_id}", status_code=204)
async def delete_measurement(
    measurement_id: int, background: BackgroundTasks, session: AsyncSession = Depends(get_db)
) -> Response:
    """Delete a reading here, then its Garmin copy (in the background) when one is known."""
    deleted = await health.delete_body_measurement(
        await athlete_id_for(session, DEFAULT_USER_ID), measurement_id
    )
    if deleted is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    if deleted.garmin_weigh_in_id is not None:
        background.add_task(health.delete_weight_from_garmin, deleted)
    return Response(status_code=204, background=background)


@router.get("/height", response_model=HeightRead)
async def get_height(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    athlete_id = await athlete_id_for(session, DEFAULT_USER_ID)
    return {"height_cm": await health.get_athlete_height(athlete_id)}


@router.put("/height", response_model=HeightRead)
async def set_height(body: HeightUpdate, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    athlete_id = await athlete_id_for(session, DEFAULT_USER_ID)
    await health.set_athlete_height(athlete_id, body.height_cm)
    return {"height_cm": body.height_cm}
