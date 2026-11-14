"""The body summary the email and the prompt share (ticket #198).

Worked examples from docs/research/health-metrics.md §4: time-aware EMA (alpha 0.1), kg/week
over 28 days once there are >=4 readings in 14 days, stale after 14 days, nudges at 7 days
(weight) and 30 days (waist), waist change shown only at >=2 cm.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from mycoach.coaching.body import summarize_body, when_text

LONDON = ZoneInfo("Europe/London")
# Saturday 14 Nov 2026, 09:30 in London (GMT in November).
NOW = datetime(2026, 11, 14, 9, 30, tzinfo=UTC)


def _m(days_ago: float, weight: float | None = None, waist: float | None = None,
       note: str | None = None) -> dict[str, Any]:  # fmt: skip
    at = datetime(2026, 11, 14, 7, 0) - timedelta(days=days_ago)
    return {"id": 1, "measured_at": str(at), "weight_kg": weight, "waist_cm": waist,
            "note": note, "source": "manual"}  # fmt: skip


def _summary(measurements: list[dict[str, Any]], height_cm: float | None = None):  # type: ignore[no-untyped-def]
    return summarize_body(measurements, height_cm=height_cm, now=NOW, tz=LONDON)


def test_no_measurements_is_the_empty_state() -> None:
    summary = _summary([])

    assert summary.empty
    assert summary.weight is None
    assert summary.waist is None


def test_the_latest_weight_and_when_it_was_taken() -> None:
    summary = _summary([_m(2, weight=82.4), _m(5, weight=83.0)])

    assert not summary.empty
    assert summary.weight is not None
    assert summary.weight.latest_kg == 82.4
    assert summary.weight.days_ago == 2
    assert summary.weight.when == "measured Thu (2 days ago)"


@pytest.mark.parametrize(
    ("days", "text"),
    [
        (0, "measured today"),
        (1, "measured yesterday"),
        (6, "measured Sun (6 days ago)"),
        (9, "measured 5 Nov (9 days ago)"),
    ],
)
def test_when_text(days: int, text: str) -> None:
    today = NOW.date()
    assert when_text(today - timedelta(days=days), today) == text


def test_the_local_day_decides_how_long_ago() -> None:
    # 23:30 UTC on Thu 12 Nov is already Fri 13 Nov in Berlin.
    late = {"id": 1, "measured_at": "2026-11-12 23:30:00", "weight_kg": 80.0,
            "waist_cm": None, "note": None, "source": "manual"}  # fmt: skip

    summary = summarize_body([late], height_cm=None, now=NOW, tz=ZoneInfo("Europe/Berlin"))

    assert summary.weight is not None
    assert summary.weight.days_ago == 1


# ── Weight trend ────────────────────────────────────────────────────


def test_no_trend_with_fewer_than_four_readings_in_fourteen_days() -> None:
    summary = _summary([_m(1, weight=80.0), _m(3, weight=80.5), _m(5, weight=81.0),
                        _m(20, weight=82.0)])  # fmt: skip

    assert summary.weight is not None
    assert summary.weight.trend_kg is None
    assert summary.weight.rate_kg_per_week is None


def test_several_readings_on_one_day_count_once() -> None:
    summary = _summary([_m(1, weight=80.0), _m(1.1, weight=80.2), _m(1.2, weight=80.4),
                        _m(3, weight=80.5)])  # fmt: skip

    assert summary.weight is not None
    assert summary.weight.trend_kg is None


def test_the_trend_is_an_ema_with_alpha_a_tenth_per_day() -> None:
    # Hand-worked: seed 80; 80.1; 80.29; 80.561.
    summary = _summary([_m(3, weight=80.0), _m(2, weight=81.0), _m(1, weight=82.0),
                        _m(0, weight=83.0)])  # fmt: skip

    assert summary.weight is not None
    assert summary.weight.trend_kg == pytest.approx(80.561)


def test_the_ema_weighs_a_gap_by_its_length() -> None:
    # A 2-day gap: alpha = 1 - 0.9**2 = 0.19, so 80 + 0.19 * 2 = 80.38.
    summary = _summary([_m(4, weight=80.0), _m(3, weight=80.0), _m(2, weight=80.0),
                        _m(0, weight=82.0)])  # fmt: skip

    assert summary.weight is not None
    assert summary.weight.trend_kg == pytest.approx(80.38)


def test_a_long_gap_all_but_resets_the_trend() -> None:
    # 90 days of silence: the old 95 kg keeps 0.9**86 (~0.01%) of its weight.
    summary = _summary([_m(90, weight=95.0), _m(4, weight=80.0), _m(3, weight=80.0),
                        _m(2, weight=80.0), _m(1, weight=80.0)])  # fmt: skip

    assert summary.weight is not None
    assert summary.weight.trend_kg == pytest.approx(80.0, abs=0.01)


def test_the_rate_is_kg_per_week_over_the_last_28_days() -> None:
    # Losing exactly 0.1 kg a day for four weeks is 0.7 kg a week; a reading from before the
    # window (a heavy day 40 days back) does not bend it.
    readings = [_m(d, weight=80.0 + 0.1 * d) for d in range(0, 28)]
    summary = _summary([*readings, _m(40, weight=99.0)])

    assert summary.weight is not None
    assert summary.weight.rate_kg_per_week == pytest.approx(-0.7)


def test_a_trend_from_a_stale_fortnight_is_kept_but_marked_stale() -> None:
    old = [_m(d, weight=80.0) for d in (15, 16, 17, 18)]

    summary = _summary(old)

    assert summary.weight is not None
    assert summary.weight.trend_kg == pytest.approx(80.0)
    assert summary.weight.stale


def test_a_recent_trend_is_not_stale() -> None:
    summary = _summary([_m(d, weight=80.0) for d in (13, 12, 11, 10)])

    assert summary.weight is not None
    assert not summary.weight.stale


@pytest.mark.parametrize(("days", "due"), [(6, False), (7, True), (30, True)])
def test_time_to_weigh_in_after_seven_days(days: int, due: bool) -> None:
    assert _summary([_m(days, weight=80.0)]).weigh_in_due is due


def test_no_weight_ever_but_a_waist_means_a_weigh_in_is_due() -> None:
    assert _summary([_m(1, waist=90.0)]).weigh_in_due


# ── Waist ───────────────────────────────────────────────────────────


def test_the_latest_waist_and_its_ratio_to_height() -> None:
    summary = _summary([_m(3, waist=91.0), _m(40, waist=94.0)], height_cm=180.0)

    assert summary.waist is not None
    assert summary.waist.latest_cm == 91.0
    assert summary.waist.when == "measured Wed (3 days ago)"
    assert summary.waist.ratio == pytest.approx(0.506, abs=0.001)


def test_no_ratio_without_a_height() -> None:
    summary = _summary([_m(3, waist=91.0)])

    assert summary.waist is not None
    assert summary.waist.ratio is None


def test_a_four_week_change_of_two_cm_or_more_shows() -> None:
    summary = _summary([_m(0, waist=89.5), _m(28, waist=92.0)])

    assert summary.waist is not None
    assert summary.waist.change_cm == pytest.approx(-2.5)
    assert summary.waist.change_meaningful


def test_a_change_under_two_cm_is_no_change() -> None:
    summary = _summary([_m(0, waist=90.5), _m(30, waist=92.0)])

    assert summary.waist is not None
    assert summary.waist.change_cm == pytest.approx(-1.5)
    assert not summary.waist.change_meaningful


def test_the_baseline_is_the_reading_closest_to_four_weeks_back() -> None:
    summary = _summary([_m(0, waist=88.0), _m(10, waist=89.0), _m(27, waist=91.0),
                        _m(41, waist=95.0)])  # fmt: skip

    assert summary.waist is not None
    assert summary.waist.change_cm == pytest.approx(-3.0)


def test_no_change_without_a_reading_two_to_six_weeks_earlier() -> None:
    summary = _summary([_m(0, waist=88.0), _m(10, waist=92.0), _m(60, waist=95.0)])

    assert summary.waist is not None
    assert summary.waist.change_cm is None
    assert not summary.waist.change_meaningful


@pytest.mark.parametrize(("days", "due"), [(29, False), (30, True)])
def test_a_waist_check_is_due_after_thirty_days(days: int, due: bool) -> None:
    assert _summary([_m(days, waist=90.0)]).waist_check_due is due


def test_no_waist_ever_but_a_weight_means_a_waist_check_is_due() -> None:
    assert _summary([_m(1, weight=80.0)]).waist_check_due


# ── Notes ───────────────────────────────────────────────────────────


def test_notes_from_the_last_fortnight_ride_along_newest_first() -> None:
    summary = _summary([_m(1, weight=82.0, note="salty ramen"), _m(3, weight=80.0),
                        _m(5, weight=80.1, note="travel"),
                        _m(20, weight=80.0, note="old")])  # fmt: skip

    assert summary.recent_notes == [("Fri 13 Nov", "salty ramen"), ("Mon 9 Nov", "travel")]
