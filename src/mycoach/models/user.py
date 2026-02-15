from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    fitness_level: Mapped[str] = mapped_column(
        String(20), default="intermediate"
    )  # beginner, intermediate, advanced
    goals: Mapped[str | None] = mapped_column(Text, default=None)

    # Email preferences (per-type opt-in, all enabled by default)
    email_daily_briefing: Mapped[bool] = mapped_column(default=True)
    email_weekly_plan: Mapped[bool] = mapped_column(default=True)
    email_post_workout: Mapped[bool] = mapped_column(default=True)
    email_sleep_coaching: Mapped[bool] = mapped_column(default=True)
    email_weekly_recap: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
