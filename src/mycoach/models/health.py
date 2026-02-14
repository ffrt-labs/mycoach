from datetime import date, datetime

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class DailyHealthSnapshot(Base):
    __tablename__ = "daily_health_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    snapshot_date: Mapped[date] = mapped_column(unique=True)

    # Heart rate
    resting_hr: Mapped[int | None] = mapped_column(default=None)
    max_hr: Mapped[int | None] = mapped_column(default=None)
    avg_hr: Mapped[int | None] = mapped_column(default=None)

    # HRV
    hrv_status: Mapped[float | None] = mapped_column(Float, default=None)  # ms
    hrv_7day_avg: Mapped[float | None] = mapped_column(Float, default=None)

    # Sleep
    sleep_duration_minutes: Mapped[int | None] = mapped_column(default=None)
    sleep_score: Mapped[int | None] = mapped_column(default=None)
    sleep_deep_minutes: Mapped[int | None] = mapped_column(default=None)
    sleep_light_minutes: Mapped[int | None] = mapped_column(default=None)
    sleep_rem_minutes: Mapped[int | None] = mapped_column(default=None)
    sleep_awake_minutes: Mapped[int | None] = mapped_column(default=None)

    # Body Battery & Stress
    body_battery_high: Mapped[int | None] = mapped_column(default=None)
    body_battery_low: Mapped[int | None] = mapped_column(default=None)
    avg_stress: Mapped[int | None] = mapped_column(default=None)

    # Training metrics
    training_readiness: Mapped[int | None] = mapped_column(default=None)
    training_load: Mapped[float | None] = mapped_column(Float, default=None)
    training_status: Mapped[str | None] = mapped_column(
        String(50), default=None
    )  # productive, maintaining, detraining, etc.
    vo2_max: Mapped[float | None] = mapped_column(Float, default=None)

    # Other
    steps: Mapped[int | None] = mapped_column(default=None)
    respiration_avg: Mapped[float | None] = mapped_column(Float, default=None)
    spo2_avg: Mapped[float | None] = mapped_column(Float, default=None)
    intensity_minutes: Mapped[int | None] = mapped_column(default=None)

    # Raw data for debugging
    raw_data: Mapped[str | None] = mapped_column(Text, default=None)  # JSON blob

    data_source: Mapped[str] = mapped_column(String(50), default="garmin")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
