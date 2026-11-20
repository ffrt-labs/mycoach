"""The post-workout email on the v5 contract: Coach notes in slots, or none (#238)."""

import html as html_lib
import re
from typing import Any
from unittest.mock import patch

from mycoach.config import Settings
from mycoach.email.sender import send_post_workout
from tests.test_email.test_post_workout_week import _open, _open_week, _session, _week

SWIM = {"external_id": "swim-1", "title": "Pool swim", "sport": "swimming"}

NOTES: dict[str, Any] = {
    "headline": {
        "tone": "watch",
        "verdict": "Solid, but short",
        "summary": "Steady aerobic swim with efficient technique; the main set drifted into Z3.",
    },
    "actions": [
        {"do": "Swim the full set. Keep the main set intact.", "why": "Distance flat for weeks."},
        {"do": "Main set at RPE 4, HR in Z2.", "why": None},
    ],
    "zone_note": {"tone": "watch", "text": "Above the keep-Z2 target; no Z4-5."},
    "trend_note": "Efficiency is recovering after the early-November dip.",
    "next_session": [
        {
            "phase": "warmup",
            "set": "300 + 4×50",
            "cue": "Easy, then build",
            "target": "RPE 3",
            "rest_s": 15,
        },
        {
            "phase": "main",
            "set": "5×200",
            "cue": "Even splits, record each",
            "target": "RPE 4–5 · Z2",
            "rest_s": 25,
        },
        {"phase": "cooldown", "set": "200", "cue": "Easy", "target": None, "rest_s": None},
    ],
    "guardrails": [{"when": "Short on time", "then": "Pull 2×100, keep the main set"}],
    "week_addition": "One extra easy Z2 swim, 1200–1500 m",
    "ready_in_hours": 24,
    "recovery": "Protein + carbs within 2 h · light shoulder mobility",
    "analysis_md": "## Pool swim\nSteady.",
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
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        send_post_workout(content, "Pool swim", _settings(), activity=SWIM, **views)
    return str(mock_send.call_args[0][2])


def _text(html: str) -> str:
    html = re.sub(r"<(style|head)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _hero(html: str) -> str:
    return html.split('class="px hero"', 1)[1].split(">", 1)[1].split("</td>", 1)[0]


def _section(html: str, label: str) -> str:
    """The section whose label is ``label``, up to the next section."""
    after = html.split(f">{label}</div>", 1)[1]
    return after.split('class="px section', 1)[0]


# --- Headline -------------------------------------------------------------------------


def test_the_hero_names_the_sport_and_activity_then_the_coachs_verdict() -> None:
    hero = _text(_hero(_render()))

    assert hero.startswith("Swim · Pool swim Solid, but short")
    assert "the main set drifted into Z3." in hero


# --- Next time ------------------------------------------------------------------------


def test_next_time_numbers_each_action_with_its_reason() -> None:
    card = _text(_render().split('class="coach"', 1)[1].split(">", 1)[1].split("</table>", 1)[0])

    assert card.startswith("Next time")
    assert "1 Swim the full set. Keep the main set intact. Distance flat for weeks." in card
    assert "2 Main set at RPE 4, HR in Z2." in card


def test_next_time_comes_right_after_the_headline() -> None:
    text = _text(_render())

    assert text.index("Solid, but short") < text.index("Next time") < text.index("Heart rate")


# --- Zones and trend ------------------------------------------------------------------


def test_the_zone_note_carries_its_tone() -> None:
    zones = _section(_render(), "Heart rate zones")

    assert re.search(r'class="chip c-watch">Watch<', zones)
    assert "Above the keep-Z2 target; no Z4-5." in _text(zones)


def test_no_zone_note_leaves_the_heart_rate_section_out() -> None:
    assert "Heart rate zones" not in _render({**NOTES, "zone_note": None})


def test_the_trend_note_has_its_own_section() -> None:
    trend = _text(_section(_render(), "Trend"))

    assert trend.startswith("Efficiency is recovering after the early-November dip.")


# --- Next session ---------------------------------------------------------------------


def test_the_next_session_is_a_block_table() -> None:
    table = _text(_section(_render(), "Next session"))

    assert table.startswith("Phase Set Target Rest")
    assert "Warm-up 300 + 4×50 Easy, then build RPE 3 15s" in table
    assert "Main 5×200 Even splits, record each RPE 4–5 · Z2 25s" in table
    assert "Cool-down 200 Easy — —" in table


def test_guardrails_and_the_week_addition_follow_the_blocks() -> None:
    block = _section(_render(), "Next session")
    text = _text(block)

    assert "IF Short on time → Pull 2×100, keep the main set" in text
    assert "+1 This week: One extra easy Z2 swim, 1200–1500 m" in text
    assert text.index("Cool-down") < text.index("Short on time") < text.index("This week")


def test_nothing_to_prescribe_leaves_the_next_session_out() -> None:
    html = _render({**NOTES, "next_session": [], "guardrails": [], "week_addition": None})

    assert "Next session" not in html


# --- Recovery -------------------------------------------------------------------------


def test_recovery_says_when_the_athlete_is_ready_again() -> None:
    recovery = _section(_render(), "Recovery")

    assert re.search(r'class="chip c-good">Ready again in &lt; 24 h<', recovery)
    assert "Protein + carbs within 2 h · light shoulder mobility" in _text(recovery)


# --- Only slots and code --------------------------------------------------------------


def test_the_v4_paragraph_sections_are_gone() -> None:
    text = _text(_render())

    for v4_label in ("Planned vs actual", "Highlights", "Work on", "Trends"):
        assert v4_label not in text


# --- Coach notes unavailable ----------------------------------------------------------


def _with_code_blocks(content: dict[str, Any] | None) -> str:
    week = _week([_session("Tuesday", "Aerobic base swim", "done", done_by="swim-1")])
    open_week = _open_week([_open("Thursday", "Threshold swim")], days_remaining=3)
    return _render(content, week=week, open_this_week=open_week)


def test_without_coach_notes_the_email_keeps_its_code_blocks_and_says_so() -> None:
    html = _with_code_blocks(None)
    text = _text(html)

    assert "Coach notes unavailable today" in text
    assert re.search(r'class="notice c-neutral"', html)
    assert "Completes Tue · Aerobic base swim" in text
    assert "Still open this week" in text
    assert "Next time" not in text
    assert "Recovery" not in text


def test_coach_notes_that_no_longer_validate_are_treated_as_unavailable() -> None:
    """A v4 analysis, or anything else off-contract, renders no slots."""
    text = _text(_with_code_blocks({"performance_summary": "Steady aerobic swim."}))

    assert "Coach notes unavailable today" in text
    assert "Steady aerobic swim." not in text


def test_with_coach_notes_there_is_no_unavailable_notice() -> None:
    assert "Coach notes unavailable" not in _render()
