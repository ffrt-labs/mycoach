from datetime import datetime

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import Base


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    sport: Mapped[str] = mapped_column(String(50))  # gym, swimming, padel, cardio
    title: Mapped[str] = mapped_column(String(200))
    start_time: Mapped[datetime]
    end_time: Mapped[datetime | None] = mapped_column(default=None)
    duration_minutes: Mapped[int | None] = mapped_column(default=None)

    # HR data from Garmin
    avg_hr: Mapped[int | None] = mapped_column(default=None)
    max_hr: Mapped[int | None] = mapped_column(default=None)
    calories: Mapped[int | None] = mapped_column(default=None)
    hr_zones: Mapped[str | None] = mapped_column(Text, default=None)  # JSON

    # Training effect from Garmin
    training_effect_aerobic: Mapped[float | None] = mapped_column(Float, default=None)
    training_effect_anaerobic: Mapped[float | None] = mapped_column(Float, default=None)

    # Source tracking
    data_source: Mapped[str] = mapped_column(
        String(20)
    )  # garmin, hevy, merged
    garmin_activity_id: Mapped[str | None] = mapped_column(
        String(100), default=None, unique=True
    )
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class GymWorkoutDetail(Base):
    __tablename__ = "gym_workout_details"

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"))
    exercise_title: Mapped[str] = mapped_column(String(200))
    superset_id: Mapped[int | None] = mapped_column(default=None)
    exercise_notes: Mapped[str | None] = mapped_column(Text, default=None)
    set_index: Mapped[int]
    set_type: Mapped[str] = mapped_column(
        String(20), default="normal"
    )  # normal, warmup, dropset, failure
    weight_kg: Mapped[float | None] = mapped_column(Float, default=None)
    reps: Mapped[int | None] = mapped_column(default=None)
    distance_meters: Mapped[float | None] = mapped_column(Float, default=None)
    duration_seconds: Mapped[int | None] = mapped_column(default=None)
    rpe: Mapped[float | None] = mapped_column(Float, default=None)
