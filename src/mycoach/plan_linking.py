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
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.activity import Activity, GymWorkoutDetail
from mycoach.models.plan import PlannedSession, WeeklyPlan
from mycoach.observability.adherence import adherence_scores
from mycoach.observability.scores import ScoreSink, emit_after_commit, get_score_sink

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
    *,
    scores: ScoreSink | None = None,
    clock: Clock = _utc_now,
) -> None:
    """Mark a planned session as completed and link it to the actual activity.

    An activity with no performed sets is a logged no-op: the prescription
    stays open (see ``has_performed_sets``) and no adherence is scored.
    ``scores`` defaults to the process-wide sink; ``clock`` stamps the
    adherence scores.
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
        await _fulfil(session, planned, activity_id, scores, clock)


async def claim_planned_session(
    session: AsyncSession,
    user_id: int,
    activity_id: int,
    planned_session_id: int,
    *,
    scores: ScoreSink | None = None,
    clock: Clock = _utc_now,
) -> str | None:
    """Link an imported activity to the prescription it claims to answer.

    Declines a target session already answered by a different activity, an
    activity that already answers a different session (one activity claims
    at most one prescription), and an activity with no performed sets.

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

    other_stmt = (
        select(PlannedSession.id)
        .join(WeeklyPlan, PlannedSession.plan_id == WeeklyPlan.id)
        .where(
            PlannedSession.activity_id == activity_id,
            PlannedSession.id != planned_session_id,
            WeeklyPlan.user_id == user_id,
        )
    )
    other_session_id = (await session.execute(other_stmt)).scalar_one_or_none()
    if other_session_id is not None:
        return (
            f"activity {activity_id} already claims planned_session_id {other_session_id} — "
            "workout imported without a prescription link"
        )

    if not await has_performed_sets(session, activity_id):
        return (
            f"planned_session_id {planned_session_id} not claimed: activity has no "
            "performed sets — workout imported without a prescription link"
        )

    await _fulfil(session, planned, activity_id, scores, clock)
    return None


async def _fulfil(
    session: AsyncSession,
    planned: PlannedSession,
    activity_id: int,
    scores: ScoreSink | None,
    clock: Clock,
) -> None:
    """Prescription fulfillment: the one place both entry points record the link.

    It also scores the fulfillment's adherence on the weekly plan's trace.
    The scores go to the sink only once the caller's transaction commits
    (``emit_after_commit``), so a rolled-back link never posts one.

    This emit point is a stand-in for a ``CanonicalActivitySettled`` handler
    (ADR 0002, docs/adr/0002-in-process-events-vocabulary-now-transport-later.md).
    When that event exists, the scoring below moves into its handler
    unchanged.
    """
    planned.completed = True
    planned.activity_id = activity_id
    await session.flush()

    plan = await session.get(WeeklyPlan, planned.plan_id)
    activity = await session.get(Activity, activity_id)
    if plan is None or activity is None:
        return
    sets_stmt = (
        select(GymWorkoutDetail)
        .where(GymWorkoutDetail.activity_id == activity_id)
        .order_by(GymWorkoutDetail.set_index)
    )
    sets = (await session.execute(sets_stmt)).scalars().all()
    emit_after_commit(
        session,
        scores or get_score_sink(),
        adherence_scores(
            plan=plan,
            planned=planned,
            activity=activity,
            sets=sets,
            gym_slot=await _gym_slot(session, planned),
            now=clock(),
        ),
    )


async def _gym_slot(session: AsyncSession, planned: PlannedSession) -> int | None:
    """The index of ``planned``'s gym slot in the plan run that made it.

    The weekly plan makes one gym-track planned session per gym slot, in slot
    order, so the slot index is the session's rank by id among its plan's gym
    sessions: the ``i`` of the run's ``gym_adjustment:<i>`` generation.

    This couples to ``CoachingEngine.generate_weekly_plan``'s insertion
    order. No stable key exists without a migration: two gym slots may share
    a ``day_of_week``, and ``PlannedSession`` does not store its slot. The
    contract is pinned by ``TestGymSlotContract`` in
    tests/test_observability/test_adherence_scores.py.
    """
    if planned.track != "gym":
        return None
    stmt = (
        select(PlannedSession.id)
        .where(PlannedSession.plan_id == planned.plan_id, PlannedSession.track == "gym")
        .order_by(PlannedSession.id)
    )
    ids = list((await session.execute(stmt)).scalars().all())
    return ids.index(planned.id) if planned.id in ids else None


async def unlink_planned_session(
    session: AsyncSession,
    user_id: int,
    planned_session_id: int,
) -> PlannedSession | None:
    """Release a prescription from the activity that claimed it.

    Clears the prescription side only (``activity_id``, ``completed``) so the
    prescription is to-do again; the activity itself is untouched. Deleting an
    activity is a health-side action, separate from unlink (#130).

    Returns the updated planned session, or ``None`` if it doesn't exist or
    doesn't belong to this user.
    """
    stmt = (
        select(PlannedSession)
        .join(WeeklyPlan, PlannedSession.plan_id == WeeklyPlan.id)
        .where(PlannedSession.id == planned_session_id, WeeklyPlan.user_id == user_id)
    )
    planned = (await session.execute(stmt)).scalar_one_or_none()
    if planned is None:
        return None

    planned.completed = False
    planned.activity_id = None
    await session.flush()
    return planned
