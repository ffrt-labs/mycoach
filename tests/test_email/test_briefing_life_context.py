"""The daily briefing's code-rendered Life context banner and Open this week (#233)."""

import html as html_lib
import re
from datetime import date
from typing import Any
from unittest.mock import patch

from mycoach.coaching.context import OpenSession, OpenThisWeek
from mycoach.coaching.life_context import Episode, LifeContext, RampBack, RecoverySignals
from mycoach.config import Settings
from mycoach.email.sender import send_daily_briefing
from mycoach.models.life_context import EpisodeKind, Equipment, IllnessSeverity

BRIEFING = None  # no Coach notes: these blocks are code-rendered either way (#241)
THURSDAY = date(2026, 11, 19)


def _settings() -> Settings:
    return Settings(
        email_enabled=True,
        email_from="coach@example.com",
        email_to="user@example.com",
        email_resend_api_key="re_test_key",
        email_smtp_host="",
        app_base_url="https://coach.example.com",
        env="test",
    )


def _render(**views: Any) -> str:
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        send_daily_briefing(BRIEFING, _settings(), **views)
    return str(mock_send.call_args[0][2])


def _text(html: str) -> str:
    """The email's visible text, whitespace collapsed."""
    html = re.sub(r"<(style|head)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _open(
    day: str,
    title: str,
    status: str = "upcoming",
    sport: str = "gym",
    intent: str | None = None,
    minutes: int | None = None,
) -> OpenSession:
    return {
        "id": hash(title) % 1000,
        "day": day,
        "day_of_week": 0,
        "status": status,  # type: ignore[typeddict-item]
        "sport": sport,
        "track": None,
        "title": title,
        "intent": intent,
        "duration_minutes": minutes,
        "details": None,
        "notes": None,
    }


def _week(sessions: list[OpenSession], days_remaining: int) -> OpenThisWeek:
    return {"sessions": sessions, "days_remaining": days_remaining}


# --- Open this week -------------------------------------------------------------------


def test_open_this_week_counts_what_is_left_and_the_days_to_do_it() -> None:
    week = _week(
        [
            _open("Monday", "Squat Day", "overdue", "gym", "strength", 75),
            _open("Saturday", "Easy aerobic run", "upcoming", "running", "aerobic_base", 40),
        ],
        days_remaining=4,
    )

    text = _text(_render(open_this_week=week))

    assert "Open this week" in text
    assert "2 left · 4 days remaining" in text
    assert "Squat Day Gym · strength · 75 min" in text
    assert "Easy aerobic run Running · aerobic base · 40 min" in text


def test_open_rows_chip_an_overdue_session_today_and_an_upcoming_weekday() -> None:
    week = _week(
        [
            _open("Monday", "Squat Day", "overdue"),
            _open("Thursday", "Easy spin", "today"),
            _open("Saturday", "Long run", "upcoming"),
        ],
        days_remaining=4,
    )

    html = _render(open_this_week=week)

    assert re.search(r'class="chip c-watch">Planned day passed<', html)
    assert re.search(r'class="chip c-neutral">Today<', html)
    assert re.search(r'class="chip c-neutral">Sat<', html)


def test_a_row_without_intent_or_minutes_shows_only_its_sport() -> None:
    week = _week([_open("Saturday", "Long run", sport="running")], days_remaining=4)

    text = _text(_render(open_this_week=week))

    assert "Long run Running Sat" in text


def test_more_open_sessions_than_days_left_shows_the_overload_notice() -> None:
    sessions = [_open("Saturday", f"Session {i}") for i in range(3)]

    assert "More sessions open than days left." in _text(
        _render(open_this_week=_week(sessions, days_remaining=2))
    )
    assert "More sessions open than days left." not in _text(
        _render(open_this_week=_week(sessions, days_remaining=3))
    )


def test_nothing_open_says_every_planned_session_is_done() -> None:
    html = _render(open_this_week=_week([], days_remaining=1))

    assert "0 left · 1 day remaining" in _text(html)
    assert re.search(r"c-good[^>]*>Every planned session is done\.<", html)


def test_no_open_this_week_leaves_the_block_out() -> None:
    assert "Open this week" not in _text(_render(open_this_week=None))


# --- Life context banner --------------------------------------------------------------

LINK = "https://coach.example.com/#life-context"


def _illness(started_on: date, severity: str, ended_on: date | None = None) -> Episode:
    return Episode(
        id=1,
        kind=EpisodeKind.ILLNESS,
        started_on=started_on,
        ended_on=ended_on,
        severity=IllnessSeverity(severity),
    )


def _trip(
    started_on: date,
    ended_on: date | None = None,
    equipment: str = "hotel_gym",
    tz: float | None = None,
    no_training: bool = False,
) -> Episode:
    return Episode(
        id=2,
        kind=EpisodeKind.TRAVEL,
        started_on=started_on,
        ended_on=ended_on,
        equipment=Equipment(equipment),
        tz_shift_hours=tz,
        no_training=no_training,
    )


def _life(*episodes: Episode, ramp_back: RampBack | None = None) -> LifeContext:
    return LifeContext(today=THURSDAY, episodes=list(episodes), ramp_back=ramp_back)


def _banner(html: str) -> str:
    """The banner's markup, from its opening tag to the readiness eyebrow."""
    match = re.search(r'<div class="banner.*?(?=Today\'s readiness)', html, flags=re.S)
    assert match, "no banner"
    return match.group(0)


def test_sick_banner_says_rest_and_that_nothing_is_owed() -> None:
    banner = _banner(_render(life=_life(_illness(date(2026, 11, 18), "sick"))))

    assert "c-alert" in banner
    assert "Ill since Wed 18 Nov · day 2" in _text(banner)
    assert "Rest today. Nothing to make up — sick days are excused." in _text(banner)
    assert f'href="{LINK}"' in banner
    assert "Feeling better →" in _text(banner)


def test_mild_banner_says_light_and_short_or_rest() -> None:
    banner = _banner(_render(life=_life(_illness(THURSDAY, "mild"))))

    assert "c-watch" in banner
    assert "Mild cold · day 1" in _text(banner)
    assert "Light and short at most, or rest." in _text(banner)
    assert "skip" not in _text(banner)
    assert "Update →" in _text(banner)


def test_ramp_back_banner_shows_the_day_the_load_and_a_segment_bar() -> None:
    ramp = RampBack(episode_id=1, day=2, length=5, load_pct=60, ends_on=date(2026, 11, 22))

    banner = _banner(_render(life=_life(ramp_back=ramp)))

    assert "c-good" in banner
    assert "Returning from illness · day 2/5 · ~60% of normal load" in _text(banner)
    assert banner.count("rb-on") == 2
    assert banner.count("rb-off") == 3
    assert "consider one more easy day" not in _text(banner)


def test_last_ramp_back_day_with_low_signals_suggests_one_more_easy_day() -> None:
    ramp = RampBack(
        episode_id=1,
        day=5,
        length=5,
        load_pct=90,
        ends_on=THURSDAY,
        extend_because=RecoverySignals(hrv_below_baseline=True, resting_hr_elevated=False),
    )

    text = _text(_banner(_render(life=_life(ramp_back=ramp))))

    assert "HRV still below your range: consider one more easy day." in text


def test_travelling_banner_names_equipment_time_zone_and_return_day() -> None:
    trip = _trip(date(2026, 11, 17), date(2026, 11, 27), "hotel_gym", tz=6)

    banner = _banner(_render(life=_life(trip)))

    assert "c-info" in banner
    assert "Hotel gym · 6 h ahead of home · back Fri 27 Nov" in _text(banner)
    assert "jet-lag principle applies today" in _text(banner)
    assert "Edit trip →" in _text(banner)


def test_a_small_time_zone_shift_brings_no_jet_lag_line() -> None:
    trip = _trip(date(2026, 11, 17), equipment="full_gym", tz=-2)

    text = _text(_banner(_render(life=_life(trip))))

    assert "Full gym · 2 h behind home" in text
    assert "back" not in text
    assert "jet-lag" not in text


def test_no_training_travel_day_says_rest_and_that_it_can_excuse_a_session() -> None:
    trip = _trip(date(2026, 11, 17), no_training=True)

    banner = _banner(_render(life=_life(trip)))

    assert "c-neutral" in banner
    assert "No training possible today" in _text(banner)
    assert "Rest. Today can excuse one not-done session; nothing to make up." in _text(banner)
    assert "Back home →" in _text(banner)


def test_an_illness_on_a_trip_shows_only_the_illness() -> None:
    life = _life(_trip(date(2026, 11, 17), tz=6), _illness(THURSDAY, "sick"))

    html = _render(life=life)

    assert html.count('<div class="banner') == 1
    assert "Ill since Thu 19 Nov · day 1" in _text(_banner(html))
    assert "Hotel gym" not in _text(html)


def test_no_life_context_has_no_banner() -> None:
    assert '<div class="banner' not in _render(life=_life())
    assert '<div class="banner' not in _render(life=None)


# --- Footer ---------------------------------------------------------------------------


def test_footer_asks_to_tell_the_coach_about_illness_or_travel() -> None:
    html = _render()

    assert re.search(
        rf'<a href="{re.escape(LINK)}"[^>]*>Feeling unwell or travelling\? Tell the coach →</a>',
        html,
    )
