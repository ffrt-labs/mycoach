"""The code-rendered steps strip in the briefing emails (#196)."""

import re
from datetime import date, timedelta
from html.parser import HTMLParser
from unittest.mock import MagicMock, patch

import pytest

from mycoach.coaching.steps import StepsSummary, summarize_steps
from mycoach.config import Settings
from mycoach.email.sender import send_briefing_unavailable, send_daily_briefing

YESTERDAY = date(2026, 11, 13)  # a Friday
PREVIOUS = [10000] * 7


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        email_enabled=True,
        email_from="coach@example.com",
        email_to="user@example.com",
        email_resend_api_key="re_test_key",
        env="test",
    )


def _steps(counts: list[int | None]) -> StepsSummary:
    start = YESTERDAY - timedelta(days=len(counts) - 1)
    daily = [{"date": str(start + timedelta(days=i)), "steps": c} for i, c in enumerate(counts)]
    return summarize_steps(daily, target=10000)


class _Strip(HTMLParser):
    """The strip's day columns: each column's classes, text, and its bar cells' heights."""

    def __init__(self) -> None:
        super().__init__()
        self.columns: list[dict] = []
        self._depth = 0  # nesting inside the current column's <td>

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "td" and "st-col" in classes and not self._depth:
            self.columns.append({"classes": classes, "text": "", "bar": None})
            self._depth = 1
            return
        if not self._depth:
            return
        if tag == "td":
            self._depth += 1
            if "st-bar" in classes:
                self.columns[-1]["bar"] = int(a["height"] or 0)

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag == "td":
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth:
            self.columns[-1]["text"] += data.strip()


def _columns(html: str) -> list[dict]:
    parser = _Strip()
    parser.feed(html)
    return parser.columns


def _chips(html: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", m.group(1) + " " + m.group(2)).strip()
        for m in re.finditer(r'class="chip c-([\w-]+) st-chip"[^>]*>([^<]*)<', html)
    ]


@patch("mycoach.email.sender.send_email", return_value=True)
def _briefing_html(steps: StepsSummary | None, mock_send: MagicMock) -> str:
    send_daily_briefing({"readiness_verdict": "moderate"}, steps=steps, settings=_settings())
    return str(mock_send.call_args[0][2])


@patch("mycoach.email.sender.send_email", return_value=True)
def _unavailable_html(steps: StepsSummary | None, mock_send: MagicMock) -> str:
    send_briefing_unavailable(
        day="2026-11-14",
        failures=3,
        is_broken=True,
        can_still_retry=False,
        steps=steps,
        settings=_settings(),
    )
    return str(mock_send.call_args[0][2])


def test_the_strip_has_a_column_per_day_ending_yesterday_with_goal_ticks() -> None:
    html = _briefing_html(_steps(PREVIOUS + [9000, 12000, 10000, 8000, 11000, 7000, 12431]))

    columns = _columns(html)
    assert [c["text"].replace("✓", "") for c in columns] == [
        "Sat",
        "Sun",
        "Mon",
        "Tue",
        "Wed",
        "Thu",
        "Fri",
    ]
    assert [c["text"].startswith("✓") for c in columns] == [
        False,
        True,
        True,
        False,
        True,
        False,
        True,
    ]
    assert ["st-yesterday" in c["classes"] for c in columns] == [False] * 6 + [True]
    assert "Yesterday: 12,431 steps" in re.sub(r"<[^>]+>", "", html)


def test_bars_fill_in_proportion_to_the_target_and_missing_days_are_empty() -> None:
    html = _briefing_html(_steps(PREVIOUS + [5000, None, 10000, 20000, None, 2500, 7500]))

    bars = [c["bar"] for c in _columns(html)]
    full = bars[2]
    assert full and bars[3] == full  # at or above target: a full bar
    assert bars[0] == full // 2
    assert bars[5] == full // 4
    assert bars[1] is None and bars[4] is None  # no count: no bar at all
    assert "Yesterday: 7,500 steps" in re.sub(r"<[^>]+>", "", html)


def test_chips_show_average_change_and_days_at_goal() -> None:
    html = _briefing_html(_steps(PREVIOUS + [12000] * 7))

    assert _chips(html) == [
        "neutral 7-day avg 12,000/day",
        "good ↑ 2,000/day vs previous 7 days",
        "neutral 7/7 days at goal",
    ]


def test_a_drop_is_an_amber_chip() -> None:
    html = _briefing_html(_steps(PREVIOUS + [8000] * 7))

    assert "warm ↓ 2,000/day vs previous 7 days" in _chips(html)


@pytest.mark.parametrize("week", [[11000] * 7, [9000] * 7])
def test_a_change_within_ten_percent_is_a_blue_chip(week: list[int]) -> None:
    chips = _chips(_briefing_html(_steps(PREVIOUS + week)))

    assert chips[1].startswith("info ")
    assert chips[1].endswith(" 1,000/day vs previous 7 days")


def test_no_previous_week_leaves_out_the_change_chip() -> None:
    chips = _chips(_briefing_html(_steps([None] * 7 + [10000] * 7)))

    assert chips == ["neutral 7-day avg 10,000/day", "neutral 7/7 days at goal"]


def test_no_step_data_or_no_summary_means_no_strip() -> None:
    for steps in (_steps([None] * 14), None):
        html = _briefing_html(steps)
        assert _columns(html) == []
        assert "Yesterday:" not in html


def test_the_strip_uses_no_images_or_svg() -> None:
    html = _briefing_html(_steps(PREVIOUS + [9000, 12000, 10000, 8000, 11000, 7000, 12431]))

    assert "<svg" not in html.lower()
    assert "<img" not in html.lower()


def test_the_no_briefing_email_carries_the_strip_too() -> None:
    html = _unavailable_html(_steps(PREVIOUS + [9000, 12000, 10000, 8000, 11000, 7000, 12431]))

    assert len(_columns(html)) == 7
    assert "Yesterday: 12,431 steps" in re.sub(r"<[^>]+>", "", html)
    assert _unavailable_html(None).count("st-col") == 0
