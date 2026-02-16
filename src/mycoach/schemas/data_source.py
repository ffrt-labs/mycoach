from datetime import datetime

from pydantic import BaseModel, Field


class DataSourceConfigBase(BaseModel):
    source_type: str = Field(max_length=50)
    enabled: bool = True


class DataSourceConfigCreate(DataSourceConfigBase):
    credentials_encrypted: str | None = None


class DataSourceConfigUpdate(BaseModel):
    enabled: bool | None = None
    credentials_encrypted: str | None = None


class DataSourceConfigRead(DataSourceConfigBase):
    id: int
    user_id: int
    last_sync_at: datetime | None = None
    sync_status: str
    sync_error: str | None = None
    created_at: datetime
    has_credentials: bool = False

    model_config = {"from_attributes": True}


class DataSourceStatus(BaseModel):
    source_type: str
    enabled: bool
    sync_status: str
    last_sync_at: datetime | None = None
    sync_error: str | None = None


class CredentialsInput(BaseModel):
    """Plaintext credentials to be encrypted before storage."""

    credentials: dict[str, str] = Field(
        ..., description="Key-value pairs of credentials (e.g. {'email': '...', 'password': '...'})"
    )


class CredentialsStored(BaseModel):
    """Response confirming credentials were stored."""

    source_type: str
    has_credentials: bool = True
    message: str = "Credentials encrypted and stored."
