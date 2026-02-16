from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.user import User
from mycoach.schemas.user import UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/api/profile", tags=["profile"])

USER_ID = 1  # Single-user MVP


@router.get("", response_model=UserRead)
async def get_profile(db: AsyncSession = Depends(get_db)) -> UserRead:
    """Get the current user's profile."""
    result = await db.execute(select(User).where(User.id == USER_ID))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User profile not found. Create one first.")
    return UserRead.model_validate(user)


@router.post("", response_model=UserRead, status_code=201)
async def create_profile(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    """Create the user profile (single-user MVP)."""
    result = await db.execute(select(User).where(User.id == USER_ID))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Profile already exists")

    user = User(
        id=USER_ID,
        name=body.name,
        email=body.email,
        fitness_level=body.fitness_level,
        goals=body.goals,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserRead.model_validate(user)


@router.put("", response_model=UserRead)
async def update_profile(
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    """Update the user profile (partial update)."""
    result = await db.execute(select(User).where(User.id == USER_ID))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User profile not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return UserRead.model_validate(user)
