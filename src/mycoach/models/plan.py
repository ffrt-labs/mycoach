from datetime import date, datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class WeeklyPlan(Base):
    __tablename__ = "weekly_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    week_start: Mapped[date]  # Monday of the plan week
    mesocycle_week: Mapped[int | None] = mapped_column(default=None)  # Week within block
    mesocycle_phase: Mapped[str | None] = mapped_column(
        String(30), default=None
    )  # build, peak, deload
    prompt_version: Mapped[str] = mapped_column(String(10), default="v1")
    status: Mapped[str] = mapped_column(
        String(20), default="active"
    )  # draft, active, completed, superseded
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    raw_llm_output: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class PlannedSession(Base):
    __tablename__ = "planned_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("weekly_plans.id"))
    day_of_week: Mapped[int]  # 0=Monday, 6=Sunday
    sport: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    duration_minutes: Mapped[int | None] = mapped_column(default=None)
    details: Mapped[str | None] = mapped_column(Text, default=None)  # JSON structured
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    completed: Mapped[bool] = mapped_column(default=False)
    activity_id: Mapped[int | None] = mapped_column(
        ForeignKey("activities.id"), default=None
    )  # Link to actual workout
