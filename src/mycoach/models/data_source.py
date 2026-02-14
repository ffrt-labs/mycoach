from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class DataSourceConfig(Base):
    __tablename__ = "data_source_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    source_type: Mapped[str] = mapped_column(String(50))  # garmin, hevy_csv, strava, etc.
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(default=None)
    sync_status: Mapped[str] = mapped_column(String(20), default="never")  # never, ok, error
    sync_error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
