from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.database import get_db
from mycoach.models.user import User
from mycoach.schemas.user import EmailPreferencesRead, EmailPreferencesUpdate

router = APIRouter(prefix="/api/email-preferences", tags=["email-preferences"])

USER_ID = 1  # Single-user MVP


@router.get("", response_model=EmailPreferencesRead)
async def get_email_preferences(db: AsyncSession = Depends(get_db)) -> EmailPreferencesRead:
    result = await db.execute(select(User).where(User.id == USER_ID))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return EmailPreferencesRead.model_validate(user)


@router.patch("", response_model=EmailPreferencesRead)
async def update_email_preferences(
    body: EmailPreferencesUpdate,
    db: AsyncSession = Depends(get_db),
) -> EmailPreferencesRead:
    result = await db.execute(select(User).where(User.id == USER_ID))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return EmailPreferencesRead.model_validate(user)
