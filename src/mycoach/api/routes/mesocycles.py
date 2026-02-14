"""Mesocycle API routes — manage training block configurations per sport."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.coaching import MesocycleConfig
from mycoach.schemas.coaching import (
    MesocycleConfigCreate,
    MesocycleConfigRead,
    MesocycleConfigUpdate,
)

router = APIRouter(prefix="/api/mesocycles", tags=["mesocycles"])

USER_ID = 1  # Single-user MVP


@router.post("", response_model=MesocycleConfigRead, status_code=201)
async def create_mesocycle(
    body: MesocycleConfigCreate,
    session: AsyncSession = Depends(get_db),
) -> MesocycleConfig:
    """Create a mesocycle configuration for a sport.

    Only one active mesocycle per sport is allowed. If one already exists,
    returns 409 Conflict.
    """
    existing = await session.execute(
        select(MesocycleConfig).where(
            MesocycleConfig.user_id == USER_ID,
            MesocycleConfig.sport == body.sport,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Mesocycle already exists for sport '{body.sport}'. Update or delete it first.",
        )

    row = MesocycleConfig(
        user_id=USER_ID,
        sport=body.sport,
        block_length_weeks=body.block_length_weeks,
        current_week=body.current_week,
        phase=body.phase,
        start_date=body.start_date,
        progression_rules=body.progression_rules,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("", response_model=list[MesocycleConfigRead])
async def list_mesocycles(
    session: AsyncSession = Depends(get_db),
) -> list[MesocycleConfig]:
    """List all mesocycle configurations."""
    stmt = (
        select(MesocycleConfig)
        .where(MesocycleConfig.user_id == USER_ID)
        .order_by(MesocycleConfig.sport)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{sport}", response_model=MesocycleConfigRead)
async def get_mesocycle(
    sport: str,
    session: AsyncSession = Depends(get_db),
) -> MesocycleConfig:
    """Get mesocycle configuration for a specific sport."""
    stmt = select(MesocycleConfig).where(
        MesocycleConfig.user_id == USER_ID,
        MesocycleConfig.sport == sport,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No mesocycle for sport '{sport}'")
    return row


@router.put("/{sport}", response_model=MesocycleConfigRead)
async def update_mesocycle(
    sport: str,
    body: MesocycleConfigUpdate,
    session: AsyncSession = Depends(get_db),
) -> MesocycleConfig:
    """Update mesocycle configuration for a sport (partial update)."""
    stmt = select(MesocycleConfig).where(
        MesocycleConfig.user_id == USER_ID,
        MesocycleConfig.sport == sport,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No mesocycle for sport '{sport}'")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(row, field, value)
    row.updated_at = datetime.utcnow()

    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{sport}", status_code=204)
async def delete_mesocycle(
    sport: str,
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete mesocycle configuration for a sport."""
    stmt = select(MesocycleConfig).where(
        MesocycleConfig.user_id == USER_ID,
        MesocycleConfig.sport == sport,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No mesocycle for sport '{sport}'")

    await session.delete(row)
    await session.commit()
