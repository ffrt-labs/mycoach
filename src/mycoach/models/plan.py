from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class Track(StrEnum):
    """Which track of the weekly plan a planned session belongs to."""

    GYM = "gym"  # the routine, adjusted per gym slot
    CARDIO = "cardio"  # designed from goals in one cardio-plan call


class WeeklyPlan(Base):
    __tablename__ = "weekly_plans"
    __table_args__ = (UniqueConstraint("user_id", "week_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    week_start: Mapped[date]  # Monday of the plan week
    mesocycle_week: Mapped[int | None] = mapped_column(default=None)  # Week within block
    mesocycle_phase: Mapped[str | None] = mapped_column(
        String(30), default=None
    )  # build, peak, deload
    prompt_version: Mapped[str] = mapped_column(String(10), default="v1")
    # Where the gym-adjustment and cardio-plan prompts came from: "lf:<n>" or
    # "bundled:v2". Null when that track had no slots, and for older plans.
    gym_prompt_version: Mapped[str | None] = mapped_column(String(20), default=None)
    cardio_prompt_version: Mapped[str | None] = mapped_column(String(20), default=None)
    status: Mapped[str] = mapped_column(
        String(20), default="active"
    )  # draft, active, completed, superseded
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    raw_llm_output: Mapped[str | None] = mapped_column(Text, default=None)
    # "declared" if the week's availability was set by the user, "default" if
    # materialised from the standing schedule. Null for plans generated before
    # this column existed.
    availability_source: Mapped[str | None] = mapped_column(String(20), default=None)
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
    track: Mapped[str] = mapped_column(String(20), default=Track.CARDIO.value)  # a Track
    activity_id: Mapped[int | None] = mapped_column(
        ForeignKey("activities.id"), default=None
    )  # Link to actual workout
