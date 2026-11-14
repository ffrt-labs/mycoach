"""``health.get_daily_steps``: one entry per day, oldest first, gaps kept (#196)."""

from datetime import date

from mycoach import health
from mycoach.models.health import DailyHealthSnapshot
from tests.conftest import test_session

ATHLETE = 1


async def _seed(*rows: tuple[date, int | None, str]) -> None:
    async with test_session() as session:
        for day, steps, source in rows:
            session.add(
                DailyHealthSnapshot(
                    user_id=ATHLETE, snapshot_date=day, steps=steps, data_source=source
                )
            )
        await session.commit()


async def test_returns_every_day_up_to_and_including_until_oldest_first() -> None:
    await _seed(
        (date(2026, 11, 10), 8000, "garmin"),
        (date(2026, 11, 13), 12431, "garmin"),
        (date(2026, 11, 14), 900, "garmin"),  # today: after ``until``, left out
        (date(2026, 11, 9), 5000, "garmin"),  # before the window
    )

    days = await health.get_daily_steps(ATHLETE, until=date(2026, 11, 13), days=4)

    assert days == [
        {"date": "2026-11-10", "steps": 8000},
        {"date": "2026-11-11", "steps": None},
        {"date": "2026-11-12", "steps": None},
        {"date": "2026-11-13", "steps": 12431},
    ]


async def test_defaults_to_fourteen_days() -> None:
    days = await health.get_daily_steps(ATHLETE, until=date(2026, 11, 13))

    assert len(days) == 14
    assert days[0]["date"] == "2026-10-31"
    assert all(d["steps"] is None for d in days)


async def test_a_day_with_several_sources_takes_the_highest_count() -> None:
    await _seed(
        (date(2026, 11, 13), 9000, "garmin"),
        (date(2026, 11, 13), 11000, "manual"),
        (date(2026, 11, 12), None, "manual"),
        (date(2026, 11, 12), 7000, "garmin"),
    )

    days = await health.get_daily_steps(ATHLETE, until=date(2026, 11, 13), days=2)

    assert [d["steps"] for d in days] == [7000, 11000]


async def test_other_athletes_steps_are_not_read() -> None:
    async with test_session() as session:
        session.add(DailyHealthSnapshot(user_id=2, snapshot_date=date(2026, 11, 13), steps=5))
        await session.commit()

    days = await health.get_daily_steps(ATHLETE, until=date(2026, 11, 13), days=1)

    assert days == [{"date": "2026-11-13", "steps": None}]
