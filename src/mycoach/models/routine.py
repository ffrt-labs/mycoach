from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mycoach.database import Base


class WorkoutRoutine(Base):
    __tablename__ = "workout_routines"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    days: Mapped[list["RoutineDay"]] = relationship(
        back_populates="routine", cascade="all, delete-orphan", order_by="RoutineDay.order_index"
    )


class RoutineDay(Base):
    __tablename__ = "routine_days"

    id: Mapped[int] = mapped_column(primary_key=True)
    routine_id: Mapped[int] = mapped_column(ForeignKey("workout_routines.id", ondelete="CASCADE"))
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

    id: Mapped[int] = mapped_column(primary_key=True)
    routine_day_id: Mapped[int] = mapped_column(ForeignKey("routine_days.id", ondelete="CASCADE"))
    exercise_name: Mapped[str] = mapped_column(String(200))
    sets: Mapped[int]
    rep_range: Mapped[str] = mapped_column(String(20))  # e.g. "8-10"
    order_index: Mapped[int] = mapped_column(default=0)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    superset_group: Mapped[int | None] = mapped_column(default=None)

    routine_day: Mapped["RoutineDay"] = relationship(back_populates="exercises")
