"""The ``body_summary`` variable of the daily briefing prompt (ticket #198).

Code computes every number; the prompt hands them to the LLM with the context it needs to
apply the rules (trend not a single day, noise thresholds, waist-to-height, nudges).
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from mycoach.coaching.body import BodySummary, summarize_body
from mycoach.coaching.prompt_builder import daily_briefing_variables

NOW = datetime(2026, 11, 14, 9, 30, tzinfo=UTC)


def _m(days_ago: float, weight: float | None = None, waist: float | None = None,
       note: str | None = None) -> dict[str, Any]:  # fmt: skip
    at = datetime(2026, 11, 14, 7, 0) - timedelta(days=days_ago)
    return {"id": 1, "measured_at": str(at), "weight_kg": weight, "waist_cm": waist,
            "note": note, "source": "manual"}  # fmt: skip


def _body(measurements: list[dict[str, Any]], height_cm: float | None = 180.0) -> BodySummary:
    return summarize_body(measurements, height_cm=height_cm, now=NOW, tz=ZoneInfo("Europe/London"))


def _text(body: BodySummary | None) -> str:
    return daily_briefing_variables(
        health_today={}, health_trends=[], recent_activities=[], body=body
    )["body_summary"]


def test_without_measurements() -> None:
    assert _text(None) == "No body measurements logged."
    assert _text(_body([])) == "No body measurements logged."


def test_a_trend_with_its_rate() -> None:
    text = _text(_body([_m(d, weight=80.0 + 0.1 * d) for d in range(0, 28)]))

    assert "Latest weight: 80.0 kg, measured today." in text
    assert "kg/week over 28 days: -0.70" in text


def test_a_stale_latest_weight_is_marked() -> None:
    text = _text(_body([_m(d, weight=80.0) for d in (15, 16, 17, 18)]))

    assert (
        "Latest weight: 80.0 kg, measured 30 Oct (15 days ago). "
        "STALE: last weigh-in 15 days ago."
    ) in text
    assert "Weight trend: not enough weigh-ins (needs 4+ days in the last 14)." in text


def test_no_trend_says_why() -> None:
    text = _text(_body([_m(2, weight=82.4)]))

    assert "Weight trend: not enough weigh-ins (needs 4+ days in the last 14)." in text


def test_waist_ratio_change_nudges_and_notes() -> None:
    text = _text(_body([_m(0, waist=89.5, note="travel"), _m(28, waist=92.0), _m(9, weight=80.0)]))

    assert "Latest waist: 89.5 cm, measured today." in text
    assert "Waist-to-height: 0.50 (target < 0.50)." in text
    assert "4-week waist change: -2.5 cm." in text
    assert "Nudge due: weigh-in (none for 9 days)." in text
    assert "Recent notes: Sat 14 Nov: travel" in text


def test_a_small_waist_change_is_reported_as_noise() -> None:
    text = _text(_body([_m(0, waist=90.5), _m(30, waist=92.0)]))

    assert "4-week waist change: none (-1.5 cm is within measurement noise)." in text
