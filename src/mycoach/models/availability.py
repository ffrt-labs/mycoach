from datetime import date

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class WeeklyAvailability(Base):
    __tablename__ = "weekly_availabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    week_start: Mapped[date]  # Monday of the target week
    day_of_week: Mapped[int]  # 0=Monday, 6=Sunday
    # sport: one of gym/swimming/running/padel; nullable for backward compat
    sport: Mapped[str | None] = mapped_column(String(50), nullable=True)


class DefaultAvailability(Base):
    """The user's standing weekly schedule, used to seed a week with no declared rows."""

    __tablename__ = "default_availability"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    day_of_week: Mapped[int]  # 0=Monday, 6=Sunday
    sport: Mapped[str | None] = mapped_column(String(50), nullable=True)
