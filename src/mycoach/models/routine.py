from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mycoach.database import COACHING_SCHEMA, Base


class WorkoutRoutine(Base):
    __tablename__ = "workout_routines"
    __table_args__ = {"schema": COACHING_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(f"{COACHING_SCHEMA}.users.id"))
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    days: Mapped[list["RoutineDay"]] = relationship(
        back_populates="routine", cascade="all, delete-orphan", order_by="RoutineDay.order_index"
    )


class RoutineDay(Base):
    __tablename__ = "routine_days"
    __table_args__ = {"schema": COACHING_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    routine_id: Mapped[int] = mapped_column(
        ForeignKey(f"{COACHING_SCHEMA}.workout_routines.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100))  # e.g. "Push Day"
    day_of_week: Mapped[int | None] = mapped_column(default=None)  # 0=Monday, 6=Sunday
    order_index: Mapped[int] = mapped_column(default=0)

    routine: Mapped["WorkoutRoutine"] = relationship(back_populates="days")
    exercises: Mapped[list["RoutineExercise"]] = relationship(
        back_populates="routine_day",
        cascade="all, delete-orphan",
        order_by="RoutineExercise.order_index",
    )


class RoutineExercise(Base):
    __tablename__ = "routine_exercises"
    __table_args__ = {"schema": COACHING_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    routine_day_id: Mapped[int] = mapped_column(
        ForeignKey(f"{COACHING_SCHEMA}.routine_days.id", ondelete="CASCADE")
    )
    exercise_id: Mapped[str | None] = mapped_column(String(200), default=None, index=True)
    exercise_name: Mapped[str] = mapped_column(String(200))
    sets: Mapped[int]
    rep_range: Mapped[str] = mapped_column(String(20))  # e.g. "8-10"
    order_index: Mapped[int] = mapped_column(default=0)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    superset_group: Mapped[int | None] = mapped_column(default=None)

    routine_day: Mapped["RoutineDay"] = relationship(back_populates="exercises")


ROUTINE_MAX_SETS = 10
"""More working sets of one exercise than this is a routine typo, not a prescription (#187)."""

ROUTINE_ERROR_PLANNED_SETS = 3
"""What the weekly plan stores in place of a set count over ``ROUTINE_MAX_SETS`` (#234)."""


def is_routine_error(sets: object) -> bool:
    return isinstance(sets, int) and not isinstance(sets, bool) and sets > ROUTINE_MAX_SETS


def planned_set_count(routine_sets: int) -> int:
    """The set count the plan stores for a routine exercise: a routine error is planned
    as ``ROUTINE_ERROR_PLANNED_SETS``, anything else as the routine's count."""
    return ROUTINE_ERROR_PLANNED_SETS if is_routine_error(routine_sets) else routine_sets


def planned_sets(routine_sets: int) -> dict[str, int]:
    """A planned exercise's ``sets`` from its routine's: a routine error is planned as
    ``ROUTINE_ERROR_PLANNED_SETS``, the routine's count kept as ``routine_sets`` so the
    plan email's Routine error notice can name it."""
    planned = {"sets": planned_set_count(routine_sets)}
    if is_routine_error(routine_sets):
        planned["routine_sets"] = routine_sets
    return planned
