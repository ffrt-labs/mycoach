from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import COACHING_SCHEMA, Base


class InsightType(StrEnum):
    """The coaching insights the engine writes (``CoachingInsight.insight_type``)."""

    DAILY_BRIEFING = "daily_briefing"
    POST_WORKOUT = "post_workout"
    WEEKLY_RECAP = "weekly_recap"


class CoachingInsight(Base):
    __tablename__ = "coaching_insights"
    __table_args__ = {"schema": COACHING_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{COACHING_SCHEMA}.users.id"))
    insight_date: Mapped[date]
    insight_type: Mapped[str] = mapped_column(
        String(30)
    )  # daily_briefing, post_workout, sleep, weekly_recap
    content: Mapped[str] = mapped_column(Text)  # JSON structured output
    # Where the prompt came from: "lf:<n>" (Langfuse version n) or
    # "bundled:v2". Older rows hold a bare template version ("v1", "v2").
    prompt_version: Mapped[str] = mapped_column(String(20), default="v1")
    # The activity a post_workout insight is about, by its immutable external id (never an
    # internal health id, never a cross-schema key: tickets #108, #186).
    activity_external_id: Mapped[str | None] = mapped_column(String(100), default=None)
    # Insight revisions (CONTEXT.md): an immutable version of a coaching insight. A settled
    # activity that changes in a coaching-relevant way gets a new revision, never an overwrite.
    revision: Mapped[int] = mapped_column(default=1)
    # The deterministic health event id that produced this revision (idempotency key, #112).
    source_event_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class MesocycleConfig(Base):
    __tablename__ = "mesocycle_configs"
    __table_args__ = {"schema": COACHING_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{COACHING_SCHEMA}.users.id"))
    sport: Mapped[str] = mapped_column(String(50))
    block_length_weeks: Mapped[int] = mapped_column(default=4)
    current_week: Mapped[int] = mapped_column(default=1)
    phase: Mapped[str] = mapped_column(String(30), default="build")  # build, peak, deload
    start_date: Mapped[date]
    progression_rules: Mapped[str | None] = mapped_column(
        Text, default=None
    )  # JSON: e.g., {"weight_increment_kg": 2.5}
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
