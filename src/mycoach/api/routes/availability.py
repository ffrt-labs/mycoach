"""Availability API routes — manage weekly training availability slots."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.availability import WeeklyAvailability
from mycoach.schemas.availability import (
    AvailabilitySlot,
    WeeklyAvailabilityCreate,
    WeeklyAvailabilityRead,
)

router = APIRouter(prefix="/api/availability", tags=["availability"])

USER_ID = 1  # Single-user MVP


def _next_monday(ref: date | None = None) -> date:
    """Return the Monday of the next week relative to `ref` (default: today)."""
    d = ref or date.today()
    days_ahead = 7 - d.weekday()  # weekday(): 0=Mon
    return d + timedelta(days=days_ahead)


@router.post("", response_model=list[WeeklyAvailabilityRead], status_code=201)
async def set_availability(
    body: WeeklyAvailabilityCreate,
    session: AsyncSession = Depends(get_db),
) -> list[WeeklyAvailability]:
    """Set weekly availability slots. Replaces any existing slots for that week."""
    # Validate week_start is a Monday
    if body.week_start.weekday() != 0:
        raise HTTPException(status_code=422, detail="week_start must be a Monday")

    # Delete existing slots for this week
    await session.execute(
        delete(WeeklyAvailability).where(
            WeeklyAvailability.user_id == USER_ID,
            WeeklyAvailability.week_start == body.week_start,
        )
    )

    rows = []
    for slot in body.slots:
        row = WeeklyAvailability(
            user_id=USER_ID,
            week_start=body.week_start,
            day_of_week=slot.day_of_week,
            start_time=slot.start_time,
            duration_minutes=slot.duration_minutes,
            preferred_sport=slot.preferred_sport,
        )
        session.add(row)
        rows.append(row)

    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows


@router.get("/next-week", response_model=list[WeeklyAvailabilityRead])
async def get_next_week_availability(
    session: AsyncSession = Depends(get_db),
) -> list[WeeklyAvailability]:
    """Get availability slots for the upcoming week (next Monday onwards)."""
    monday = _next_monday()
    stmt = (
        select(WeeklyAvailability)
        .where(
            WeeklyAvailability.user_id == USER_ID,
            WeeklyAvailability.week_start == monday,
        )
        .order_by(WeeklyAvailability.day_of_week)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{week_start}", response_model=list[WeeklyAvailabilityRead])
async def get_week_availability(
    week_start: date,
    session: AsyncSession = Depends(get_db),
) -> list[WeeklyAvailability]:
    """Get availability slots for a specific week (by Monday date)."""
    if week_start.weekday() != 0:
        raise HTTPException(status_code=422, detail="week_start must be a Monday")
    stmt = (
        select(WeeklyAvailability)
        .where(
            WeeklyAvailability.user_id == USER_ID,
            WeeklyAvailability.week_start == week_start,
        )
        .order_by(WeeklyAvailability.day_of_week)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.put("/{slot_id}", response_model=WeeklyAvailabilityRead)
async def update_slot(
    slot_id: int,
    body: AvailabilitySlot,
    session: AsyncSession = Depends(get_db),
) -> WeeklyAvailability:
    """Update a single availability slot."""
    stmt = select(WeeklyAvailability).where(
        WeeklyAvailability.id == slot_id,
        WeeklyAvailability.user_id == USER_ID,
    )
    result = await session.execute(stmt)
    slot = result.scalar_one_or_none()
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot not found")

    slot.day_of_week = body.day_of_week
    slot.start_time = body.start_time
    slot.duration_minutes = body.duration_minutes
    slot.preferred_sport = body.preferred_sport
    await session.commit()
    await session.refresh(slot)
    return slot


@router.delete("/{slot_id}", status_code=204)
async def delete_slot(
    slot_id: int,
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a single availability slot."""
    stmt = select(WeeklyAvailability).where(
        WeeklyAvailability.id == slot_id,
        WeeklyAvailability.user_id == USER_ID,
    )
    result = await session.execute(stmt)
    slot = result.scalar_one_or_none()
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot not found")

    await session.delete(slot)
    await session.commit()
