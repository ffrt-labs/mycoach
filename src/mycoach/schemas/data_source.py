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

    model_config = {"from_attributes": True}


class DataSourceStatus(BaseModel):
    source_type: str
    enabled: bool
    sync_status: str
    last_sync_at: datetime | None = None
    sync_error: str | None = None
