"""Coaching's handlers for the observed-health events (map #85, ticket #112, ADR 0002).

Health raises ``CanonicalActivityRecorded`` / ``Settled`` / ``Revised`` as plain data from its
commands. The call site (an API route or a scheduler job) dispatches them here **after** the
health transaction has committed, so a handler failing can never roll a health write back.
There is no pub/sub registry before the broker: the call site is the broker.

Failure discipline (the in-process-call-vs-durable-event risk):

1. dispatch only after health's commit, never inside it;
2. every handler's failure is caught and logged on its own, so one cannot block a sibling or
   fail the request or job that triggered it;
3. nothing redelivers yet, so a failed handler is a genuine gap until the broker exists. The
   mitigation is that every handler is idempotent (a deterministic event id, an existing
   insight, an already-claimed session), so it is safe to re-invoke by hand against the same
   event once a failure shows in the logs.
"""

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.coaching.exceptions import PipelineSkip
from mycoach.health.events import (
    CanonicalActivityRecorded,
    CanonicalActivityRevised,
    CanonicalActivitySettled,
    HealthEvent,
)
from mycoach.plan_linking import claim_planned_session

logger = logging.getLogger(__name__)

# The activity fields the post-workout prompt actually reads (``_format_activity_detail``). A
# revision touching one of them can change the coaching; anything else (a title fix) cannot.
REVISION_FIELDS = frozenset(
    {
        "avg_hr",
        "max_hr",
        "calories",
        "training_effect_aerobic",
        "training_effect_anaerobic",
        "epoc",
        "recovery_time_minutes",
        "distance_meters",
        "duration_minutes",
    }
)

Handler = Callable[[Any], Awaitable[str | None]]
"""A handler takes its event and returns a human-readable note (or None)."""


@dataclass(frozen=True)
class HandlerFailure:
    event_id: str
    event_type: str
    handler: str
    error: str


@dataclass
class DispatchResult:
    failures: list[HandlerFailure] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


async def dispatch(
    events: Sequence[HealthEvent], handlers: Mapping[type, Sequence[Handler]]
) -> DispatchResult:
    """Run each event's handlers, isolating every failure. Never raises."""
    result = DispatchResult()
    for event in events:
        for handler in handlers.get(type(event), ()):
            name = getattr(handler, "__qualname__", repr(handler))
            try:
                note = await handler(event)
            except Exception as exc:  # noqa: BLE001 - isolation is the point (ADR 0002)
                logger.exception(
                    "Handler %s failed for %s %s", name, type(event).__name__, event.event_id
                )
                result.failures.append(
                    HandlerFailure(event.event_id, type(event).__name__, name, str(exc))
                )
                continue
            if note:
                result.notes.append(note)
    return result


def _session_factory(factory: Callable[[], AsyncSession] | None) -> Callable[[], AsyncSession]:
    if factory is not None:
        return factory
    from mycoach import database  # resolved at call time so tests can point it elsewhere

    return database.async_session


def claim_on_recorded(
    user_id: int,
    planned_session_id: int,
    *,
    session_factory: Callable[[], AsyncSession] | None = None,
) -> Handler:
    """``CanonicalActivityRecorded`` -> ``claim_planned_session``.

    The planned session id rode in on the workout request, which the call site knows and the
    event (an observed fact) does not carry. Idempotent: re-claiming the same pair is a no-op.
    A declined claim is returned as a note, never raised: the log is the precious thing.
    """

    async def on_recorded(event: CanonicalActivityRecorded) -> str | None:
        async with _session_factory(session_factory)() as session:
            declined = await claim_planned_session(
                session, user_id, event.activity_external_id, planned_session_id
            )
            await session.commit()
            return declined

    return on_recorded


Notify = Callable[[Any, str], Awaitable[None] | None]


def post_workout_on_settled(
    user_id: int,
    *,
    engine_factory: Callable[[], Any] | None = None,
    notify: Notify | None = None,
    session_factory: Callable[[], AsyncSession] | None = None,
) -> Handler:
    """``CanonicalActivitySettled`` -> final post-workout coaching.

    Settled is what tells coaching that final analysis is safe to generate. Idempotent: an
    activity that already has its analysis is skipped quietly. ``notify`` (the email) runs
    only for an analysis this call produced.
    """

    async def on_settled(event: CanonicalActivitySettled) -> str | None:
        if engine_factory is not None:
            engine = engine_factory()
        else:
            from mycoach.coaching.engine import CoachingEngine

            engine = CoachingEngine()
        async with _session_factory(session_factory)() as session:
            try:
                insight = await engine.generate_post_workout_analysis(
                    session, user_id, event.activity_external_id
                )
            except PipelineSkip:
                return None
        if notify is not None:
            pending = notify(insight, event.activity_external_id)
            if inspect.isawaitable(pending):
                await pending
        return None

    return on_settled


def post_workout_on_revised(
    user_id: int,
    *,
    engine_factory: Callable[[], Any] | None = None,
    session_factory: Callable[[], AsyncSession] | None = None,
) -> Handler:
    """``CanonicalActivityRevised`` -> a retained Insight revision, when it matters.

    Only when ``changed_fields`` intersects what the post-workout prompt reads. The event id is
    the revision's idempotency key, so handling the same event twice writes one revision.
    """

    async def on_revised(event: CanonicalActivityRevised) -> str | None:
        if not REVISION_FIELDS.intersection(event.changed_fields):
            return None
        if engine_factory is not None:
            engine = engine_factory()
        else:
            from mycoach.coaching.engine import CoachingEngine

            engine = CoachingEngine()
        async with _session_factory(session_factory)() as session:
            try:
                await engine.generate_post_workout_analysis(
                    session, user_id, event.activity_external_id, revision_event_id=event.event_id
                )
            except PipelineSkip:
                return None
        return None

    return on_revised

