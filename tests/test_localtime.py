"""The athlete's local time: the one conversion between stored UTC and the athlete's day."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from mycoach.localtime import local_day, local_today, to_naive_utc


def test_local_today_is_the_date_in_the_given_timezone() -> None:
    # 23:30 UTC on 1 July is already 2 July in London (BST) but still 1 July in UTC.
    late_evening = datetime(2026, 7, 1, 23, 30, tzinfo=UTC)

    assert local_today("Europe/London", now=late_evening) == date(2026, 7, 2)
    assert local_today("UTC", now=late_evening) == date(2026, 7, 1)
    assert local_today(ZoneInfo("America/New_York"), now=late_evening) == date(2026, 7, 1)


def test_local_day_of_a_stored_naive_utc_time() -> None:
    assert local_day(datetime(2026, 7, 1, 23, 30), "Europe/London") == date(2026, 7, 2)
    assert local_day("2026-07-01 23:30:00", "Europe/London") == date(2026, 7, 2)
    assert local_day("2026-07-01 23:30:00", "UTC") == date(2026, 7, 1)


def test_a_naive_input_is_local_wall_time() -> None:
    assert to_naive_utc(datetime(2026, 7, 2, 0, 30), "Europe/London") == datetime(
        2026, 7, 1, 23, 30
    )
    aware = datetime(2026, 7, 2, 0, 30, tzinfo=ZoneInfo("Europe/London"))
    assert to_naive_utc(aware, "UTC") == datetime(2026, 7, 1, 23, 30)
