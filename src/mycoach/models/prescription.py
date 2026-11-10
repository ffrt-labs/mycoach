from datetime import datetime

from sqlalchemy import Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mycoach.database import COACHING_SCHEMA, Base


class PrescriptionFulfillment(Base):
    """What the plan called for on one performed set (CONTEXT.md: Prescription fulfillment).

    Coaching-owned. The performed set itself lives in ``health.gym_workout_details``
    and carries no prescription: observed facts and prescriptions never share a
    row. The two sides meet only through the activity's external id and the set
    index, never a cross-schema FK (map #85, ticket #108). Written at log time,
    in the request that records the workout, while the plan and the performed
    sets are both in scope.
    """

    __tablename__ = "prescription_fulfillments"
    __table_args__ = (
        UniqueConstraint(
            "activity_external_id",
            "exercise_title",
            "set_index",
            name="uq_prescription_fulfillments_activity_exercise_set",
        ),
        {"schema": COACHING_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_external_id: Mapped[str] = mapped_column(String(100), index=True)
    # The logger numbers sets from 1 within each exercise, so the set's natural key
    # is (external id, exercise, set_index), not the (external id, set_index) pair
    # ticket #108 sketched. The title is what the performed row always carries.
    exercise_title: Mapped[str] = mapped_column(String(200))
    set_index: Mapped[int]
    exercise_id: Mapped[str | None] = mapped_column(String(200), default=None)
    prescribed_weight_kg: Mapped[float | None] = mapped_column(Float, default=None)
    prescribed_reps: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
