from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class SportProfile(Base):
    __tablename__ = "sport_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    sport: Mapped[str] = mapped_column(String(50))  # gym, swimming, padel
    skill_level: Mapped[str] = mapped_column(
        String(20), default="intermediate"
    )  # beginner, intermediate, advanced
    goals: Mapped[str | None] = mapped_column(Text, default=None)
    preferences: Mapped[str | None] = mapped_column(
        Text, default=None
    )  # JSON string for sport-specific preferences
    benchmarks: Mapped[str | None] = mapped_column(
        Text, default=None
    )  # JSON string for PRs, test results, etc.
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )
