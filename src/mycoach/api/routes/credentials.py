"""Credential management endpoints — store and delete encrypted credentials."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.config import get_settings
from mycoach.crypto import encrypt_credentials
from mycoach.database import get_db
from mycoach.models.data_source import DataSourceConfig
from mycoach.schemas.data_source import CredentialsInput, CredentialsStored

router = APIRouter(prefix="/api/credentials", tags=["credentials"])
logger = logging.getLogger(__name__)

DEFAULT_USER_ID = 1


def _require_encryption_key() -> str:
    """Return the encryption key or raise 503 if not configured."""
    key = get_settings().encryption_key
    if not key:
        raise HTTPException(
            status_code=503,
            detail="Encryption key not configured. Set MYCOACH_ENCRYPTION_KEY.",
        )
    return key


@router.put("/{source_type}", response_model=CredentialsStored)
async def store_credentials(
    source_type: str,
    body: CredentialsInput,
    session: AsyncSession = Depends(get_db),
) -> CredentialsStored:
    """Encrypt and store credentials for a data source.

    Creates the DataSourceConfig if it doesn't exist, or updates the
    encrypted credentials on the existing config.
    """
    key = _require_encryption_key()
    encrypted = encrypt_credentials(body.credentials, key)

    result = await session.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.user_id == DEFAULT_USER_ID,
            DataSourceConfig.source_type == source_type,
        )
    )
    config = result.scalar_one_or_none()

    if config is None:
        config = DataSourceConfig(
            user_id=DEFAULT_USER_ID,
            source_type=source_type,
            credentials_encrypted=encrypted,
        )
        session.add(config)
    else:
        config.credentials_encrypted = encrypted

    await session.commit()
    logger.info("Credentials stored for source_type=%s", source_type)

    return CredentialsStored(source_type=source_type)


@router.delete("/{source_type}")
async def delete_credentials(
    source_type: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Remove stored credentials for a data source."""
    result = await session.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.user_id == DEFAULT_USER_ID,
            DataSourceConfig.source_type == source_type,
        )
    )
    config = result.scalar_one_or_none()

    if config is None:
        raise HTTPException(status_code=404, detail=f"No config found for source '{source_type}'.")

    config.credentials_encrypted = None
    await session.commit()
    logger.info("Credentials deleted for source_type=%s", source_type)

    return {"message": f"Credentials removed for {source_type}."}


@router.get("/{source_type}/status")
async def credentials_status(
    source_type: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Check whether credentials are stored for a data source (without revealing them)."""
    result = await session.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.user_id == DEFAULT_USER_ID,
            DataSourceConfig.source_type == source_type,
        )
    )
    config = result.scalar_one_or_none()

    has_credentials = config is not None and config.credentials_encrypted is not None
    return {"source_type": source_type, "has_credentials": has_credentials}
