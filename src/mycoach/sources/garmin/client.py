"""Garmin Connect API client wrapper using garminconnect library."""

import logging
from datetime import date
from typing import Any

from garminconnect import Garmin  # type: ignore[import-untyped]

from mycoach.sources.garmin.auth import GarminAuth

logger = logging.getLogger(__name__)


class GarminClient:
    """Wraps the garminconnect library to fetch health and activity data."""

    def __init__(self, auth: GarminAuth | None = None) -> None:
        self.auth = auth or GarminAuth()
        self._api: Garmin | None = None

    def connect(self) -> bool:
        """Authenticate and initialize the Garmin API client.

        Returns:
            True if connection succeeds, False otherwise.
        """
        if not self.auth.login():
            return False

        try:
            self._api = Garmin()
            self._api.login()
            logger.info("Garmin API client connected")
            return True
        except Exception:
            logger.exception("Failed to initialize Garmin API client")
            return False

    @property
    def api(self) -> Garmin:
        if self._api is None:
            raise RuntimeError("GarminClient not connected. Call connect() first.")
        return self._api

    def get_stats(self, day: date) -> dict[str, Any]:
        """Get daily stats (steps, HR, stress, Body Battery, etc.)."""
        return self.api.get_stats(day.isoformat())  # type: ignore[no-any-return]

    def get_heart_rates(self, day: date) -> dict[str, Any]:
        """Get heart rate data for a day."""
        return self.api.get_heart_rates(day.isoformat())  # type: ignore[no-any-return]

    def get_hrv_data(self, day: date) -> dict[str, Any]:
        """Get HRV data for a day."""
        return self.api.get_hrv_data(day.isoformat())  # type: ignore[no-any-return]

    def get_sleep_data(self, day: date) -> dict[str, Any]:
        """Get sleep data for a day."""
        return self.api.get_sleep_data(day.isoformat())  # type: ignore[no-any-return]

    def get_stress_data(self, day: date) -> dict[str, Any]:
        """Get stress data for a day."""
        return self.api.get_stress_data(day.isoformat())  # type: ignore[no-any-return]

    def get_body_battery(self, start: date, end: date) -> list[dict[str, Any]]:
        """Get Body Battery data for a date range."""
        return self.api.get_body_battery(  # type: ignore[no-any-return]
            start.isoformat(), end.isoformat()
        )

    def get_training_readiness(self, day: date) -> dict[str, Any]:
        """Get training readiness score for a day."""
        return self.api.get_training_readiness(day.isoformat())  # type: ignore[no-any-return]

    def get_training_status(self, day: date) -> dict[str, Any]:
        """Get training status for a day."""
        return self.api.get_training_status(day.isoformat())  # type: ignore[no-any-return]

    def get_max_metrics(self, day: date) -> dict[str, Any]:
        """Get max metrics (VO2max, etc.) for a day."""
        return self.api.get_max_metrics(day.isoformat())  # type: ignore[no-any-return]

    def get_respiration_data(self, day: date) -> dict[str, Any]:
        """Get respiration data for a day."""
        return self.api.get_respiration_data(day.isoformat())  # type: ignore[no-any-return]

    def get_spo2_data(self, day: date) -> dict[str, Any]:
        """Get SpO2 data for a day."""
        return self.api.get_spo2_data(day.isoformat())  # type: ignore[no-any-return]

    def get_activities_by_date(self, start: date, end: date) -> list[dict[str, Any]]:
        """Get activities for a date range."""
        return self.api.get_activities_by_date(  # type: ignore[no-any-return]
            start.isoformat(), end.isoformat()
        )
