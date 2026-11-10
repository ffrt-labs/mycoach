import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import HEALTH_SCHEMA, Base


class Activity(Base):
    """A Canonical activity (CONTEXT.md): the single observed activity for a session.

    Mapped to ``health.canonical_activities`` (map #85, ticket #185, design #109). The
    class keeps its pre-#185 name, as ``GymWorkoutDetail`` does, so the many callers do not
    churn; the table says what it is. Provenance is the set of source records linked
    through ``canonical_activity_sources``, never a value on this row.
    """

    __tablename__ = "canonical_activities"
    __table_args__ = (
        # The external id names the activity across the seam, so it is unique per athlete.
        Index(
            "uq_canonical_activities_athlete_external_id", "athlete_id", "external_id", unique=True
        ),
        {"schema": HEALTH_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The Athlete ID (health-owned, ``health.athletes``): "health issues its own
    # athlete_id" (ticket #108). No cross-schema FK to coaching.users. The
    # attribute keeps its pre-seam name so the health-side queries stay put until
    # the module boundary (#186) maps users.health_athlete_id; until then the
    # athlete id equals the coaching user id (one-athlete deployment).
    user_id: Mapped[int] = mapped_column("athlete_id")
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

    # Distance / pace from Garmin
    distance_meters: Mapped[float | None] = mapped_column(Float, default=None)
    avg_speed_mps: Mapped[float | None] = mapped_column(Float, default=None)

    # Training effect from Garmin
    training_effect_aerobic: Mapped[float | None] = mapped_column(Float, default=None)
    training_effect_anaerobic: Mapped[float | None] = mapped_column(Float, default=None)
    epoc: Mapped[float | None] = mapped_column(Float, default=None)
    recovery_time_minutes: Mapped[int | None] = mapped_column(default=None)
    avg_cadence: Mapped[int | None] = mapped_column(default=None)
    avg_swolf: Mapped[float | None] = mapped_column(Float, default=None)

    # Swimming-specific metrics
    moving_duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    fastest_split_100_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    avg_strokes_per_length: Mapped[float | None] = mapped_column(Float, default=None)

    # The source that created this activity (garmin, hevy, logger): a cache of the first
    # contributing source record, never "merged". Reconciliation is not a value any more;
    # provenance is the set of linked source records (``canonical_activity_sources``), and
    # the Garmin HR/training-effect columns above are a cache copied from the Garmin one.
    data_source: Mapped[str] = mapped_column(String(20))
    # The gym source record's external id (the logger's client UUID), carried forward: this
    # is what ``coaching.prescription_fulfillments.activity_external_id`` keys against.
    # A Garmin-only activity carries its Garmin id; a source with no native id, a derived one.
    # Never null in practice (ticket #186): intake carries the source's id forward, and a row
    # built any other way gets a fresh one, so coaching can always name the activity.
    external_id: Mapped[str] = mapped_column(
        String(100), default=lambda: uuid.uuid4().hex, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    # Settled activity (CONTEXT.md, #112): reconciliation completed or its deadline passed.
    # ``settlement_reason`` is "reconciled" or "deadline"; both null while unsettled.
    settled_at: Mapped[datetime | None] = mapped_column(default=None)
    settlement_reason: Mapped[str | None] = mapped_column(String(20), default=None)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class GymWorkoutDetail(Base):
    __tablename__ = "gym_workout_details"
    __table_args__ = {"schema": HEALTH_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    # A Performed set attaches to the canonical activity, not to a source record (#109).
    activity_id: Mapped[int] = mapped_column(
        "canonical_activity_id", ForeignKey(f"{HEALTH_SCHEMA}.canonical_activities.id")
    )
    # Null means a custom exercise with no catalogue match (CONTEXT.md).
    exercise_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{HEALTH_SCHEMA}.exercises.id"), default=None, index=True
    )
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
