"""Linking an actual activity to the prescription it answers.

The link is stored in exactly one place — ``PlannedSession.activity_id`` —
so there is no forward column on ``activities`` to drift out of step with it.

This lives at the package root, beside ``exercise_catalogue``, because both
ingestion (``sources.importer``) and coaching (``coaching.engine``) need it and
neither should import the other: the ingestion side has no business reaching
into the AI layer.

Two entry points, because the callers know different things. ``post_workout``
has already resolved a planned session and just records the link;
``import_workouts`` is handed an id by an offline phone and must decide whether
to trust it.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.activity import GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan

logger = logging.getLogger(__name__)


async def has_performed_sets(session: AsyncSession, activity_id: int) -> bool:
    """Whether the activity has at least one performed set.

    The claim guard: a prescription is only answered by an activity that shows
    work was done. Empty activities are still stored (source records are
    immutable) but never complete a prescription. Callers must have flushed the
    activity's sets first.
    """
    stmt = select(GymWorkoutDetail.id).where(GymWorkoutDetail.activity_id == activity_id).limit(1)
    return (await session.execute(stmt)).first() is not None


async def link_activity_to_planned_session(
    session: AsyncSession,
    activity_id: int,
    planned_session_id: int,
) -> None:
    """Mark a planned session as completed and link it to the actual activity.

    An activity with no performed sets is a logged no-op: the prescription
    stays open (see ``has_performed_sets``).
    """
    if not await has_performed_sets(session, activity_id):
        logger.warning(
            "Not linking activity %s to planned session %s: no performed sets",
            activity_id,
            planned_session_id,
        )
        return
    stmt = select(PlannedSession).where(PlannedSession.id == planned_session_id)
    result = await session.execute(stmt)
    planned = result.scalar_one_or_none()
    if planned is not None:
        planned.completed = True
        planned.activity_id = activity_id
        await session.flush()


async def claim_planned_session(
    session: AsyncSession,
    user_id: int,
    activity_id: int,
    planned_session_id: int,
) -> str | None:
    """Link an imported activity to the prescription it claims to answer.

    Returns ``None`` on success, or a human-readable reason the claim was
    declined. A declined claim is never an exception: the id arrives from a
    phone that may have been offline while MyCoach regenerated or superseded
    the plan underneath it, so a stale id is an expected outcome rather than a
    broken client. Refusing the import over it would cost a real training log
    to protect a bookkeeping link.

    (This is deliberately the opposite of ``exercise_id``, which rejects the
    whole batch in ``schemas.workout_import``. That id comes from a catalogue
    pinned at a commit and can only be wrong if the client is broken.)
    """
    stmt = (
        select(PlannedSession)
        .join(WeeklyPlan, PlannedSession.plan_id == WeeklyPlan.id)
        .where(PlannedSession.id == planned_session_id, WeeklyPlan.user_id == user_id)
    )
    planned = (await session.execute(stmt)).scalar_one_or_none()

    if planned is None:
        return (
            f"planned_session_id {planned_session_id} not found — "
            "workout imported without a prescription link"
        )

    if planned.activity_id is not None and planned.activity_id != activity_id:
        return (
            f"planned_session_id {planned_session_id} is already answered by "
            f"activity {planned.activity_id} — workout imported without a prescription link"
        )

    if not await has_performed_sets(session, activity_id):
        return (
            f"planned_session_id {planned_session_id} not claimed: activity has no "
            "performed sets — workout imported without a prescription link"
        )

    planned.completed = True
    planned.activity_id = activity_id
    await session.flush()
    return None
