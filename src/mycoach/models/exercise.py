from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import HEALTH_SCHEMA, Base


class Exercise(Base):
    """Health-owned exercise identity (map #85, ticket #185, design #109).

    A load-time mirror of the pinned catalogue (``exercise_catalogue``), which stays the
    source of truth: ``sync_exercises`` upserts it at startup. ``gym_workout_details`` points
    here through a nullable FK; NULL still means a custom exercise.
    """

    __tablename__ = "exercises"
    __table_args__ = {"schema": HEALTH_SCHEMA}

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
