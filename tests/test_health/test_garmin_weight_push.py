"""The one-way Garmin weight push (ticket #199), against a mocked Garmin Connect account.

MyCoach owns the weight: a reading is saved locally first, then copied to Garmin. Garmin's
weigh-ins are never read back as data, only to learn the id of the one we created.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from mycoach import health
from mycoach.sources.garmin.client import GarminClient
from mycoach.sources.garmin.source import GarminSource

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


def _sync_client() -> GarminClient:
    """The sync's own client, already connected."""
    client = GarminClient()
    assert client.connect()
    return client


async def test_a_failed_push_is_recorded_and_the_sync_retries_it(
    fake_garmin: MagicMock,
) -> None:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )
    fake_garmin.add_weigh_in_with_timestamps.side_effect = ConnectionError("Garmin is down")

    status = await health.push_weight_to_garmin(ATHLETE, recorded.id)

    assert status == "failed"
    assert await _status() == "failed"

    fake_garmin.add_weigh_in_with_timestamps.side_effect = None
    _garmin_has(fake_garmin, 9002, 82.4)
    assert await health.retry_garmin_weight_pushes(ATHLETE, _sync_client()) == 1
    assert await _status() == "pushed"
    assert fake_garmin.add_weigh_in_with_timestamps.call_count == 2


async def test_retries_stop_after_five_attempts(fake_garmin: MagicMock) -> None:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )
    fake_garmin.add_weigh_in_with_timestamps.side_effect = ConnectionError("Garmin is down")
    await health.push_weight_to_garmin(ATHLETE, recorded.id)

    for _ in range(6):
        await health.retry_garmin_weight_pushes(ATHLETE, _sync_client())

    assert fake_garmin.add_weigh_in_with_timestamps.call_count == 5
    assert await _status() == "failed"


async def test_a_fresh_pending_weight_is_left_to_its_own_push(fake_garmin: MagicMock) -> None:
    await health.record_body_measurement(ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4)

    assert await health.retry_garmin_weight_pushes(ATHLETE, _sync_client()) == 0
    fake_garmin.add_weigh_in_with_timestamps.assert_not_called()

    later = datetime.utcnow() + timedelta(minutes=11)
    assert await health.retry_garmin_weight_pushes(ATHLETE, _sync_client(), now=later) == 1


async def test_a_waist_only_reading_is_never_pushed(fake_garmin: MagicMock) -> None:
    recorded = await health.record_body_measurement(ATHLETE, measured_at=MEASURED_AT, waist_cm=91.0)

    assert await health.push_weight_to_garmin(ATHLETE, recorded.id) is None
    later = datetime.utcnow() + timedelta(hours=1)
    await health.retry_garmin_weight_pushes(ATHLETE, _sync_client(), now=later)

    fake_garmin.add_weigh_in_with_timestamps.assert_not_called()
    assert await _status() is None


async def test_a_failed_login_counts_as_a_failed_attempt(fake_garmin: MagicMock) -> None:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )
    fake_garmin.login.side_effect = RuntimeError("401")

    assert await health.push_weight_to_garmin(ATHLETE, recorded.id) == "failed"


async def test_without_garmin_credentials_nothing_is_attempted() -> None:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )

    assert await health.push_weight_to_garmin(ATHLETE, recorded.id) == "pending"


async def test_a_reading_deleted_mid_push_takes_its_garmin_copy_with_it(
    fake_garmin: MagicMock,
) -> None:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )
    _garmin_has(fake_garmin, 9003, 82.4)

    def delete_locally_meanwhile(**_: object) -> None:
        asyncio.run_coroutine_threadsafe(
            health.delete_body_measurement(ATHLETE, recorded.id), loop
        ).result()

    loop = asyncio.get_running_loop()
    fake_garmin.add_weigh_in_with_timestamps.side_effect = delete_locally_meanwhile

    assert await health.push_weight_to_garmin(ATHLETE, recorded.id) is None
    fake_garmin.delete_weigh_in.assert_called_once_with("9003", "2026-07-10")


async def test_deleting_a_never_pushed_reading_leaves_garmin_alone(
    fake_garmin: MagicMock,
) -> None:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )
    deleted = await health.delete_body_measurement(ATHLETE, recorded.id)
    assert deleted is not None

    assert await health.delete_weight_from_garmin(deleted) is False
    fake_garmin.delete_weigh_in.assert_not_called()


async def test_a_garmin_outage_on_delete_does_not_raise(fake_garmin: MagicMock) -> None:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )
    _garmin_has(fake_garmin, 9004, 82.4)
    await health.push_weight_to_garmin(ATHLETE, recorded.id)
    deleted = await health.delete_body_measurement(ATHLETE, recorded.id)
    assert deleted is not None
    fake_garmin.delete_weigh_in.side_effect = ConnectionError("Garmin is down")

    assert await health.delete_weight_from_garmin(deleted) is False


async def test_the_garmin_sync_retries_unpushed_weights(fake_garmin: MagicMock) -> None:
    # The 06:00 sync and every briefing poll both run health.sync_garmin.
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )
    fake_garmin.add_weigh_in_with_timestamps.side_effect = ConnectionError("Garmin is down")
    await health.push_weight_to_garmin(ATHLETE, recorded.id)
    fake_garmin.add_weigh_in_with_timestamps.side_effect = None
    _garmin_has(fake_garmin, 9005, 82.4)
    source = GarminSource()
    assert await source.authenticate()

    await health.sync_garmin(ATHLETE, source, since=datetime.utcnow())

    assert await _status() == "pushed"


async def test_a_weight_retry_crash_never_fails_the_sync(
    fake_garmin: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_: object, **__: object) -> int:
        raise RuntimeError("database hiccup")

    monkeypatch.setattr("mycoach.health.commands.retry_garmin_weight_pushes", boom)
    source = GarminSource()
    assert await source.authenticate()

    outcome = await health.sync_garmin(ATHLETE, source, since=datetime.utcnow())

    assert outcome is not None


# --- A push whose Garmin id was not found (the POST answers 204, the day list lags) ---


async def _pushed_without_an_id(fake_garmin: MagicMock) -> int:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=MEASURED_AT, weight_kg=82.4
    )
    # The fixture's day list is empty: Garmin took the weigh-in, its id can't be found yet.
    assert await health.push_weight_to_garmin(ATHLETE, recorded.id) == "pushed"
    return recorded.id


async def test_a_push_whose_id_is_not_found_says_so_on_the_row(fake_garmin: MagicMock) -> None:
    await _pushed_without_an_id(fake_garmin)

    [row] = await health.list_body_measurements(ATHLETE)
    assert row["garmin_push_status"] == "pushed"
    assert "id was not found" in row["garmin_push_error"]


async def test_the_sync_looks_a_missing_id_up_again_without_pushing_twice(
    fake_garmin: MagicMock,
) -> None:
    measurement_id = await _pushed_without_an_id(fake_garmin)
    _garmin_has(fake_garmin, 9006, 82.4)

    await health.retry_garmin_weight_pushes(ATHLETE, _sync_client())

    fake_garmin.add_weigh_in_with_timestamps.assert_called_once()
    [row] = await health.list_body_measurements(ATHLETE)
    assert row["garmin_push_error"] is None
    deleted = await health.delete_body_measurement(ATHLETE, measurement_id)
    assert deleted is not None
    assert deleted.garmin_weigh_in_id == "9006"


async def test_a_delete_looks_a_missing_id_up_again(fake_garmin: MagicMock) -> None:
    measurement_id = await _pushed_without_an_id(fake_garmin)
    deleted = await health.delete_body_measurement(ATHLETE, measurement_id)
    assert deleted is not None
    _garmin_has(fake_garmin, 9007, 82.4)

    assert await health.delete_weight_from_garmin(deleted) is True
    fake_garmin.delete_weigh_in.assert_called_once_with("9007", "2026-07-10")


async def test_a_delete_whose_id_is_still_not_found_is_logged(
    fake_garmin: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    measurement_id = await _pushed_without_an_id(fake_garmin)
    deleted = await health.delete_body_measurement(ATHLETE, measurement_id)
    assert deleted is not None

    assert await health.delete_weight_from_garmin(deleted) is False
    fake_garmin.delete_weigh_in.assert_not_called()
    assert any("delete it there by hand" in r.getMessage() for r in caplog.records)
