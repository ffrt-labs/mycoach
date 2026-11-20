"""The daily briefing on the v5 contract: Coach notes in slots, the rest from code (#241)."""

import copy
import html as html_lib
import logging
import re
from typing import Any
from unittest.mock import patch

import pytest

from mycoach.coaching.context import OpenSession, OpenThisWeek
from mycoach.config import Settings
from mycoach.email.sender import send_daily_briefing

NOTES: dict[str, Any] = {
    "readiness": "go_hard",
    "headline": {
        "tone": "good",
        "summary": "Nothing planned for today and you're fresh: a good day for Squat Day.",
    },
    "options": [
        {
            "open_session_id": 11,
            "title": None,
            "steps": ["Last working weights, RPE 7–8", "Log every load"],
            "why": "Fresh legs today, and Thursday keeps its swim.",
            "recommended": True,
        },
        {
            "open_session_id": 14,
            "title": None,
            "steps": ["8×100 at RPE 6, 20 s rest"],
            "why": "If you would rather swim today; then lift on Thursday.",
            "recommended": False,
        },
    ],
    "guardrail": {"when": "HRV 7-day avg below range or RHR > 45", "then": "Easy swim instead"},
    "sleep_note": "Best REM in 3 days. Awake 1 min.",
    "tonight": [
        {"tone": "neutral", "text": "Bed 22:00–22:30"},
        {"tone": "neutral", "text": "Screens off 60 min before bed"},
    ],
    "key_metrics": {"sleep_score": 97, "resting_hr": 43},
    "analysis_md": "## Today\nGo hard.",
}


def _open(
    id: int,
    day: str,
    title: str,
    status: str = "upcoming",
    sport: str = "gym",
    intent: str | None = None,
    minutes: int | None = None,
) -> OpenSession:
    return {
        "id": id,
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


WEEK: OpenThisWeek = {
    "sessions": [
        _open(11, "Monday", "Squat Day", "overdue", "gym", "strength", 75),
        _open(14, "Thursday", "Threshold intervals swim", "upcoming", "swimming", "threshold", 45),
        _open(15, "Friday", "Deadlift", "upcoming", "gym", "strength", 65),
    ],
    "days_remaining": 5,
}

SNAPSHOT = {
    "sleep_score": 97,
    "sleep_duration_minutes": 543,
    "sleep_deep_minutes": 133,
    "sleep_rem_minutes": 156,
    "sleep_light_minutes": 253,
    "sleep_awake_minutes": 1,
    "resting_hr": 43,
    "hrv_7day_avg": 52.0,
    "hrv_status": 48.0,
}


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


def _render(content: dict[str, Any] | None = NOTES, **views: Any) -> str:
    views.setdefault("open_this_week", WEEK)
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        send_daily_briefing(content, _settings(), **views)
    return str(mock_send.call_args[0][2])


def _notes(**changes: Any) -> dict[str, Any]:
    notes = copy.deepcopy(NOTES)
    notes.update(changes)
    return notes


def _text(html: str) -> str:
    html = re.sub(r"<(style|head)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _hero(html: str) -> str:
    return html.split('class="px hero"', 1)[1].split(">", 1)[1].split("</td>", 1)[0]


def _section(html: str, label: str) -> str:
    """The section whose label is ``label``, up to the next section."""
    after = html.split(f">{label}</", 1)[1].split(">", 1)[1]
    return after.split('<td class="px', 1)[0].rsplit("<tr", 1)[0]


# --- Headline -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("readiness", "verdict"),
    [
        ("go_hard", "Go hard"),
        ("moderate", "Moderate"),
        ("active_recovery", "Active recovery"),
        ("rest", "Rest"),
    ],
)
def test_the_verdict_is_mapped_by_code_from_readiness(readiness: str, verdict: str) -> None:
    hero = _text(_hero(_render(_notes(readiness=readiness))))

    assert hero.startswith(f"Today's readiness {verdict} ")


def test_the_coach_writes_the_lead_under_the_verdict_in_its_tone() -> None:
    hero = _hero(_render())

    assert "Nothing planned for today and you're fresh: a good day for Squat Day." in _text(hero)
    assert 'class="chip c-good"' in hero


# --- Do today · pick one --------------------------------------------------------------


def _card(html: str) -> str:
    return _text(_section(html, "Do today · pick one"))


def test_an_option_linked_by_id_is_named_from_its_open_session() -> None:
    card = _card(_render())

    assert "Squat Day Recommended 75 min Open since Mon · gym · strength" in card
    assert "Last working weights, RPE 7–8" in card
    assert "Fresh legs today, and Thursday keeps its swim." in card


def test_an_upcoming_option_names_its_planned_day() -> None:
    card = _card(_render())

    assert "Threshold intervals swim 45 min Planned Thu · swimming · threshold" in card


def test_two_options_are_alternatives() -> None:
    assert _card(_render()).endswith("Don't stack both.")


def test_one_option_has_no_stacking_line() -> None:
    card = _card(_render(_notes(options=NOTES["options"][:1])))

    assert "Don't stack both." not in card


def test_an_option_with_only_a_title_is_shown_as_the_coach_named_it() -> None:
    rest = {"title": "Rest and a walk", "steps": ["20 min easy walk"], "why": "Ill since Monday."}
    option = {"open_session_id": None, "recommended": True, **rest}

    card = _card(_render(_notes(options=[option])))

    assert card == "Rest and a walk Recommended → 20 min easy walk Ill since Monday."


def test_an_unknown_id_is_dropped_with_a_warning_and_the_email_still_goes_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    options = copy.deepcopy(NOTES["options"])
    options[0]["open_session_id"] = 99

    with caplog.at_level(logging.WARNING):
        card = _card(_render(_notes(options=options)))

    assert "Squat Day" not in card
    assert "Threshold intervals swim" in card
    assert "Don't stack both." not in card
    assert any("99" in r.getMessage() for r in caplog.records)


def test_an_option_with_neither_id_nor_title_is_dropped() -> None:
    options = copy.deepcopy(NOTES["options"])
    options[1]["open_session_id"] = None

    card = _card(_render(_notes(options=options)))

    assert "8×100" not in card
    assert "Don't stack both." not in card


def test_with_no_option_left_the_card_is_omitted() -> None:
    options = copy.deepcopy(NOTES["options"])
    for option in options:
        option["open_session_id"] = 99

    html = _render(_notes(options=options))

    assert "Do today · pick one" not in html


def test_ids_cannot_resolve_without_open_this_week() -> None:
    assert "Do today · pick one" not in _render(open_this_week=None)


def test_at_most_one_option_is_recommended() -> None:
    options = copy.deepcopy(NOTES["options"])
    options[1]["recommended"] = True

    card = _card(_render(_notes(options=options)))

    assert card.count("Recommended") == 1
    assert card.startswith("from your open sessions Squat Day Recommended")


# --- Open this week: Today's pick ------------------------------------------------------


def _open_week(html: str) -> str:
    return _text(_section(html, "Open this week"))


def test_the_recommended_open_session_is_todays_pick_in_open_this_week() -> None:
    rows = _open_week(_render())

    assert "MON Squat Day Gym · strength · 75 min Today's pick" in rows
    assert "Planned day passed" not in rows  # the pick replaces the overdue chip
    assert "THU Threshold intervals swim Swimming · threshold · 45 min Thu" in rows


def test_without_a_recommended_open_session_there_is_no_pick() -> None:
    options = copy.deepcopy(NOTES["options"])
    options[0]["recommended"] = False

    rows = _open_week(_render(_notes(options=options)))

    assert "Today's pick" not in rows
    assert "MON Squat Day Gym · strength · 75 min Planned day passed" in rows


def test_a_recommended_option_with_only_a_title_picks_no_open_session() -> None:
    walk = {"open_session_id": None, "title": "Easy walk", "steps": ["30 min"], "why": "Rest day."}

    rows = _open_week(_render(_notes(options=[{**walk, "recommended": True}])))

    assert "Today's pick" not in rows


# --- Guardrail, last night, tonight ----------------------------------------------------


def test_the_guardrail_is_for_tomorrow() -> None:
    guardrail = _text(_section(_render(), "Guardrail · tomorrow"))

    assert guardrail == "IF HRV 7-day avg below range or RHR > 45 → Easy swim instead"


def test_no_guardrail_no_section() -> None:
    assert "Guardrail · tomorrow" not in _render(_notes(guardrail=None))


def test_last_nights_tiles_and_stages_come_from_the_health_snapshot() -> None:
    night = _text(_section(_render(hrv=SNAPSHOT), "Last night"))

    assert night.startswith("Sleep score 97 Excellent Sleep 9h03 Resting HR 43 bpm")
    # Shares of the whole night, awake time included (none here): 543 min.
    assert "Deep 133m · 24% REM 156m · 29% Light 253m · 47%" in night


def test_the_hrv_line_and_the_coachs_sleep_note_close_last_night() -> None:
    night = _text(_section(_render(hrv=SNAPSHOT), "Last night"))

    assert night.endswith(
        "HRV 52 ms 7-day avg · Building baseline · last night 48 ms "
        "Best REM in 3 days. Awake 1 min."
    )


def test_the_tiles_never_show_the_coachs_key_metrics() -> None:
    """The numbers come from the snapshot; key_metrics is only kept for the log."""
    notes = _notes(key_metrics={"sleep_score": 12, "resting_hr": 99})

    night = _text(_section(_render(notes, hrv=SNAPSHOT), "Last night"))

    assert "12" not in night and "99" not in night


def test_a_poor_night_is_qualified_in_the_alert_tone() -> None:
    night = _section(_render(hrv={"sleep_score": 52}), "Last night")

    assert 'class="muted tone-alert" style="margin-top:6px;">Poor</div>' in night
    assert "Sleep" in _text(night)


def test_without_sleep_data_last_night_keeps_only_the_coachs_note() -> None:
    night = _text(_section(_render(hrv=None), "Last night"))

    assert night == "Best REM in 3 days. Awake 1 min."


def test_tonight_shows_the_coachs_tips_with_a_chip_only_off_neutral() -> None:
    tips = [*NOTES["tonight"], {"tone": "watch", "text": "Jet lag: morning light, no nap"}]

    tonight = _section(_render(_notes(tonight=tips)), "Tonight")

    assert _text(tonight) == (
        "Bed 22:00–22:30 Screens off 60 min before bed Watch Jet lag: morning light, no nap"
    )
    assert tonight.count('class="chip') == 1


def test_no_tips_no_tonight() -> None:
    assert ">Tonight<" not in _render(_notes(tonight=[]))


# --- Without Coach notes ---------------------------------------------------------------


@pytest.mark.parametrize("content", [None, {"readiness_verdict": "go_hard"}])
def test_without_valid_notes_the_code_blocks_go_out_under_a_notice(
    content: dict[str, Any] | None,
) -> None:
    """``None`` is the engine's fallback; a v4 or off-contract answer counts as none."""
    html = _render(content, hrv=SNAPSHOT)
    text = _text(html)

    assert text.index("Coach notes unavailable today.") < text.index("Open this week")
    assert "Do today · pick one" not in html
    assert "Guardrail · tomorrow" not in html
    assert ">Tonight<" not in html
    assert "MON Squat Day Gym · strength · 75 min Planned day passed" in text
    assert "Sleep score 97 Excellent" in text
    assert "Today's pick" not in text


# --- Footer: Full analysis -------------------------------------------------------------


def test_the_footer_links_the_full_analysis_of_the_rendered_insight() -> None:
    html = _render(insight_id=42)

    assert '<a href="https://coach.example.com/insights/42"' in html
    assert "Full analysis →" in _text(html)


def test_without_notes_there_is_no_analysis_to_link() -> None:
    assert "Full analysis" not in _render(None, insight_id=42)
