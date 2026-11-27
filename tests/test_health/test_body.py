"""Body measurements through the health module (ticket #198).

MyCoach owns weight and waist: the Body page records them through a health command and
reads them back through a health query, as plain data.
"""

from datetime import datetime

import pytest

from mycoach import health

ATHLETE = 1


async def test_a_recorded_measurement_reads_back_newest_first() -> None:
    await health.record_body_measurement(
        ATHLETE, measured_at=datetime(2026, 11, 10, 7, 0), weight_kg=82.4
    )
    await health.record_body_measurement(
        ATHLETE,
        measured_at=datetime(2026, 11, 12, 7, 5),
        weight_kg=82.0,
        waist_cm=91.5,
        note="salty dinner",
    )

    rows = await health.list_body_measurements(ATHLETE)

    assert [(r["measured_at"], r["weight_kg"], r["waist_cm"], r["note"]) for r in rows] == [
        ("2026-11-12 07:05:00", 82.0, 91.5, "salty dinner"),
        ("2026-11-10 07:00:00", 82.4, None, None),
    ]
    assert {r["source"] for r in rows} == {"manual"}


async def test_a_measurement_needs_weight_or_waist() -> None:
    with pytest.raises(ValueError):
        await health.record_body_measurement(ATHLETE, measured_at=datetime(2026, 11, 10, 7, 0))

    assert await health.list_body_measurements(ATHLETE) == []


async def test_listing_is_limited_and_per_athlete() -> None:
    for day in range(1, 13):
        await health.record_body_measurement(
            ATHLETE, measured_at=datetime(2026, 11, day, 7, 0), weight_kg=80 + day / 10
        )
    await health.record_body_measurement(
        2, measured_at=datetime(2026, 11, 13, 7, 0), weight_kg=60.0
    )

    rows = await health.list_body_measurements(ATHLETE, limit=10)

    assert len(rows) == 10
    assert rows[0]["measured_at"] == "2026-11-12 07:00:00"


async def test_listing_since_a_time_returns_everything_after_it() -> None:
    for day in (1, 5, 9):
        await health.record_body_measurement(
            ATHLETE, measured_at=datetime(2026, 11, day, 7, 0), weight_kg=80.0
        )

    rows = await health.list_body_measurements(ATHLETE, since=datetime(2026, 11, 5), limit=None)

    assert [r["measured_at"][:10] for r in rows] == ["2026-11-09", "2026-11-05"]


async def test_a_deleted_measurement_is_gone_and_its_facts_are_returned() -> None:
    recorded = await health.record_body_measurement(
        ATHLETE, measured_at=datetime(2026, 11, 10, 7, 0), weight_kg=82.4
    )

    deleted = await health.delete_body_measurement(ATHLETE, recorded.id)

    assert deleted is not None
    assert deleted.weight_kg == 82.4
    assert deleted.measured_at == datetime(2026, 11, 10, 7, 0)
    assert await health.list_body_measurements(ATHLETE) == []


async def test_deleting_another_athletes_measurement_does_nothing() -> None:
    recorded = await health.record_body_measurement(
        2, measured_at=datetime(2026, 11, 10, 7, 0), weight_kg=60.0
    )

    assert await health.delete_body_measurement(ATHLETE, recorded.id) is None
    assert len(await health.list_body_measurements(2)) == 1


async def test_height_is_entered_once_and_read_back() -> None:
    assert await health.get_athlete_height(ATHLETE) is None

    await health.set_athlete_height(ATHLETE, 180.0)
    await health.set_athlete_height(ATHLETE, 181.5)

    assert await health.get_athlete_height(ATHLETE) == 181.5
