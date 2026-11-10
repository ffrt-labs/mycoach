"""The one shared intake every ingestion path funnels through (map #85, ticket #185, design #109).

Garmin sync, the logger push and the Hevy CSV import all end here: a source record is
recorded as received, immutable, and a canonical activity is built from it at once.
Idempotent on (athlete, source, external_id), so a replay is a no-op, and shaped so an n8n
workflow can later call it over HTTP (#106). Reconciling a Garmin record with a gym one
is ``sources.reconcile``'s job, not intake's.

Health side: this module must never import coaching.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.health.events import (
    CanonicalActivityRecorded,
    CanonicalActivitySettled,
    HealthEvent,
)
from mycoach.models.activity import Activity
from mycoach.models.source_record import CanonicalActivitySource, SourceRecord


@dataclass(frozen=True)
class IntakeResult:
    source_record_id: int
    canonical_activity_id: int
    created: bool
    """False when the (athlete, source, external_id) was already recorded: nothing changed."""
    events: list[HealthEvent] = field(default_factory=list)
    """What the intake raised, as data, for the call site to dispatch after the commit."""


async def intake_source_record(
    session: AsyncSession,
    *,
    athlete_id: int,
    source: str,
    external_id: str,
    raw_payload: dict[str, Any],
    canonical: Activity,
) -> IntakeResult:
    """Record one source record and its canonical activity, unless already recorded.

    ``canonical`` is the unattached canonical activity built from the payload; it is only
    added when the record is new. Does not commit: the caller owns the transaction.
    """
    existing = (
        await session.execute(
            select(SourceRecord.id).where(
                SourceRecord.athlete_id == athlete_id,
                SourceRecord.source == source,
                SourceRecord.external_id == external_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        activity_id = (
            await session.execute(
                select(CanonicalActivitySource.canonical_activity_id).where(
                    CanonicalActivitySource.source_record_id == existing
                )
            )
        ).scalar_one()
        return IntakeResult(existing, activity_id, created=False)

    record = SourceRecord(
        athlete_id=athlete_id,
        source=source,
        external_id=external_id,
        sport=canonical.sport,
        start_time=canonical.start_time,
        end_time=canonical.end_time,
        title=canonical.title,
        raw_payload=raw_payload,
    )
    canonical.user_id = athlete_id
    canonical.data_source = source
    # Every canonical activity has an external id (#186): its gym source record's id, or, for a
    # Garmin-only one, the Garmin id. Coaching keys everything it stores about an activity by it.
    canonical.external_id = canonical.external_id or external_id
    taken = (
        await session.execute(
            select(Activity.id).where(
                Activity.user_id == athlete_id, Activity.external_id == canonical.external_id
            )
        )
    ).first()
    if taken is not None:
        # Two sources may reuse one id (a source record is unique per source, not across
        # them); the canonical external id is unique per athlete, so qualify it.
        canonical.external_id = f"{source}:{canonical.external_id}"
    now = datetime.utcnow()
    events: list[HealthEvent] = [
        CanonicalActivityRecorded.build(
            athlete_id, canonical.external_id, canonical.sport, source, now
        )
    ]
    if source == "garmin":
        # No second source is expected for a Garmin-only activity: settled at creation (#112).
        canonical.settled_at = now
        canonical.settlement_reason = "reconciled"
        events.append(
            CanonicalActivitySettled.build(
                athlete_id, canonical.external_id, canonical.sport, now, "reconciled"
            )
        )
    session.add_all([record, canonical])
    await session.flush()
    session.add(
        CanonicalActivitySource(canonical_activity_id=canonical.id, source_record_id=record.id)
    )
    await session.flush()
    return IntakeResult(record.id, canonical.id, created=True, events=events)
