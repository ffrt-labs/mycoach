from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import HEALTH_SCHEMA, Base


class Athlete(Base):
    """The athlete the health side records observed facts about.

    The health schema issues the Athlete ID (map #85, ticket #108); coaching maps
    its own user to it through ``users.health_athlete_id``. One row today, but
    every health row stays athlete-aware for the eventual multi-athlete split.
    """

    __tablename__ = "athletes"
    __table_args__ = {"schema": HEALTH_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
