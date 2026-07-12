"""Garmin DataSource implementation — orchestrates auth, fetch, and import."""

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.user import User
from mycoach.sources.base import DataSource, ImportResult
from mycoach.sources.garmin.client import GarminClient
from mycoach.sources.garmin.mappers import (
    import_activities,
    import_health_snapshot,
    map_health_snapshot,
)

logger = logging.getLogger(__name__)


class GarminSource(DataSource):
    """Fetches health snapshots and activities from Garmin Connect."""

    def __init__(self, client: GarminClient | None = None) -> None:
        self._client = client or GarminClient()

    @property
    def source_type(self) -> str:
        return "garmin"

    async def authenticate(self) -> bool:
        return self._client.connect()

    async def fetch_and_import(
        self, session: AsyncSession, user_id: int, since: datetime | None = None
    ) -> ImportResult:
        """Fetch health and activity data from Garmin and import into DB.

        Fetches daily health snapshots and activities for each day in the range.
        Default range: last 7 days if `since` is not provided.
        """
        result = ImportResult(source_type="garmin")
        errors: list[str] = []

        user_exists = await session.execute(select(User.id).where(User.id == user_id))
        if user_exists.scalar_one_or_none() is None:
            result.errors = [
                f"No profile found for user_id={user_id}. Create one at /api/profile first."
            ]
            return result

        end_date = date.today()
        if since:
            start_date = since.date() if isinstance(since, datetime) else since
        else:
            start_date = end_date - timedelta(days=7)

        # Fetch health snapshots day by day
        empty_health_days: list[date] = []
        current = start_date
        while current <= end_date:
            try:
                snapshot, has_data = self._fetch_health_for_day(user_id, current)
                if not has_data:
                    empty_health_days.append(current)
                created = await import_health_snapshot(session, snapshot)
                if created:
                    result.health_snapshots_created += 1
                else:
                    result.health_snapshots_updated += 1
            except Exception as e:
                await session.rollback()
                errors.append(f"Health fetch failed for {current}: {e}")
                logger.warning("Health fetch failed for %s: %s", current, e)
            current += timedelta(days=1)

        if empty_health_days:
            errors.append(
                f"{len(empty_health_days)} day(s) synced with no usable Garmin health data: "
                f"{', '.join(str(d) for d in empty_health_days)}"
            )

        # Fetch activities for the date range
        try:
            raw_activities = self._client.get_activities_by_date(start_date, end_date)
            act_result = await import_activities(session, user_id, raw_activities)
            result.activities_created = act_result.activities_created
            result.activities_skipped = act_result.activities_skipped
            if act_result.errors:
                errors.extend(act_result.errors)
        except Exception as e:
            await session.rollback()
            errors.append(f"Activities fetch failed: {e}")
            logger.warning("Activities fetch failed: %s", e)

        await session.commit()

        if errors:
            result.errors = errors
        return result

    def _fetch_health_for_day(self, user_id: int, day: date) -> tuple[DailyHealthSnapshot, bool]:
        """Fetch all health data for a single day and build a snapshot.

        Each API call is wrapped individually so partial data is still captured.
        """
        raw_stats = self._safe_call(self._client.get_stats, day)
        stats = raw_stats if isinstance(raw_stats, dict) else {}
        sleep = self._safe_call(self._client.get_sleep_data, day)
        hrv = self._safe_call(self._client.get_hrv_data, day)
        stress = self._safe_call(self._client.get_stress_data, day)
        body_battery = self._safe_call(self._client.get_body_battery, day, day)
        training_readiness = self._safe_call(self._client.get_training_readiness, day)
        training_status = self._safe_call(self._client.get_training_status, day)
        max_metrics = self._safe_call(self._client.get_max_metrics, day)
        respiration = self._safe_call(self._client.get_respiration_data, day)
        spo2 = self._safe_call(self._client.get_spo2_data, day)

        field_status = {
            "stats": bool(stats),
            "sleep": isinstance(sleep, dict),
            "hrv": isinstance(hrv, dict),
            "stress": isinstance(stress, dict),
            "body_battery": isinstance(body_battery, list),
            "training_readiness": isinstance(training_readiness, dict),
            "training_status": isinstance(training_status, dict),
            "max_metrics": bool(max_metrics),
            "respiration": isinstance(respiration, dict),
            "spo2": isinstance(spo2, dict),
        }
        if not any(field_status.values()):
            logger.warning(
                "Garmin health fetch for %s: no usable fields at all — %s", day, field_status
            )
        elif not all(field_status.values()):
            logger.info("Garmin health fetch for %s: partial data — %s", day, field_status)

        snapshot = map_health_snapshot(
            user_id=user_id,
            snapshot_date=day,
            stats=stats,
            sleep=sleep,
            hrv=hrv,
            stress=stress,
            body_battery=body_battery if isinstance(body_battery, list) else None,
            training_readiness=training_readiness,
            training_status=training_status,
            max_metrics=max_metrics,
            respiration=respiration,
            spo2=spo2,
        )
        return snapshot, any(field_status.values())

    @staticmethod
    def _safe_call(func: Any, *args: Any) -> Any:
        """Call a Garmin API method, returning None on failure."""
        try:
            return func(*args)
        except Exception as e:
            logger.warning("Garmin API call %s failed: %s", func.__name__, e)
            return None
