"""Logging workouts: health records the facts, coaching records what the plan called for.

One call site for the Logger push, the universal import endpoint and the Hevy CSV upload
(map #85, tickets #111, #186). The order is the design:

1. ``health.record_workouts``: one idempotent call, its own transaction, committed;
2. ``record_prescription_fulfillment``: coaching's rows, keyed by the activity's *external*
   id and set, in coaching's own transaction (never a cross-store foreign key);
3. dispatch the ``CanonicalActivityRecorded`` events health returned, after both commits:
   the planned-session claim runs as a handler, isolated from the log it follows.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from mycoach import health
from mycoach.coaching.athlete import athlete_id_for
from mycoach.coaching.health_events import claim_on_recorded, dispatch
from mycoach.coaching.prescriptions import record_fulfillments
from mycoach.health.events import CanonicalActivityRecorded
from mycoach.sources.base import ImportResult
from mycoach.sources.workout_import import WorkoutImport

logger = logging.getLogger(__name__)


async def log_workouts(
    db: AsyncSession, user_id: int, workouts: list[WorkoutImport], source: str
) -> ImportResult:
    """Record workouts with health, then their prescriptions and plan claims with coaching.

    A workout carrying a ``planned_session_id`` is linked to that prescription once it is
    recorded. Deduplicated workouts are not re-linked: the first import already did it, so a
    re-post stays a no-op. A claim that cannot be honoured is reported in ``errors`` and the
    workout still lands.
    """
    athlete_id = await athlete_id_for(db, user_id)
    recorded = await health.record_workouts(athlete_id, source, workouts)
    result = recorded.result

    created = {
        e.activity_external_id for e in recorded.events if isinstance(e, CanonicalActivityRecorded)
    }
    planned_by_external_id: dict[str, int] = {}
    for workout, external_id in zip(workouts, result.external_ids, strict=True):
        if external_id not in created:
            continue
        await record_fulfillments(db, external_id, workout.sets)
        if workout.planned_session_id is not None:
            planned_by_external_id[external_id] = workout.planned_session_id
    await db.commit()

    notes: list[str] = []
    for event in recorded.events:
        if not isinstance(event, CanonicalActivityRecorded):
            continue
        planned_session_id = planned_by_external_id.get(event.activity_external_id)
        if planned_session_id is None:
            continue
        outcome = await dispatch(
            [event], {CanonicalActivityRecorded: [claim_on_recorded(user_id, planned_session_id)]}
        )
        notes.extend(outcome.notes)
        notes.extend(f"claim handler failed: {f.error}" for f in outcome.failures)
    if notes:
        result.errors = (result.errors or []) + notes
    return result
