"""Merge Garmin and Hevy gym activities by date/time overlap.

For gym sessions, Hevy CSV is the source of truth for exercise details (sets,
reps, weights, RPE). Garmin provides the HR/calorie/training-effect overlay.
This module matches activities from both sources by time overlap and merges
them into a single "merged" activity.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.activity import Activity

logger = logging.getLogger(__name__)

# Two activities are considered overlapping if one starts within this window
# of the other's time range.
OVERLAP_TOLERANCE = timedelta(minutes=30)


@dataclass
class MergeResult:
    """Result of a merge operation."""

    merged: int = 0
    errors: list[str] | None = None

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


async def merge_garmin_hevy(session: AsyncSession, user_id: int) -> MergeResult:
    """Find and merge overlapping Garmin + Hevy gym activities for a user.

    For each unmerged Hevy gym activity, find a Garmin gym activity that overlaps
    in time. When a match is found:
    1. Copy Garmin HR/calorie/training data onto the Hevy activity.
    2. Set data_source to "merged" and store the garmin_activity_id.
    3. Delete the now-redundant Garmin activity.

    Returns:
        MergeResult with count of merged activities and any errors.
    """
    result = MergeResult()
    errors: list[str] = []

    # Get all unmerged Hevy gym activities
    hevy_stmt = select(Activity).where(
        Activity.user_id == user_id,
        Activity.sport == "gym",
        Activity.data_source == "hevy",
    )
    hevy_result = await session.execute(hevy_stmt)
    hevy_activities = list(hevy_result.scalars().all())

    if not hevy_activities:
        return result

    # Get all unmerged Garmin gym activities
    garmin_stmt = select(Activity).where(
        Activity.user_id == user_id,
        Activity.sport == "gym",
        Activity.data_source == "garmin",
    )
    garmin_result = await session.execute(garmin_stmt)
    garmin_activities = list(garmin_result.scalars().all())

    if not garmin_activities:
        return result

    # Track which Garmin activities have been matched to avoid double-matching
    matched_garmin_ids: set[int] = set()

    for hevy in hevy_activities:
        match = _find_overlapping_garmin(hevy, garmin_activities, matched_garmin_ids)
        if match is None:
            continue

        matched_garmin_ids.add(match.id)

        # Capture Garmin data before deleting
        g_avg_hr: int | None = match.avg_hr
        g_max_hr: int | None = match.max_hr
        g_calories: int | None = match.calories
        g_hr_zones: str | None = match.hr_zones
        g_te_aerobic: float | None = match.training_effect_aerobic
        g_te_anaerobic: float | None = match.training_effect_anaerobic
        g_activity_id: str | None = match.garmin_activity_id
        g_duration: int | None = match.duration_minutes

        # Delete the Garmin activity first to free the unique garmin_activity_id
        await session.delete(match)
        await session.flush()

        # Now merge Garmin data onto the Hevy activity
        hevy.avg_hr = g_avg_hr
        hevy.max_hr = g_max_hr
        hevy.calories = g_calories
        hevy.hr_zones = g_hr_zones
        hevy.training_effect_aerobic = g_te_aerobic
        hevy.training_effect_anaerobic = g_te_anaerobic
        hevy.garmin_activity_id = g_activity_id
        hevy.data_source = "merged"

        # Use Garmin duration if Hevy doesn't have one
        if hevy.duration_minutes is None and g_duration is not None:
            hevy.duration_minutes = g_duration

        result.merged += 1
        logger.info(
            "Merged Garmin activity %s into Hevy activity %s ('%s' at %s)",
            match.garmin_activity_id,
            hevy.id,
            hevy.title,
            hevy.start_time,
        )

    if errors:
        result.errors = errors

    await session.flush()
    return result


def _find_overlapping_garmin(
    hevy: Activity,
    garmin_activities: list[Activity],
    already_matched: set[int],
) -> Activity | None:
    """Find a Garmin activity that overlaps in time with a Hevy activity.

    Two activities overlap if either one starts during the other's time range
    (with tolerance), or if they start within the tolerance window of each other.
    """
    best_match: Activity | None = None
    best_overlap: float = 0.0

    hevy_start = hevy.start_time
    hevy_end = hevy.end_time or (
        hevy_start + timedelta(minutes=hevy.duration_minutes)
        if hevy.duration_minutes
        else hevy_start + OVERLAP_TOLERANCE
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

        # Expand ranges by tolerance for fuzzy matching
        overlap_start = max(hevy_start, garmin_start)
        overlap_end = min(
            hevy_end + OVERLAP_TOLERANCE,
            garmin_end + OVERLAP_TOLERANCE,
        )

        if overlap_start <= overlap_end:
            overlap_seconds = (overlap_end - overlap_start).total_seconds()
            if overlap_seconds > best_overlap:
                best_overlap = overlap_seconds
                best_match = garmin

    return best_match
