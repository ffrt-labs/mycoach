"""The one-way Garmin weight push (ticket #199), against a mocked Garmin Connect account.

MyCoach owns the weight: a reading is saved locally first, then copied to Garmin. Garmin's
weigh-ins are never read back as data, only to learn the id of the one we created.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from mycoach import health

ATHLETE = 1
# 10 July 2026, 07:00 UTC = 08:00 in Europe/London (BST).
MEASURED_AT = datetime(2026, 7, 10, 7, 0)
GMT_MS = int(MEASURED_AT.replace(tzinfo=UTC).timestamp() * 1000)


def _garmin_has(api: MagicMock, sample_pk: int, weight_kg: float, gmt_ms: int = GMT_MS) -> None:
    api.get_daily_weigh_ins.return_value = {
        "dateWeightList": [
            {"samplePk": sample_pk, "weight": weight_kg * 1000, "timestampGMT": gmt_ms}
        ]
    }


async def _status() -> str | None:
    rows = await health.list_body_measurements(ATHLETE)
    status: str | None = rows[0]["garmin_push_status"]
    return status


async def test_a_weight_is_pushed_with_local_and_utc_timestamps(fake_garmin: MagicMock) -> None:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )
    assert await _status() == "pending"
    _garmin_has(fake_garmin, 9001, 82.4)

    await health.push_weight_to_garmin(ATHLETE, recorded.id)

    fake_garmin.add_weigh_in_with_timestamps.assert_called_once_with(
        weight=82.4,
        unitKey="kg",
        dateTimestamp="2026-07-10T08:00:00",
        gmtTimestamp="2026-07-10T07:00:00",
    )
    assert await _status() == "pushed"

    deleted = await health.delete_body_measurement(ATHLETE, recorded.id)
    assert deleted is not None
    await health.delete_weight_from_garmin(deleted)
    fake_garmin.delete_weigh_in.assert_called_once_with("9001", "2026-07-10")
