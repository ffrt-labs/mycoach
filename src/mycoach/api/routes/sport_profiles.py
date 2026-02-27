"""Sport profile API routes — manage per-sport skill levels, goals, and benchmarks."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.sport_profile import SportProfile
from mycoach.schemas.sport_profile import (
    SportProfileCreate,
    SportProfileRead,
    SportProfileUpdate,
)

router = APIRouter(prefix="/api/sport-profiles", tags=["sport-profiles"])

USER_ID = 1  # Single-user MVP


@router.post("", response_model=SportProfileRead, status_code=201)
async def create_sport_profile(
    body: SportProfileCreate,
    session: AsyncSession = Depends(get_db),
) -> SportProfile:
    """Create a sport profile.

    Only one profile per sport is allowed. Returns 409 if one already exists.
    """
    existing = await session.execute(
        select(SportProfile).where(
            SportProfile.user_id == USER_ID,
            SportProfile.sport == body.sport,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Sport profile already exists for '{body.sport}'. Update or delete it first.",
        )

    row = SportProfile(
        user_id=USER_ID,
        sport=body.sport,
        skill_level=body.skill_level,
        goals=body.goals,
        preferences=body.preferences,
        benchmarks=body.benchmarks,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("", response_model=list[SportProfileRead])
async def list_sport_profiles(
    session: AsyncSession = Depends(get_db),
) -> list[SportProfile]:
    """List all sport profiles."""
    stmt = select(SportProfile).where(SportProfile.user_id == USER_ID).order_by(SportProfile.sport)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{sport}", response_model=SportProfileRead)
async def get_sport_profile(
    sport: str,
    session: AsyncSession = Depends(get_db),
) -> SportProfile:
    """Get sport profile for a specific sport."""
    stmt = select(SportProfile).where(
        SportProfile.user_id == USER_ID,
        SportProfile.sport == sport,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No sport profile for '{sport}'")
    return row


@router.put("/{sport}", response_model=SportProfileRead)
async def update_sport_profile(
    sport: str,
    body: SportProfileUpdate,
    session: AsyncSession = Depends(get_db),
) -> SportProfile:
    """Update sport profile for a sport (partial update)."""
    stmt = select(SportProfile).where(
        SportProfile.user_id == USER_ID,
        SportProfile.sport == sport,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No sport profile for '{sport}'")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(row, field, value)
    row.updated_at = datetime.utcnow()

    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{sport}", status_code=204)
async def delete_sport_profile(
    sport: str,
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete sport profile for a sport."""
    stmt = select(SportProfile).where(
        SportProfile.user_id == USER_ID,
        SportProfile.sport == sport,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No sport profile for '{sport}'")

    await session.delete(row)
    await session.commit()
