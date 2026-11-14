"""Body API: weight and waist readings and the athlete's height (ticket #198).

MyCoach owns body data. Writes go through health commands, reads through health queries
(the boundary of #186): this module never touches the health schema.
"""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.api.defaults import DEFAULT_USER_ID
from mycoach.coaching.athlete import athlete_id_for
from mycoach.config import get_settings
from mycoach.database import get_db
from mycoach.schemas.body import (
    BodyMeasurementCreate,
    BodyMeasurementRead,
    HeightRead,
    HeightUpdate,
)

router = APIRouter(prefix="/api/body", tags=["body"])


def _to_utc(measured_at: datetime | None) -> datetime:
    """Naive UTC for the health side. A naive input is local wall time in the configured
    timezone (what a datetime-local input sends)."""
    if measured_at is None:
        return datetime.now(UTC).replace(tzinfo=None)
    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=ZoneInfo(get_settings().timezone))
    return measured_at.astimezone(UTC).replace(tzinfo=None)


@router.post("/measurements", response_model=BodyMeasurementRead, status_code=201)
async def create_measurement(
    body: BodyMeasurementCreate, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Log a weight and/or waist reading."""
    recorded = await health.record_body_measurement(
        await athlete_id_for(session, DEFAULT_USER_ID),
        measured_at=_to_utc(body.measured_at),
        weight_kg=body.weight_kg,
        waist_cm=body.waist_cm,
        note=body.note.strip() if body.note else None,
    )
    return asdict(recorded)


@router.get("/measurements", response_model=list[BodyMeasurementRead])
async def list_measurements(session: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    """The last 10 readings, newest first."""
    return await health.list_body_measurements(
        await athlete_id_for(session, DEFAULT_USER_ID), limit=10
    )


@router.delete("/measurements/{measurement_id}", status_code=204)
async def delete_measurement(
    measurement_id: int, session: AsyncSession = Depends(get_db)
) -> Response:
    deleted = await health.delete_body_measurement(
        await athlete_id_for(session, DEFAULT_USER_ID), measurement_id
    )
    if deleted is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    return Response(status_code=204)


@router.get("/height", response_model=HeightRead)
async def get_height(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    athlete_id = await athlete_id_for(session, DEFAULT_USER_ID)
    return {"height_cm": await health.get_athlete_height(athlete_id)}


@router.put("/height", response_model=HeightRead)
async def set_height(body: HeightUpdate, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    athlete_id = await athlete_id_for(session, DEFAULT_USER_ID)
    await health.set_athlete_height(athlete_id, body.height_cm)
    return {"height_cm": body.height_cm}
