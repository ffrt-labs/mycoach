"""The outbound half of the Garmin adapter: copy a weight to Garmin Connect (ticket #199).

The health side is the source of truth for Body measurements (entered manually); Garmin
is a downstream, one-way copy, so its own features (and the watch's calorie maths) see
the weight. Nothing here is ever imported back: the only read is the
day's weigh-in list, to learn the id of the weigh-in we just created.

Blocking, like the rest of ``GarminClient``; callers run it in a thread.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from mycoach.localtime import local_day
from mycoach.sources.garmin.client import GarminClient

logger = logging.getLogger(__name__)


class GarminWeighIns:
    """Adds and deletes weigh-ins on a connected ``GarminClient``."""

    def __init__(self, client: GarminClient, timezone: str) -> None:
        self._client = client
        self._tz = ZoneInfo(timezone)

    def add(self, weight_kg: float, measured_at: datetime) -> str | None:
        """Add a manual weigh-in at ``measured_at`` (naive UTC); return Garmin's id for it.

        garminconnect 0.3.2 reads ``dateTimestamp`` as the *server's* local time, so the
        athlete's wall time is passed naive (it survives unchanged) and the instant as naive
        UTC. The POST usually answers 204 with no body, so the id comes from the day's list.
        None when Garmin took the weigh-in but its id can't be found.
        """
        utc = measured_at.replace(tzinfo=UTC)
        local = utc.astimezone(self._tz).replace(tzinfo=None)
        response = self._client.api.add_weigh_in_with_timestamps(
            weight=weight_kg,
            unitKey="kg",
            dateTimestamp=local.isoformat(timespec="seconds"),
            gmtTimestamp=measured_at.isoformat(timespec="seconds"),
        )
        if isinstance(response, dict) and response.get("samplePk") is not None:
            return str(response["samplePk"])
        return self._find(weight_kg, utc, local.date().isoformat())

    def delete(self, weigh_in_id: str, measured_at: datetime) -> None:
        """Delete the weigh-in Garmin knows as ``weigh_in_id`` (taken at ``measured_at``)."""
        self._client.api.delete_weigh_in(weigh_in_id, local_day(measured_at, self._tz).isoformat())

    def _find(self, weight_kg: float, utc: datetime, cdate: str) -> str | None:
        day = self._client.api.get_daily_weigh_ins(cdate)
        entries: list[dict[str, Any]] = (day or {}).get("dateWeightList") or []
        gmt_ms = int(utc.timestamp() * 1000)
        same_time = [e for e in entries if e.get("timestampGMT") == gmt_ms]
        same_weight = [
            e
            for e in same_time or entries
            if isinstance(e.get("weight"), int | float) and abs(e["weight"] - weight_kg * 1000) < 50
        ]
        match = same_weight or same_time
        if not match or match[-1].get("samplePk") is None:
            logger.warning("Garmin took the weigh-in for %s but its id was not found", cdate)
            return None
        return str(match[-1]["samplePk"])
