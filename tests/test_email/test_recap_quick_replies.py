"""The weekly recap's cause quick replies (#237).

A week that went quiet with nothing logged asks what happened, with buttons that
open the dashboard's Life context form prefilled for the gap (#236). Every link is
a plain GET to the dashboard: nothing is written or synced until the user submits.
"""

import re
from datetime import date
from typing import Any
from unittest.mock import patch

from mycoach.coaching.life_context import Episode
from mycoach.email.sender import send_weekly_recap
from mycoach.models.life_context import EpisodeKind, IllnessSeverity
from tests.test_email.test_weekly_recap_email import _settings, text, texts

BASE = "https://coach.example.com"


def _session(day: str, title: str, status: str = "not_done") -> dict[str, Any]:
    done = status in ("done", "substituted", "adapted_travel")
    return {
        "id": hash(day) % 1000,
        "day": day,
        "sport": "gym",
        "title": title,
        "intent": "strength",
        "completed": done,
        "status": status,
        "on_planned_day": True if done else None,
        "actual_day": day if done else None,
        "substitute": None,
    }


def _week(*sessions: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_sessions": len(sessions),
        "completed_sessions": sum(s["completed"] for s in sessions),
        "substituted_sessions": 0,
        "adapted_sessions": 0,
        "excused_sessions": 0,
        "excused_days": {},
        "sessions": list(sessions),
        "extras": [],
    }


# The 05 artboard: week of 9 Nov, five sessions, none done.
NOTHING_DONE = _week(
    _session("Monday", "Squat Day"),
    _session("Tuesday", "Swim"),
    _session("Thursday", "Swim"),
    _session("Friday", "Deadlift"),
    _session("Sunday", "Run"),
)


def _render(adherence: Any = NOTHING_DONE, **kwargs: Any) -> str:
    kwargs.setdefault("episodes", [])
    kwargs.setdefault("today", date(2026, 11, 16))
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        send_weekly_recap(
            {"week_summary": "Nothing logged."},
            week_start="2026-11-09",
            settings=_settings(),
            adherence=adherence,
            **kwargs,
        )
    return str(mock_send.call_args[0][2])


def _hrefs(html: str) -> list[str]:
    """The quick-reply buttons' links, in order."""
    buttons = re.findall(r'class="quick-reply"[^>]*>\s*<a href="([^"]+)"', html)
    return [h.replace("&amp;", "&") for h in buttons]


class TestQuickReplies:
    def test_a_week_with_nothing_done_asks_what_happened_over_the_gap(self) -> None:
        html = _render()

        assert text(html, "quick-replies-q") == "What happened between 9 and 15 Nov?"
        assert text(html, "quick-replies-sub") == (
            "Opens the dashboard with the dates filled in. Sick days are excused and "
            "leave the count; next week's plan reads it."
        )
        assert texts(html, "quick-reply-label") == ["I was ill", "I was travelling"]
        assert _hrefs(html) == [
            f"{BASE}/?log=illness&from=2026-11-09&to=2026-11-15#life-context",
            f"{BASE}/?log=travel&from=2026-11-09&to=2026-11-15#life-context",
        ]


def _sick(start: date, end: date) -> Episode:
    return Episode(
        id=1,
        kind=EpisodeKind.ILLNESS,
        started_on=start,
        ended_on=end,
        severity=IllnessSeverity.SICK,
    )


# Squat and the Tuesday swim done; the rest not done.
TWO_OF_FIVE = _week(
    _session("Monday", "Squat Day", "done"),
    _session("Tuesday", "Swim", "done"),
    _session("Thursday", "Swim"),
    _session("Friday", "Deadlift"),
    _session("Sunday", "Run"),
)


class TestWhenShown:
    def test_a_partly_done_week_with_health_data_asks_nothing(self) -> None:
        assert texts(_render(TWO_OF_FIVE, health_days_missing=0), "quick-replies") == []

    def test_a_partly_done_week_with_one_day_of_health_data_missing_asks_nothing(self) -> None:
        assert texts(_render(TWO_OF_FIVE, health_days_missing=1), "quick-replies") == []

    def test_a_partly_done_week_missing_two_days_of_health_data_asks(self) -> None:
        html = _render(TWO_OF_FIVE, health_days_missing=2)

        assert text(html, "quick-replies-q") == "What happened between 12 and 15 Nov?"
        assert _hrefs(html)[0] == (
            f"{BASE}/?log=illness&from=2026-11-12&to=2026-11-15#life-context"
        )

    def test_a_partly_done_week_with_unknown_health_data_asks_nothing(self) -> None:
        assert texts(_render(TWO_OF_FIVE, health_days_missing=None), "quick-replies") == []

    def test_a_fully_done_week_asks_nothing_even_without_health_data(self) -> None:
        week = _week(
            _session("Monday", "Squat Day", "done"), _session("Friday", "Deadlift", "done")
        )

        assert texts(_render(week, health_days_missing=7), "quick-replies") == []

    def test_a_week_with_a_logged_episode_asks_nothing(self) -> None:
        html = _render(episodes=[_sick(date(2026, 11, 13), date(2026, 11, 14))])

        assert texts(html, "quick-replies") == []

    def test_a_week_whose_episodes_could_not_be_read_asks_nothing(self) -> None:
        assert texts(_render(episodes=None), "quick-replies") == []

    def test_a_week_with_no_plan_asks_nothing(self) -> None:
        assert texts(_render(None, health_days_missing=7), "quick-replies") == []


class TestPrefillRange:
    def test_the_range_spans_the_first_to_the_last_not_done_session(self) -> None:
        week = _week(
            _session("Monday", "Squat Day", "done"),
            _session("Tuesday", "Swim"),
            _session("Thursday", "Swim"),
            _session("Saturday", "Long run", "done"),
        )

        html = _render(week, health_days_missing=3)

        assert text(html, "quick-replies-q") == "What happened between 10 and 12 Nov?"
        assert _hrefs(html)[:2] == [
            f"{BASE}/?log=illness&from=2026-11-10&to=2026-11-12#life-context",
            f"{BASE}/?log=travel&from=2026-11-10&to=2026-11-12#life-context",
        ]

    def test_one_not_done_session_names_its_day(self) -> None:
        week = _week(_session("Wednesday", "Swim"))

        assert text(_render(week), "quick-replies-q") == "What happened on 11 Nov?"

    def test_illness_ends_yesterday_at_the_latest_travel_does_not(self) -> None:
        # A recap sent on the Sunday it covers.
        html = _render(today=date(2026, 11, 15))

        assert _hrefs(html) == [
            f"{BASE}/?log=illness&from=2026-11-09&to=2026-11-14#life-context",
            f"{BASE}/?log=travel&from=2026-11-09&to=2026-11-15#life-context",
        ]

    def test_a_gap_spanning_two_months_names_both(self) -> None:
        week = _week(_session("Thursday", "Swim"), _session("Sunday", "Run"))
        with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
            send_weekly_recap(
                {},
                week_start="2026-10-26",
                settings=_settings(),
                adherence=week,
                episodes=[],
                today=date(2026, 11, 2),
            )

        html = str(mock_send.call_args[0][2])
        assert text(html, "quick-replies-q") == "What happened between 29 Oct and 1 Nov?"


class TestWatchButton:
    def test_two_days_without_health_data_offers_the_watch_sync(self) -> None:
        html = _render(health_days_missing=2)

        assert texts(html, "quick-reply-label") == [
            "I was ill",
            "I was travelling",
            "Watch didn't sync",
        ]
        assert _hrefs(html)[2] == f"{BASE}/"

    def test_less_than_two_days_without_health_data_leaves_the_watch_out(self) -> None:
        html = _render(health_days_missing=1)

        assert "Watch didn't sync" not in texts(html, "quick-reply-label")

    def test_unknown_health_data_leaves_the_watch_out(self) -> None:
        html = _render(health_days_missing=None)

        assert len(texts(html, "quick-reply-label")) == 2

    def test_a_failed_garmin_login_asks_to_reconnect_instead(self) -> None:
        html = _render(health_days_missing=4, garmin_auth_failed=True)

        assert texts(html, "quick-reply-label")[2] == "Reconnect Garmin"
        assert _hrefs(html)[2] == f"{BASE}/"


def test_no_quick_reply_link_writes_or_syncs() -> None:
    """Mail scanners prefetch links: every button only opens the dashboard."""
    html = _render(health_days_missing=7, garmin_auth_failed=True)

    for href in _hrefs(html):
        assert href.startswith(f"{BASE}/")
        path = href.removeprefix(BASE).split("?")[0].split("#")[0]
        assert path == "/"
