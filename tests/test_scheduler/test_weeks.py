"""Which week a scheduled plan or recap is for."""

from datetime import date

import pytest

from mycoach.scheduler.weeks import plan_week_start, recap_week_start


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2024, 6, 9), date(2024, 6, 10)),  # Sunday: the next day
        (date(2024, 6, 10), date(2024, 6, 17)),  # Monday: a week ahead
        (date(2024, 6, 12), date(2024, 6, 17)),  # Wednesday
    ],
)
def test_the_plan_is_for_the_next_monday(today: date, expected: date) -> None:
    assert plan_week_start(today) == expected


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2024, 6, 10), date(2024, 6, 3)),  # Monday: the week just ended
        (date(2024, 6, 16), date(2024, 6, 3)),  # Sunday: still the week before
    ],
)
def test_the_recap_is_for_the_week_before(today: date, expected: date) -> None:
    assert recap_week_start(today) == expected
