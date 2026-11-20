"""The weekly plan email: count, week strip, factual notices, intent chips (#234)."""

import re
from datetime import date
from html.parser import HTMLParser
from unittest.mock import MagicMock, patch

import pytest

from mycoach.coaching.life_context import (
    Episode,
    EpisodeKind,
    Equipment,
    IllnessSeverity,
    LifeContext,
)
from mycoach.config import Settings
from mycoach.email.sender import send_weekly_plan

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        email_enabled=True,
        email_from="coach@example.com",
        email_to="user@example.com",
        email_resend_api_key="re_test_key",
        email_smtp_host="",
        env="test",
        app_base_url="https://coach.example.com",
    )


def _session(
    day: int, sport: str, minutes: int | None, intent: str | None, **extra: object
) -> dict:  # type: ignore[type-arg]
    return {
        "day_of_week": day,
        "day_name": DAYS[day],
        "title": f"{sport.title()} session",
        "sport": sport,
        "duration_minutes": minutes,
        "intent": intent,
        "notes": None,
        "details": None,
        **extra,
    }


WEEK = [
    _session(0, "gym", 75, "strength"),
    _session(1, "swimming", 45, "aerobic_base"),
    _session(3, "swimming", 45, "threshold"),
    _session(4, "gym", 65, "strength"),
    _session(6, "running", 40, "aerobic_base"),
]


@pytest.fixture
def mock_send() -> MagicMock:
    with patch("mycoach.email.sender.send_email", return_value=True) as send:
        yield send


def _html(mock_send: MagicMock, **kwargs: object) -> str:
    args: dict[str, object] = {
        "summary": "Easy week",
        "sessions": WEEK,
        "week_start": "2026-11-16",
        "settings": _settings(),
    }
    args.update(kwargs)
    send_weekly_plan(**args)  # type: ignore[arg-type]
    return str(mock_send.call_args[0][2])


class _WeekStrip(HTMLParser):
    """The week strip's day cells: each cell's classes and its text, space-joined."""

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[dict] = []  # type: ignore[type-arg]
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag == "td" and "ws-day" in classes and not self._depth:
            self.cells.append({"classes": classes, "text": ""})
            self._depth = 1
        elif self._depth and tag == "td":
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag == "td":
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth and data.strip():
            self.cells[-1]["text"] = (self.cells[-1]["text"] + " " + data.strip()).strip()


def _strip(html: str) -> list[dict]:  # type: ignore[type-arg]
    parser = _WeekStrip()
    parser.feed(html)
    return parser.cells


def _days_with(cells: list[dict], cls: str) -> list[str]:  # type: ignore[type-arg]
    """The day abbreviations ("THU") of the cells carrying ``cls``."""
    return [c["text"].split()[0] for c in cells if cls in c["classes"]]


def test_week_strip_shows_each_days_sport_minutes_and_intent(mock_send: MagicMock) -> None:
    cells = _strip(_html(mock_send))
    assert [c["text"] for c in cells] == [
        "MON GYM 75′ strength",
        "TUE SWIM 45′ aerobic base",
        "WED —",
        "THU SWIM 45′ threshold",
        "FRI GYM 65′ strength",
        "SAT —",
        "SUN RUN 40′ aerobic base",
    ]


def test_week_strip_outlines_only_the_quality_days(mock_send: MagicMock) -> None:
    cells = _strip(_html(mock_send))
    assert _days_with(cells, "ws-quality") == ["THU"]


MONDAY = date(2026, 11, 16)
SUNDAY_BEFORE = date(2026, 11, 15)


def _life(*episodes: Episode) -> LifeContext:
    return LifeContext(today=SUNDAY_BEFORE, episodes=list(episodes), ramp_back=None)


def _trip(start: date, end: date | None, **kw: object) -> Episode:
    return Episode(id=1, kind=EpisodeKind.TRAVEL, started_on=start, ended_on=end, **kw)  # type: ignore[arg-type]


_NOTICE = re.compile(r'<div class="notice[^"]*"[^>]*>(.*?)</div><!-- /notice -->', re.S)


def _notices(html: str) -> list[str]:
    """Each notice's text, whitespace collapsed."""
    return [
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
        for m in _NOTICE.finditer(html)
    ]


def test_week_strip_marks_the_days_a_trip_covers(mock_send: MagicMock) -> None:
    trip = _trip(date(2026, 11, 18), date(2026, 11, 20), equipment=Equipment.HOTEL_GYM)
    cells = _strip(_html(mock_send, life=_life(trip)))
    assert _days_with(cells, "ws-travel") == ["WED", "THU", "FRI"]
    assert "Travel" in cells[2]["text"]


def test_a_trip_in_the_week_gets_a_notice_of_facts_only(mock_send: MagicMock) -> None:
    trip = _trip(
        date(2026, 11, 18),
        date(2026, 11, 20),
        equipment=Equipment.HOTEL_GYM,
        tz_shift_hours=6.0,
        note="Conference, long days",
    )
    notices = _notices(_html(mock_send, life=_life(trip)))
    assert notices == ["Life context Trip Wed–Fri · hotel gym · 6 h ahead"]


def test_a_trip_running_past_the_week_with_no_return_date_says_so(
    mock_send: MagicMock,
) -> None:
    trip = _trip(date(2026, 11, 21), None, tz_shift_hours=-3.5, no_training=True)
    notices = _notices(_html(mock_send, life=_life(trip)))
    assert notices == [
        "Life context Trip from Sat · 3.5 h behind · no training possible · return date not set"
    ]
    assert _days_with(_strip(mock_send.call_args[0][2]), "ws-travel") == ["SAT", "SUN"]


def test_an_open_illness_gets_a_notice_of_facts_only(mock_send: MagicMock) -> None:
    ill = Episode(
        id=2,
        kind=EpisodeKind.ILLNESS,
        started_on=date(2026, 11, 13),
        ended_on=None,
        severity=IllnessSeverity.MILD,
        note="Sore throat",
    )
    notices = _notices(_html(mock_send, life=_life(ill)))
    assert notices == ["Life context Ill since Fri 13 Nov · mild"]
    assert _days_with(_strip(mock_send.call_args[0][2]), "ws-travel") == []


def test_no_life_context_means_no_notice(mock_send: MagicMock) -> None:
    assert _notices(_html(mock_send, life=_life())) == []


def _gym_day(day: int, *exercises: tuple[str, int, str]) -> dict:  # type: ignore[type-arg]
    return _session(
        day,
        "gym",
        65,
        "strength",
        details={"exercises": [{"name": n, "sets": s, "reps": r} for n, s, r in exercises]},
    )


def test_a_routine_set_count_over_ten_gets_a_routine_error_notice_with_a_fix_link(
    mock_send: MagicMock,
) -> None:
    friday = _gym_day(
        4,
        ("Scapular Pull Up", 3, "8-10"),
        ("Deadlift", 36, "5"),
        ("Pull Up", 36, "5"),
        ("Bulgarian Split Squat", 32, "8-10"),
    )
    html = _html(mock_send, sessions=[friday])
    assert _notices(html) == [
        "Routine error Friday lists Deadlift and Pull Up at 36 sets, "
        "Bulgarian Split Squat at 32. Planned as 3. Fix routine →"
    ]
    assert 'href="https://coach.example.com/routine"' in html


def test_a_plan_stored_as_3_still_reports_the_routines_set_count(mock_send: MagicMock) -> None:
    """The weekly plan stores a routine error as 3 sets and keeps the routine's count."""
    stored = [
        {"name": "Deadlift", "sets": 3, "routine_sets": 36, "reps": "5"},
        {"name": "Leg Raise", "sets": 3, "reps": "10"},
    ]
    friday = _session(4, "gym", 65, "strength", details={"exercises": stored})

    html = _html(mock_send, sessions=[friday])

    assert _notices(html) == [
        "Routine error Friday lists Deadlift at 36 sets. Planned as 3. Fix routine →"
    ]
    assert "3* × 5" in html
    assert "3 × 10" in html
    assert "* Routine says 36 sets — assumed 3 until fixed." in html


def test_the_day_card_shows_the_set_count_the_notice_says_was_planned(
    mock_send: MagicMock,
) -> None:
    html = _html(mock_send, sessions=[_gym_day(4, ("Deadlift", 36, "5"))])
    assert "3* × 5" in html
    assert "36 × 5" not in html


def test_ten_sets_is_not_a_routine_error(mock_send: MagicMock) -> None:
    html = _html(mock_send, sessions=[_gym_day(0, ("Squat", 10, "5"))])
    assert _notices(html) == []
    assert "10 × 5" in html


def test_notices_run_life_context_then_default_schedule_then_routine_error(
    mock_send: MagicMock,
) -> None:
    trip = _trip(date(2026, 11, 18), date(2026, 11, 20))
    html = _html(
        mock_send,
        sessions=[_gym_day(4, ("Deadlift", 36, "5"))],
        availability_source="default",
        life=_life(trip),
    )
    assert [n.split(" ", 2)[:2] for n in _notices(html)] == [
        ["Life", "context"],
        ["Default", "schedule"],
        ["Routine", "error"],
    ]


def _card_chips(html: str) -> list[str]:
    """The day cards' header chips, as "style label" (the strip draws no chips)."""
    return [
        f"{m.group(1)} {m.group(2).strip()}"
        for m in re.finditer(r'<span class="chip c-([\w-]+)">([^<]*)</span>', html)
    ]


def test_each_day_card_carries_its_intent_chip_beside_the_sport_chip(
    mock_send: MagicMock,
) -> None:
    chips = _card_chips(_html(mock_send))
    assert chips == [
        "intent-strength strength",
        "warm gym",
        "intent-aerobic_base aerobic base",
        "info swimming",
        "accent threshold · quality",
        "info swimming",
        "intent-strength strength",
        "warm gym",
        "intent-aerobic_base aerobic base",
        "good running",
    ]
    assert "Quality" not in _html(mock_send)


def test_a_session_without_an_intent_has_no_intent_chip(mock_send: MagicMock) -> None:
    html = _html(mock_send, sessions=[_session(2, "padel", 90, None)])
    assert _card_chips(html) == ["good padel"]


def test_hero_counts_the_planned_sessions_and_sums_their_minutes(mock_send: MagicMock) -> None:
    html = _html(mock_send)
    assert "5 sessions" in html
    assert "4 h 30 min" in html
