"""Reconcile a Garmin source record with a gym one: canonical-activity linking.

Replaces ``merger.py``'s delete-and-overwrite (map #85, ticket #185; designs #109/#110).
A gym source (Hevy CSV or the offline logger) is the truth for the performed sets; Garmin
supplies the HR/calorie/training-effect overlay. When a Garmin-only canonical activity
overlaps a gym one in time:

1. its join row moves onto the gym canonical activity, so that activity now has both
   source records;
2. the Garmin HR/training-effect columns are copied onto the gym activity (a cache; the
   Garmin source record's ``raw_payload`` stays the source of truth);
3. the now-redundant Garmin-only canonical activity is hard-deleted. Nothing depends on its
   id (no events, no external consumers, never a prescription key). Neither source record
   is ever touched, and the activity's ``data_source`` is not rewritten.

Idempotent: only a Garmin-only activity and a gym activity with no Garmin record are
candidates, so a second run finds nothing. Health side: never imports coaching.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.health.events import CanonicalActivityRevised, CanonicalActivitySettled, HealthEvent
from mycoach.models.activity import Activity
from mycoach.models.source_record import CanonicalActivitySource, SourceRecord

logger = logging.getLogger(__name__)

# Two activities overlap if one starts within this window of the other's time range.
OVERLAP_TOLERANCE = timedelta(minutes=30)

# Garmin-measured columns copied onto the gym canonical activity when it has none.
_GARMIN_CACHE_COLUMNS = (
    "avg_hr",
    "max_hr",
    "calories",
    "hr_zones",
    "training_effect_aerobic",
    "training_effect_anaerobic",
    "epoc",
    "recovery_time_minutes",
    "duration_minutes",
)


@dataclass
class LinkResult:
    linked: int = 0
    errors: list[str] | None = None
    events: list[HealthEvent] = field(default_factory=list)
    """Settled (a gym activity just reconciled) and Revised (late Garmin data on a settled one)."""

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def _has_source(*conditions: Any) -> Any:
    return exists().where(
        CanonicalActivitySource.canonical_activity_id == Activity.id,
        CanonicalActivitySource.source_record_id == SourceRecord.id,
        *conditions,
    )


async def link_garmin_to_gym(session: AsyncSession, athlete_id: int) -> LinkResult:
    """Link overlapping Garmin-only gym activities onto their gym canonical activities."""
    result = LinkResult()

    gym_activities = list(
        (
            await session.execute(
                select(Activity).where(
                    Activity.user_id == athlete_id,
                    Activity.sport == "gym",
                    _has_source(SourceRecord.source != "garmin"),
                    ~_has_source(SourceRecord.source == "garmin"),
                )
            )
        )
        .scalars()
        .all()
    )
    if not gym_activities:
        return result

    garmin_activities = list(
        (
            await session.execute(
                select(Activity).where(
                    Activity.user_id == athlete_id,
                    Activity.sport == "gym",
                    _has_source(SourceRecord.source == "garmin"),
                    ~_has_source(SourceRecord.source != "garmin"),
                )
            )
        )
        .scalars()
        .all()
    )
    if not garmin_activities:
        return result

    matched: set[int] = set()
    for gym in gym_activities:
        match = _find_overlapping_garmin(gym, garmin_activities, matched)
        if match is None:
            continue
        matched.add(match.id)

        changed: list[str] = []
        for column in _GARMIN_CACHE_COLUMNS:
            if getattr(gym, column) is None and getattr(match, column) is not None:
                setattr(gym, column, getattr(match, column))
                changed.append(column)
        now = datetime.utcnow()
        if gym.settled_at is None:
            # Reconciliation completed: the expected second source arrived (#112).
            gym.settled_at = now
            gym.settlement_reason = "reconciled"
            if gym.external_id:
                result.events.append(
                    CanonicalActivitySettled.build(
                        athlete_id, gym.external_id, gym.sport, now, "reconciled"
                    )
                )
        elif changed and gym.external_id:
            result.events.append(
                CanonicalActivityRevised.build(athlete_id, gym.external_id, gym.sport, now, changed)
            )
        await session.flush()

        garmin_id, gym_id = match.id, gym.id
        await session.execute(
            update(CanonicalActivitySource)
            .where(CanonicalActivitySource.canonical_activity_id == garmin_id)
            .values(canonical_activity_id=gym_id)
        )
        await session.execute(delete(Activity).where(Activity.id == garmin_id))
        session.expunge(match)

        result.linked += 1
        logger.info(
            "Linked Garmin canonical activity %s onto %s ('%s' at %s)",
            garmin_id,
            gym_id,
            gym.title,
            gym.start_time,
        )

    await session.flush()
    return result


def _find_overlapping_garmin(
    gym: Activity,
    garmin_activities: list[Activity],
    already_matched: set[int],
) -> Activity | None:
    """The Garmin activity overlapping ``gym`` the most, within the tolerance."""
    best_match: Activity | None = None
    best_overlap: float = 0.0

    gym_start = gym.start_time
    gym_end = gym.end_time or (
        gym_start + timedelta(minutes=gym.duration_minutes)
        if gym.duration_minutes
        else gym_start + OVERLAP_TOLERANCE
    )

    for garmin in garmin_activities:
        if garmin.id in already_matched:
            continue

        garmin_start = garmin.start_time
        garmin_end = garmin.end_time or (
            garmin_start + timedelta(minutes=garmin.duration_minutes)
            if garmin.duration_minutes
            else garmin_start + OVERLAP_TOLERANCE
        )

        overlap_start = max(gym_start, garmin_start)
        overlap_end = min(gym_end + OVERLAP_TOLERANCE, garmin_end + OVERLAP_TOLERANCE)

        if overlap_start <= overlap_end:
            overlap_seconds = (overlap_end - overlap_start).total_seconds()
            if overlap_seconds > best_overlap:
                best_overlap = overlap_seconds
                best_match = garmin

    return best_match
