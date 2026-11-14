"""The steps summary behind the briefing's 7-day strip and ``steps_summary`` (#196)."""

from datetime import UTC, date, datetime, timedelta

from mycoach.coaching.prompt_builder import daily_briefing_variables
from mycoach.coaching.steps import local_today, summarize_steps

YESTERDAY = date(2026, 11, 13)  # a Friday


def _daily(counts: list[int | None]) -> list[dict]:
    """``counts`` oldest first, the last one is yesterday (the health query's shape)."""
    start = YESTERDAY - timedelta(days=len(counts) - 1)
    return [{"date": str(start + timedelta(days=i)), "steps": c} for i, c in enumerate(counts)]


PREVIOUS = [10000] * 7


def test_headline_average_and_days_at_goal() -> None:
    summary = summarize_steps(
        _daily(PREVIOUS + [9000, 12000, 10000, 8000, 11000, 7000, 12431]), target=10000
    )

    assert summary.yesterday == 12431
    assert summary.avg_7d == 9919  # 69431 / 7
    assert summary.prev_avg_7d == 10000
    assert summary.days_at_goal == 4
    assert summary.target == 10000


def test_strip_is_seven_days_ending_yesterday_with_weekdays_and_goal_ticks() -> None:
    summary = summarize_steps(_daily(PREVIOUS + [5000, 10000, 2500, 20000, 0, 7500, 12431]), 10000)

    assert [d.weekday for d in summary.days] == ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
    assert [d.fill_pct for d in summary.days] == [50, 100, 25, 100, 0, 75, 100]
    assert [d.at_goal for d in summary.days] == [False, True, False, True, False, False, True]
    assert [d.is_yesterday for d in summary.days] == [False] * 6 + [True]
    assert summary.days[-1].day == YESTERDAY


def test_missing_days_are_empty_bars_and_left_out_of_the_average() -> None:
    summary = summarize_steps(_daily(PREVIOUS + [8000, None, None, 12000, None, None, None]), 10000)

    assert [d.steps for d in summary.days] == [8000, None, None, 12000, None, None, None]
    assert [d.fill_pct for d in summary.days] == [80, 0, 0, 100, 0, 0, 0]
    assert not any(d.at_goal for d in summary.days if d.steps is None)
    assert summary.yesterday is None
    assert summary.avg_7d == 10000
    assert summary.days_at_goal == 1


def test_a_change_within_ten_percent_is_neutral() -> None:
    summary = summarize_steps(_daily(PREVIOUS + [11000] * 7), 10000)

    assert summary.change == 1000
    assert summary.trend == "neutral"


def test_a_change_beyond_ten_percent_is_up_or_down() -> None:
    up = summarize_steps(_daily(PREVIOUS + [11001] * 7), 10000)
    down = summarize_steps(_daily(PREVIOUS + [8500] * 7), 10000)

    assert (up.change, up.trend) == (1001, "up")
    assert (down.change, down.trend) == (-1500, "down")


def test_no_previous_week_means_no_comparison() -> None:
    summary = summarize_steps(_daily([None] * 7 + [10000] * 7), 10000)

    assert summary.prev_avg_7d is None
    assert summary.change is None
    assert summary.trend is None


def test_no_steps_at_all() -> None:
    summary = summarize_steps(_daily([None] * 14), 10000)

    assert not summary.has_data
    assert summary.avg_7d is None
    assert summary.days_at_goal == 0
    assert len(summary.days) == 7


def test_local_today_is_the_date_in_the_given_timezone() -> None:
    # 23:30 UTC on 1 July is already 2 July in London (BST) but still 1 July in UTC.
    late_evening = datetime(2026, 7, 1, 23, 30, tzinfo=UTC)

    assert local_today("Europe/London", now=late_evening) == date(2026, 7, 2)
    assert local_today("UTC", now=late_evening) == date(2026, 7, 1)
    assert local_today("America/New_York", now=late_evening) == date(2026, 7, 1)


def _briefing_variables(steps: object) -> dict[str, str]:
    return daily_briefing_variables(
        health_today={}, health_trends=[], recent_activities=[], steps=steps
    )


def test_steps_summary_prompt_variable() -> None:
    summary = summarize_steps(
        _daily(PREVIOUS + [9000, 12000, None, 8000, 11000, 4000, 12431]), target=10000
    )

    assert _briefing_variables(summary)["steps_summary"] == (
        "Daily target: 10,000 steps\n"
        "Yesterday: 12,431 steps\n"
        "7-day avg: 9,405/day (3/7 days at or above target)\n"
        "Previous 7-day avg: 10,000/day\n"
        "Change: -595/day (-6.0%, within +/-10%: noise)\n"
        "Daily counts, oldest first: Sat 9,000; Sun 12,000; Mon no data; Tue 8,000; "
        "Wed 11,000; Thu 4,000; Fri 12,431"
    )


def test_steps_summary_flags_a_change_beyond_ten_percent_and_missing_comparison() -> None:
    down = summarize_steps(_daily(PREVIOUS + [8000] * 7), target=10000)
    fresh = summarize_steps(_daily([None] * 13 + [6000]), target=10000)

    assert "Change: -2,000/day (-20.0%)\n" in _briefing_variables(down)["steps_summary"]
    assert _briefing_variables(fresh)["steps_summary"].splitlines()[3:5] == [
        "Previous 7-day avg: no data",
        "Daily counts, oldest first: Sat no data; Sun no data; Mon no data; Tue no data; "
        "Wed no data; Thu no data; Fri 6,000",
    ]


def test_steps_summary_without_any_step_data() -> None:
    no_data = summarize_steps(_daily([None] * 14), target=10000)

    assert _briefing_variables(no_data)["steps_summary"] == "No step data for the last 7 days."
    assert _briefing_variables(None)["steps_summary"] == "No step data for the last 7 days."
