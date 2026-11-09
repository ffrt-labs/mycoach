"""Langfuse scores: the ``ScoreSink`` port and its adapters.

A ``Score`` names the trace it belongs to and carries a deterministic ID,
``<trace id>:<score name>[:<qualifier>]``, so re-sending it (a re-link, a
re-import, a retried run) overwrites instead of duplicating.

Langfuse only overwrites when the ID *and the day of the timestamp* match, so
every ``Score`` takes an explicit ``timestamp``: derive it from MyCoach data
(the date the run is about, the plan's ``created_at``, the activity start),
never from "now" at send time.

Failure policy, the same as tracing: ``ScoreSink.emit`` never raises and logs
a failure at warn level. It is synchronous and does not block (the SDK queues
scores for a background thread), so it is safe to call from an SQLAlchemy
``after_commit`` hook.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sqlalchemy import event

from mycoach.observability.tracing import Tracer, get_tracer, langfuse_client

if TYPE_CHECKING:
    from langfuse import Langfuse
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session, SessionTransaction

logger = logging.getLogger(__name__)

DataType = Literal["NUMERIC", "BOOLEAN"]


@dataclass(frozen=True)
class Score:
    """One Langfuse score on a trace, optionally on one of its observations.

    ``qualifier`` tells apart scores of the same name on one trace (the
    operation, the gym slot, the planned session). ``observation_id`` needs
    the trace too, which every score has.
    """

    trace_id: str
    name: str
    value: float
    timestamp: datetime
    data_type: DataType = "NUMERIC"
    qualifier: str | None = None
    observation_id: str | None = None
    comment: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        base = f"{self.trace_id}:{self.name}"
        return base if self.qualifier is None else f"{base}:{self.qualifier}"

    @classmethod
    def boolean(
        cls,
        trace_id: str,
        name: str,
        value: bool,
        *,
        timestamp: datetime,
        qualifier: str | None = None,
        observation_id: str | None = None,
        comment: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Score:
        return cls(
            trace_id=trace_id,
            name=name,
            value=1.0 if value else 0.0,
            timestamp=timestamp,
            data_type="BOOLEAN",
            qualifier=qualifier,
            observation_id=observation_id,
            comment=comment,
            metadata=metadata or {},
        )

    @classmethod
    def numeric(
        cls,
        trace_id: str,
        name: str,
        value: float,
        *,
        timestamp: datetime,
        qualifier: str | None = None,
        observation_id: str | None = None,
        comment: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Score:
        return cls(
            trace_id=trace_id,
            name=name,
            value=float(value),
            timestamp=timestamp,
            data_type="NUMERIC",
            qualifier=qualifier,
            observation_id=observation_id,
            comment=comment,
            metadata=metadata or {},
        )


def score_timestamp(about: date, now: datetime) -> datetime:
    """A stable timestamp for the scores of a run about ``about``.

    Midnight UTC of ``about``, so a run retried on a later day (same trace,
    same score IDs) lands on the same day and overwrites. A run about a day
    still ahead (next week's plan) gets ``now`` instead, the time it ran:
    such a run has its own trace and is never re-scored, and a score dated
    in the future would sit outside the time ranges it belongs to.
    """
    midnight = datetime.combine(about, time(), tzinfo=UTC)
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    return min(midnight, now)


class ScoreSink(Protocol):
    def emit(self, score: Score) -> None:
        """Send ``score``. Never raises; a failure is logged at warn level."""
        ...


class NoopScoreSink:
    """Langfuse off: scores go nowhere."""

    def emit(self, score: Score) -> None:
        pass


class LangfuseScoreSink:
    """Sends scores through the SDK's score API."""

    def __init__(self, client: Langfuse) -> None:
        self._lf = client

    def emit(self, score: Score) -> None:
        try:
            self._lf.create_score(
                score_id=score.id,
                trace_id=score.trace_id,
                observation_id=score.observation_id,
                name=score.name,
                value=score.value,
                data_type=score.data_type,
                timestamp=score.timestamp,
                comment=score.comment,
                metadata=dict(score.metadata) or None,
            )
        except Exception:
            logger.warning(
                "Langfuse: sending the %s score failed; the coaching run continues",
                score.name,
                exc_info=True,
            )


def score_sink_for(tracer: Tracer) -> ScoreSink:
    """The sink that matches ``tracer``: Langfuse's when tracing is on, else a no-op.

    Scores share the tracer's client, so they go to the same Langfuse project
    and Langfuse being off disables both.
    """
    client = langfuse_client(tracer)
    return NoopScoreSink() if client is None else LangfuseScoreSink(client)


def get_score_sink() -> ScoreSink:
    """The process-wide sink, following the process-wide tracer."""
    return score_sink_for(get_tracer())


_PENDING = "mycoach_pending_scores"


def emit_after_commit(session: AsyncSession, sink: ScoreSink, scores: Iterable[Score]) -> None:
    """Hand ``scores`` to ``sink`` once ``session``'s transaction commits.

    For scores about data the transaction writes (a fulfilled prescription):
    a rollback discards them, so a link that never landed never posts a
    phantom score. They are held on the session itself and go on its next
    commit, whichever code path makes it.
    """
    sync_session = session.sync_session
    pending = sync_session.info.get(_PENDING)
    if pending is None:
        pending = sync_session.info[_PENDING] = []
        event.listen(sync_session, "after_commit", _send_pending)
        event.listen(sync_session, "after_soft_rollback", _drop_pending)
    pending.extend((sink, score) for score in scores)


def _send_pending(sync_session: Session) -> None:
    pending: list[tuple[ScoreSink, Score]] = sync_session.info.get(_PENDING) or []
    batch, pending[:] = list(pending), []
    for sink, score in batch:
        sink.emit(score)  # never raises: the sink guards itself


def _drop_pending(sync_session: Session, previous_transaction: SessionTransaction) -> None:
    pending = sync_session.info.get(_PENDING)
    if pending:
        pending.clear()
