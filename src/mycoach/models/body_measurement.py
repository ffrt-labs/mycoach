from datetime import datetime

from sqlalchemy import CheckConstraint, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import HEALTH_SCHEMA, Base


class BodyMeasurement(Base):
    """A weight and/or waist reading the athlete entered (ticket #198).

    A Body measurement is an Observed health fact entered manually: the Body page is the
    only input and the health side is its source of truth, so a wearable change never
    loses it (docs/research/health-metrics.md §4). Garmin is a downstream copy of the
    weight (#199); its weigh-ins are never imported back. ``measured_at`` is UTC.
    """

    __tablename__ = "body_measurements"
    __table_args__ = (
        CheckConstraint(
            "weight_kg IS NOT NULL OR waist_cm IS NOT NULL",
            name="ck_body_measurements_weight_or_waist",
        ),
        Index("ix_body_measurements_athlete_measured_at", "athlete_id", "measured_at"),
        {"schema": HEALTH_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int]
    measured_at: Mapped[datetime]
    weight_kg: Mapped[float | None] = mapped_column(Float, default=None)
    waist_cm: Mapped[float | None] = mapped_column(Float, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # The one-way copy to Garmin Connect (#199). ``garmin_push_status`` is ``pending``,
    # ``pushed`` or ``failed`` for a weight reading and NULL for a waist-only one (never
    # pushed). ``garmin_weigh_in_id`` is Garmin's ``samplePk``, kept so a delete here
    # deletes there; ``garmin_push_attempts`` caps the retries.
    garmin_push_status: Mapped[str | None] = mapped_column(String(10), default=None)
    garmin_weigh_in_id: Mapped[str | None] = mapped_column(String(40), default=None)
    garmin_push_error: Mapped[str | None] = mapped_column(Text, default=None)
    garmin_push_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
