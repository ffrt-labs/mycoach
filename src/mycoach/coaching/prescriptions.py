"""Prescription fulfillments: what the plan called for, per performed set.

Coaching-owned (``coaching.prescription_fulfillments``, map #85 ticket #108). The
performed set in the health schema carries no prescription; this module is how
coaching writes one at log time and reads it back, keyed by the performed set's
natural key ``(activity_external_id, exercise_title, set_index)``.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.exercise_catalogue import exercise_id_for_name
from mycoach.models.prescription import PrescriptionFulfillment
from mycoach.sources.workout_import import WorkoutSetImport


@dataclass(frozen=True)
class Prescription:
    weight_kg: float | None
    reps: int | None


NO_PRESCRIPTION = Prescription(weight_kg=None, reps=None)

SetKey = tuple[str, str, int]  # (activity_external_id, exercise_title, set_index)


async def record_fulfillments(
    session: AsyncSession, activity_external_id: str | None, sets: Iterable[WorkoutSetImport]
) -> None:
    """Write one fulfillment row per set that carries a prescription.

    Does not commit; idempotent on ``(activity_external_id, exercise_title, set_index)``. A workout
    with no external id cannot be keyed, so it records nothing (the logger always
    sends its client UUID; sources with no plan send no prescription).
    """
    if not activity_external_id:
        return
    prescribed = [
        s for s in sets if s.prescribed_weight_kg is not None or s.prescribed_reps is not None
    ]
    if not prescribed:
        return
    existing = set(
        (
            await session.execute(
                select(
                    PrescriptionFulfillment.exercise_title, PrescriptionFulfillment.set_index
                ).where(PrescriptionFulfillment.activity_external_id == activity_external_id)
            )
        ).tuples()
    )
    for s in prescribed:
        if (s.exercise_title, s.set_index) in existing:
            continue
        existing.add((s.exercise_title, s.set_index))
        session.add(
            PrescriptionFulfillment(
                activity_external_id=activity_external_id,
                exercise_title=s.exercise_title,
                set_index=s.set_index,
                exercise_id=s.exercise_id or exercise_id_for_name(s.exercise_title),
                prescribed_weight_kg=s.prescribed_weight_kg,
                prescribed_reps=s.prescribed_reps,
            )
        )


async def prescriptions_for(
    session: AsyncSession, activity_external_ids: Iterable[str | None]
) -> dict[SetKey, Prescription]:
    """The stored prescriptions of the given activities.

    Keyed by ``(external_id, exercise_title, set_index)``.
    """
    ids = {i for i in activity_external_ids if i}
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(PrescriptionFulfillment).where(
                PrescriptionFulfillment.activity_external_id.in_(ids)
            )
        )
    ).scalars()
    return {
        (r.activity_external_id, r.exercise_title, r.set_index): Prescription(
            r.prescribed_weight_kg, r.prescribed_reps
        )
        for r in rows
    }
