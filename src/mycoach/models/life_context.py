from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import COACHING_SCHEMA, Base


class EpisodeKind(StrEnum):
    """What a life context episode is about (``LifeContextEpisode.kind``)."""

    ILLNESS = "illness"
    TRAVEL = "travel"  # #221


class IllnessSeverity(StrEnum):
    """How ill: ``mild`` is above the neck, ``sick`` is below it (fever, chest, aches, GI)."""

    MILD = "mild"
    SICK = "sick"


class LifeContextEpisode(Base):
    """A stretch of the athlete's life that coaching must read around (CONTEXT.md).

    Coaching-owned, never a health fact: the athlete says it, no device measures it.
    One row per episode, of one ``kind``. The shared fields say when it ran;
    kind-specific fields are nullable columns that only their kind sets (illness:
    ``severity``). ``ended_on`` is the last affected day, null while open.
    Episodes may overlap: per day the most restrictive effect wins
    (``coaching.life_context``).
    """

    __tablename__ = "life_context_episodes"
    __table_args__ = {"schema": COACHING_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{COACHING_SCHEMA}.users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    started_on: Mapped[date]
    ended_on: Mapped[date | None] = mapped_column(default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    # illness only: an IllnessSeverity value
    severity: Mapped[str | None] = mapped_column(String(10), default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
