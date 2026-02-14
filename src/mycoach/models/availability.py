from datetime import date, time

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class WeeklyAvailability(Base):
    __tablename__ = "weekly_availabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    week_start: Mapped[date]  # Monday of the target week
    day_of_week: Mapped[int]  # 0=Monday, 6=Sunday
    start_time: Mapped[time]
    duration_minutes: Mapped[int]
    preferred_sport: Mapped[str] = mapped_column(String(50))  # gym, swimming, padel, rest
