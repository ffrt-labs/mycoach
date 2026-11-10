from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import HEALTH_SCHEMA, Base


class SourceRecord(Base):
    """An immutable copy of what one source sent (CONTEXT.md: Source record).

    One shared table for every source, so a new source is new rows, never a new column or
    table (design #109). ``raw_payload`` carries everything source-specific: Garmin's raw
    activity, or the gym source's workout and its sets. Immutable: Postgres rejects an
    UPDATE or DELETE (migration ``h185a1b2c3d4``), and ``intake_source_record`` never rewrites one.
    """

    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("athlete_id", "source", "external_id", name="uq_source_records_identity"),
        {"schema": HEALTH_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int]
    source: Mapped[str] = mapped_column(String(20))  # garmin, hevy, logger
    external_id: Mapped[str] = mapped_column(String(100))
    sport: Mapped[str] = mapped_column(String(50))
    start_time: Mapped[datetime]
    end_time: Mapped[datetime | None] = mapped_column(default=None)
    title: Mapped[str] = mapped_column(String(200))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    received_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class CanonicalActivitySource(Base):
    """The reconciliation link: a source record contributing to a canonical activity.

    A join table (not a nullable FK on ``source_records``) so a canonical activity can hold
    more than one contributing source record. A source record contributes to exactly one.
    """

    __tablename__ = "canonical_activity_sources"
    __table_args__ = {"schema": HEALTH_SCHEMA}

    canonical_activity_id: Mapped[int] = mapped_column(
        ForeignKey(f"{HEALTH_SCHEMA}.canonical_activities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_record_id: Mapped[int] = mapped_column(
        ForeignKey(f"{HEALTH_SCHEMA}.source_records.id"), primary_key=True, unique=True
    )
