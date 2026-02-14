from datetime import date, datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class CoachingInsight(Base):
    __tablename__ = "coaching_insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    insight_date: Mapped[date]
    insight_type: Mapped[str] = mapped_column(
        String(30)
    )  # daily_briefing, post_workout, sleep, weekly_recap
    content: Mapped[str] = mapped_column(Text)  # JSON structured output
    prompt_version: Mapped[str] = mapped_column(String(10), default="v1")
    activity_id: Mapped[int | None] = mapped_column(
        ForeignKey("activities.id"), default=None
    )  # For post_workout type
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class MesocycleConfig(Base):
    __tablename__ = "mesocycle_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
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
