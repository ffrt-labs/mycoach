"""Seeded trace IDs and week sessions.

Expected IDs are independent fixtures: ``printf '<seed>' | sha256sum | cut -c1-32``.
They are hardcoded so the tests can't agree with the code by construction.
"""

from datetime import UTC, date, datetime

from mycoach.observability.trace_ids import (
    daily_briefing_seed,
    post_workout_seed,
    regen_seed,
    trace_id,
    week_session_id,
    weekly_plan_seed,
    weekly_recap_seed,
)


def test_weekly_plan_seed_and_trace_id() -> None:
    assert weekly_plan_seed(42) == "mycoach:weekly-plan:42"
    assert trace_id(weekly_plan_seed(42)) == "665f9fe7501f90dd1e2ee79f492b32ed"


def test_daily_briefing_seed_and_trace_id() -> None:
    seed = daily_briefing_seed(1, date(2024, 6, 10))
    assert seed == "mycoach:daily-briefing:1:2024-06-10"
    assert trace_id(seed) == "ef2156ef5a01aa2158bfb9e1b4a8fb29"


def test_post_workout_seed_and_trace_id() -> None:
    assert post_workout_seed(7) == "mycoach:post-workout:7"
    assert trace_id(post_workout_seed(7)) == "66b3c0a4d02bad6cd5f26a684a8c2008"


def test_weekly_recap_seed_and_trace_id() -> None:
    seed = weekly_recap_seed(1, date(2024, 6, 10))
    assert seed == "mycoach:weekly-recap:1:2024-06-10"
    assert trace_id(seed) == "0770cfbac563ba742e33b5e00d781155"


def test_regen_seed_appends_the_utc_timestamp() -> None:
    seed = regen_seed(
        "mycoach:daily-briefing:1:2024-06-10", datetime(2026, 11, 8, 9, 30, tzinfo=UTC)
    )
    assert seed == "mycoach:daily-briefing:1:2024-06-10:regen:20261108T093000Z"
    assert trace_id(seed) == "073b50b51cb3b6a079f0f0b19d5cbf3e"


def test_week_session_is_keyed_by_the_monday_of_the_day_it_is_about() -> None:
    assert week_session_id(1, date(2024, 6, 13)) == "mycoach:week:1:2024-06-10"
    assert week_session_id(1, date(2024, 6, 10)) == "mycoach:week:1:2024-06-10"
    assert week_session_id(1, date(2024, 6, 16)) == "mycoach:week:1:2024-06-10"
