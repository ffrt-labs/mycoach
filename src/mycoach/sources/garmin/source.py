"""Garmin DataSource implementation — orchestrates auth, fetch, and import."""

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.health import DailyHealthSnapshot
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

        end_date = date.today()
        if since:
            start_date = since.date() if isinstance(since, datetime) else since
        else:
            start_date = end_date - timedelta(days=7)

        # Fetch health snapshots day by day
        current = start_date
        while current <= end_date:
            try:
                snapshot = self._fetch_health_for_day(user_id, current)
                created = await import_health_snapshot(session, snapshot)
                if created:
                    result.health_snapshots_created += 1
            except Exception as e:
                errors.append(f"Health fetch failed for {current}: {e}")
                logger.warning("Health fetch failed for %s: %s", current, e)
            current += timedelta(days=1)

        # Fetch activities for the date range
        try:
            raw_activities = self._client.get_activities_by_date(start_date, end_date)
            act_result = await import_activities(session, user_id, raw_activities)
            result.activities_created = act_result.activities_created
            result.activities_skipped = act_result.activities_skipped
            if act_result.errors:
                errors.extend(act_result.errors)
        except Exception as e:
            errors.append(f"Activities fetch failed: {e}")
            logger.warning("Activities fetch failed: %s", e)

        await session.commit()

        if errors:
            result.errors = errors
        return result

    def _fetch_health_for_day(self, user_id: int, day: date) -> DailyHealthSnapshot:
        """Fetch all health data for a single day and build a snapshot.

        Each API call is wrapped individually so partial data is still captured.
        """
        stats = self._safe_call(self._client.get_stats, day) or {}
        sleep = self._safe_call(self._client.get_sleep_data, day)
        hrv = self._safe_call(self._client.get_hrv_data, day)
        stress = self._safe_call(self._client.get_stress_data, day)
        body_battery = self._safe_call(self._client.get_body_battery, day, day)
        training_readiness = self._safe_call(self._client.get_training_readiness, day)
        training_status = self._safe_call(self._client.get_training_status, day)
        max_metrics = self._safe_call(self._client.get_max_metrics, day)
        respiration = self._safe_call(self._client.get_respiration_data, day)
        spo2 = self._safe_call(self._client.get_spo2_data, day)

        return map_health_snapshot(
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

    @staticmethod
    def _safe_call(func: Any, *args: Any) -> Any:
        """Call a Garmin API method, returning None on failure."""
        try:
            return func(*args)
        except Exception:
            logger.debug("Garmin API call %s failed", func.__name__, exc_info=True)
            return None
