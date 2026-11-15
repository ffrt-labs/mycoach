"""The Body block of the morning email, rendered by code from the DB (ticket #198).

It shows in the daily briefing and in the "No briefing today" email, so the numbers reach
the athlete even when the LLM fails (#191 decision 3).
"""

import re
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from mycoach.coaching.body import BodySummary, summarize_body
from mycoach.config import Settings
from mycoach.email.sender import send_briefing_unavailable, send_daily_briefing

NOW = datetime(2026, 11, 14, 9, 30, tzinfo=UTC)
BRIEFING = {"readiness_verdict": "moderate", "recovery_status": "OK"}


def _settings() -> Settings:
    return Settings(
        email_enabled=True,
        email_from="coach@example.com",
        email_to="user@example.com",
        email_resend_api_key="re_test_key",
        email_smtp_host="",
        env="test",
        app_base_url="https://coach.example",
    )


def _m(days_ago: float, weight: float | None = None, waist: float | None = None) -> dict[str, Any]:
    at = datetime(2026, 11, 14, 7, 0) - timedelta(days=days_ago)
    return {"id": 1, "measured_at": str(at), "weight_kg": weight, "waist_cm": waist,
            "note": None, "source": "manual"}  # fmt: skip


def _body(measurements: list[dict[str, Any]], height_cm: float | None = 180.0) -> BodySummary:
    return summarize_body(
        measurements, height_cm=height_cm, now=NOW, tz=ZoneInfo("Europe/London")
    )


def _text(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip())


@patch("mycoach.email.sender.send_email", return_value=True)
def _briefing_html(body: BodySummary | None, mock_send: MagicMock) -> str:
    send_daily_briefing(BRIEFING, settings=_settings(), body=body)
    return str(mock_send.call_args[0][2])


def test_no_measurements_is_one_line_linking_to_the_body_page() -> None:
    html = _briefing_html(_body([]))

    assert 'href="https://coach.example/body"' in html
    assert "No weight or waist logged yet" in _text(html)
    assert "Time to weigh in" not in _text(html)


def test_no_block_when_no_summary_is_given() -> None:
    assert "coach.example/body" not in _briefing_html(None)


def test_latest_weight_with_when_and_no_trend_below_the_guard() -> None:
    text = _text(_briefing_html(_body([_m(2, weight=82.4)])))

    assert "82.4 kg" in text
    assert "measured Thu (2 days ago)" in text
    assert "kg/wk" not in text


def test_weight_trend_and_rate_once_there_are_enough_readings() -> None:
    readings = [_m(d, weight=80.0 + 0.1 * d) for d in range(0, 28)]

    text = _text(_briefing_html(_body(readings)))

    assert "Trend" in text
    assert "−0.70 kg/wk" in text
    assert "stale" not in text


def test_a_stale_weight_is_marked_and_a_weigh_in_nudged() -> None:
    text = _text(_briefing_html(_body([_m(d, weight=80.0) for d in (15, 16, 17, 18)])))

    assert "Weight stale" in text
    assert "kg/wk" not in text
    assert "⏰ Time to weigh in" in text


def test_a_two_week_old_weigh_in_alone_is_marked_stale() -> None:
    text = _text(_briefing_html(_body([_m(14, weight=80.0)])))

    assert "Weight stale" in text
    assert "80.0 kg" in text


def test_waist_with_ratio_and_a_meaningful_change() -> None:
    text = _text(_briefing_html(_body([_m(0, waist=89.5), _m(28, waist=92.0)])))

    assert "89.5 cm" in text
    assert "measured today" in text
    assert "0.50" in text
    assert "target < 0.50" in text
    assert "−2.5 cm in 4 weeks" in text


def test_a_small_waist_change_reads_no_change() -> None:
    text = _text(_briefing_html(_body([_m(0, waist=90.5), _m(30, waist=92.0)])))

    assert "no change" in text
    assert "−1.5" not in text


def test_a_waist_check_is_nudged_after_thirty_days() -> None:
    text = _text(_briefing_html(_body([_m(1, weight=80.0), _m(31, waist=90.0)])))

    assert "📏 Waist check due" in text


def test_no_ratio_without_height() -> None:
    text = _text(_briefing_html(_body([_m(0, waist=90.0)], height_cm=None)))

    assert "90.0 cm" in text
    assert "target" not in text


@patch("mycoach.email.sender.send_email", return_value=True)
def test_the_no_briefing_email_carries_the_block_too(mock_send: MagicMock) -> None:
    send_briefing_unavailable(
        day="Saturday 14 November",
        failures=3,
        is_broken=True,
        can_still_retry=False,
        settings=_settings(),
        body=_body([_m(2, weight=82.4)]),
    )

    text = _text(str(mock_send.call_args[0][2]))
    assert "No briefing yet" in text
    assert "82.4 kg" in text
