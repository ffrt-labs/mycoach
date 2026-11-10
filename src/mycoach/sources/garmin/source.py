"""Garmin DataSource implementation — orchestrates auth, fetch, and import."""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mycoach.models.health import DailyHealthSnapshot
from mycoach.models.athlete import Athlete
from mycoach.sources.base import DataSource, ImportResult
from mycoach.sources.garmin.client import GarminClient
from mycoach.sources.garmin.mappers import (
    import_activities,
    import_health_snapshot,
    map_health_snapshot,
    snapshot_has_data,
    snapshot_null_content_fields,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceUpload:
    """When the user's watch last reached Garmin's servers, and which watch."""

    uploaded_at: datetime
    """UTC, timezone-aware."""

    device_name: str | None


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

        athlete_exists = await session.execute(select(Athlete.id).where(Athlete.id == user_id))
        if athlete_exists.scalar_one_or_none() is None:
            result.errors = [
                f"No profile found for athlete_id={user_id}. Create one at /api/profile first."
            ]
            return result

        end_date = date.today()
        if since:
            start_date = since.date() if isinstance(since, datetime) else since
        else:
            start_date = end_date - timedelta(days=7)

        # Fetch health snapshots day by day
        current = start_date
        while current <= end_date:
            try:
                snapshot, has_data, failures = self._fetch_health_for_day(user_id, current)
                if not has_data:
                    result.empty_health_days.append(current)
                if failures:
                    errors.append(
                        f"Garmin API calls failed for {current}: {', '.join(failures)}"
                    )
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

        if result.empty_health_days:
            errors.append(
                f"{len(result.empty_health_days)} day(s) synced with no usable Garmin "
                f"health data: {', '.join(str(d) for d in result.empty_health_days)}"
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

    def get_last_device_upload(self) -> DeviceUpload | None:
        """When the watch last uploaded to Garmin, or None if that is unknowable.

        A day of nulls does not say whether the user simply had nothing to
        record or whether the watch stopped syncing days ago; this does. It is
        what turns an alert from "no briefing today" into "your watch hasn't
        uploaded since Aug 19 at 05:20", which is the difference between a
        message the user can act on and one they can only be annoyed by.

        Returns None rather than raising: this only ever decorates an alert
        that is already being sent, and losing the decoration must not lose
        the alert.
        """
        try:
            device = self._client.get_device_last_used()
        except Exception as e:  # noqa: BLE001 - the alert matters more than the detail
            logger.warning("Garmin last-used-device lookup failed: %s", e)
            return None

        if not isinstance(device, dict):
            return None
        raw = device.get("lastUsedDeviceUploadTime")
        if not isinstance(raw, int | float):
            return None
        # Garmin reports this as epoch milliseconds.
        return DeviceUpload(
            uploaded_at=datetime.fromtimestamp(raw / 1000, tz=UTC),
            device_name=device.get("lastUsedDeviceName") or None,
        )

    def _fetch_health_for_day(
        self, user_id: int, day: date
    ) -> tuple[DailyHealthSnapshot, bool, list[str]]:
        """Fetch all health data for a single day and build a snapshot.

        Each API call is wrapped individually so partial data is still captured.
        Returns the snapshot, whether it carries usable content, and the names
        of any Garmin calls that raised.
        """
        failures: list[str] = []
        raw_stats = self._safe_call(self._client.get_stats, day, failures=failures)
        stats = raw_stats if isinstance(raw_stats, dict) else {}
        sleep = self._safe_call(self._client.get_sleep_data, day, failures=failures)
        hrv = self._safe_call(self._client.get_hrv_data, day, failures=failures)
        stress = self._safe_call(self._client.get_stress_data, day, failures=failures)
        body_battery = self._safe_call(
            self._client.get_body_battery, day, day, failures=failures
        )
        training_readiness = self._safe_call(
            self._client.get_training_readiness, day, failures=failures
        )
        training_status = self._safe_call(
            self._client.get_training_status, day, failures=failures
        )
        max_metrics = self._safe_call(self._client.get_max_metrics, day, failures=failures)
        respiration = self._safe_call(
            self._client.get_respiration_data, day, failures=failures
        )
        spo2 = self._safe_call(self._client.get_spo2_data, day, failures=failures)

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

        # Judge the mapped content, not the response type. Garmin answers a day
        # it has nothing for with a well-formed document of nulls, which an
        # isinstance() check scores as data — that is what hid two days of
        # missing briefing data.
        has_data = snapshot_has_data(snapshot)
        if not has_data:
            logger.warning(
                "Garmin health fetch for %s: response carried no usable values", day
            )
        else:
            null_fields = snapshot_null_content_fields(snapshot)
            if null_fields:
                logger.info(
                    "Garmin health fetch for %s: partial data — null fields: %s",
                    day,
                    ", ".join(null_fields),
                )
        return snapshot, has_data, failures

    @staticmethod
    def _safe_call(func: Any, *args: Any, failures: list[str] | None = None) -> Any:
        """Call a Garmin API method, returning None on failure.

        The failure is also appended to ``failures`` when one is given, because
        a returned ``None`` alone cannot be told apart from an endpoint that
        legitimately had nothing to report — and treating a crash as an empty
        day is how a broken sync stays invisible.
        """
        try:
            return func(*args)
        except Exception as e:
            # getattr, not func.__name__: unittest.mock test doubles don't carry
            # __name__ unless explicitly configured, and a call that fails to
            # even be named must not be swallowed for that reason.
            name = getattr(func, "__name__", repr(func))
            logger.warning("Garmin API call %s failed: %s", name, e)
            if failures is not None:
                failures.append(name)
            return None
