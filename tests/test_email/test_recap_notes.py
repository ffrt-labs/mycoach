"""The weekly recap email on the v5 contract: Coach notes in slots, numbers from code (#242).

The coach writes the Lead, Next week actions, a Guardrail, session and lift notes and
the recovery and load notes; the sessions, recovery tiles, top sets, load values and
setup notices are rendered from the data. Tests render through ``send_weekly_recap``.
"""

import html as html_lib
import json
import logging
from typing import Any
from unittest.mock import patch

import pytest

from mycoach.email.sender import send_weekly_recap
from tests.test_coaching.prompt_cases import WEEK_2026_11_02_ADHERENCE
from tests.test_email.test_weekly_recap_email import _settings, text, texts

NOTES: dict[str, Any] = {
    "headline": {
        "tone": "good",
        "summary": "Both lifts and the swim are in, and Sunday's padel delivered the base.",
    },
    "actions": [
        {
            "do": "Keep one intervals session in the week.",
            "why": "It was the only high-intensity work planned.",
        },
        {"do": "Protect sleep before the hard days.", "why": None},
    ],
    "guardrail": {"when": "Resting HR up 5 bpm", "then": "Swap intervals for easy Z2"},
    "session_notes": [
        {"session_id": 2, "text": "Short on volume, still done."},
        {"session_id": 99, "text": "A session the week never had."},
    ],
    "recovery_note": "Thursday's night was the low point; sleep was back by Sunday.",
    "lift_notes": [
        {"exercise_id": "D04AC939", "tone": "watch", "trend": "flat 3 weeks"},
        {"exercise_id": "NOPE0000", "tone": "good", "trend": "up 5 kg"},
    ],
    "load_note": "Mostly low aerobic work; the anaerobic side is thin.",
    "block_suggestion": None,
    "analysis_md": "## Week\nSolid.",
}

GYM_SETS: list[dict[str, Any]] = [
    {
        "session_date": "2026-11-04",
        "exercise_id": "D04AC939",
        "exercise_title": "Barbell Squat",
        "set_index": 0,
        "set_type": "warmup",
        "weight_kg": 120.0,
        "reps": 1,
        "rpe": None,
    },
    {
        "session_date": "2026-11-04",
        "exercise_id": "D04AC939",
        "exercise_title": "Barbell Squat",
        "set_index": 1,
        "set_type": "normal",
        "weight_kg": 100.0,
        "reps": 5,
        "rpe": 8,
    },
    {
        "session_date": "2026-11-04",
        "exercise_id": "D04AC939",
        "exercise_title": "Barbell Squat",
        "set_index": 2,
        "set_type": "normal",
        "weight_kg": 95.0,
        "reps": 8,
        "rpe": 8,
    },
    {
        "session_date": "2026-11-06",
        "exercise_id": "C6272009",
        "exercise_title": "Barbell Deadlift",
        "set_index": 1,
        "set_type": "normal",
        "weight_kg": 140.0,
        "reps": 5,
        "rpe": 8.5,
    },
    {
        "session_date": "2026-11-06",
        "exercise_id": None,
        "exercise_title": "Sled push (custom)",
        "set_index": 1,
        "set_type": "normal",
        "weight_kg": 60.0,
        "reps": 10,
        "rpe": None,
    },
]

HEALTH_WEEK: list[dict[str, Any]] = [
    {"snapshot_date": "2026-11-08", "resting_hr": 52, "sleep_score": 81},
    {"snapshot_date": "2026-11-05", "resting_hr": 55, "sleep_score": 64},
    {"snapshot_date": "2026-11-03", "sleep_score": 72},
]


def _load_focus(**targets: float) -> str:
    return json.dumps(
        {"aerobic_low": 412.4, "aerobic_high": 230.0, "anaerobic": 18.6, "feedback": None} | targets
    )


def _render(content: Any = NOTES, **kwargs: Any) -> str:
    views: dict[str, Any] = {
        "adherence": WEEK_2026_11_02_ADHERENCE,
        "episodes": [],
        "gym_sets": GYM_SETS,
        "health_week": HEALTH_WEEK,
        "has_mesocycle": True,
    } | kwargs
    with patch("mycoach.email.sender.send_email", return_value=True) as mock_send:
        send_weekly_recap(content, week_start="2026-11-02", settings=_settings(), **views)
    return str(mock_send.call_args[0][2])


def _section(html: str, label: str) -> str:
    """The section whose label is ``label``, up to the next section."""
    after = html.split(f">{label}</div>", 1)[1]
    return html_lib.unescape(after.split('class="px section', 1)[0])


def _labels(html: str) -> list[str]:
    return [t for t in texts(html, "sec") if t]


class TestSlots:
    def test_the_lead_follows_the_completion(self) -> None:
        html = _render()

        assert html.index("completion-h1") < html.index("Both lifts and the swim are in")

    def test_next_week_lists_the_actions_and_the_guardrail(self) -> None:
        section = _section(_render(), "Next week")

        assert "Keep one intervals session in the week." in section
        assert "It was the only high-intensity work planned." in section
        assert "Protect sleep before the hard days." in section
        assert "Resting HR up 5 bpm → Swap intervals for easy Z2" in section

    def test_no_guardrail_leaves_only_the_actions(self) -> None:
        section = _section(_render(NOTES | {"guardrail": None}), "Next week")

        assert "→ Swap" not in section

    def test_the_next_week_card_comes_before_the_sessions(self) -> None:
        html = _render()

        assert html.index("Next week") < html.index(">Sessions</div>")

    def test_no_v4_paragraph_sections_are_left(self) -> None:
        labels = _labels(_render())

        for v4 in ("Training load", "Highlights", "Watch", "Mesocycle", "Coach recommendations"):
            assert v4 not in labels

    def test_the_cta_opens_the_full_recap(self) -> None:
        assert "Full recap →" in _render()


class TestSessionNotes:
    def test_a_session_note_joins_its_row_by_session_id(self) -> None:
        rows = texts(_render(), "session-row")

        swim = next(r for r in rows if "Swim 1.7 km" in r)
        assert "Short on volume, still done." in swim
        assert all("Short on volume" not in r for r in rows if r != swim)

    def test_a_note_on_an_unknown_session_is_dropped_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            html = _render()

        assert "A session the week never had." not in html
        assert "session_id 99" in caplog.text


class TestStrength:
    def test_the_top_set_of_each_catalogue_exercise_comes_from_the_log(self) -> None:
        rows = texts(_render(), "lift-row")

        assert rows[0].startswith("Barbell Squat 100 kg × 5 @8")
        assert rows[1].startswith("Barbell Deadlift 140 kg × 5 @8.5")

    def test_a_warm_up_set_is_never_the_top_set(self) -> None:
        assert "120 kg" not in _section(_render(), "Strength")

    def test_a_custom_exercise_is_left_out(self) -> None:
        assert "Sled push" not in _section(_render(), "Strength")

    def test_a_lift_note_joins_by_exercise_id_with_its_tone(self) -> None:
        html = _render()

        squat, deadlift = texts(html, "lift-row")
        assert squat.endswith("flat 3 weeks")
        assert "c-watch" in _section(html, "Strength").split("Barbell Deadlift")[0]
        assert deadlift == "Barbell Deadlift 140 kg × 5 @8.5"

    def test_a_lift_note_on_an_unknown_exercise_is_dropped_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            html = _render()

        assert "up 5 kg" not in html
        assert "exercise_id NOPE0000" in caplog.text

    def test_a_week_without_gym_sets_has_no_strength_section(self) -> None:
        assert "Strength" not in _labels(_render(gym_sets=[]))


class TestRecovery:
    def test_sleep_score_shows_the_weeks_range_and_its_low_day(self) -> None:
        tiles = texts(_render(), "recovery-tile")

        assert tiles[0] == "Sleep score 64–81 Low on Thu 5 Nov"

    def test_resting_hr_shows_its_range_and_how_many_readings(self) -> None:
        tiles = texts(_render(), "recovery-tile")

        assert tiles[1] == "Resting HR 52–55 bpm 2 readings this week"

    def test_a_single_reading_is_shown_as_itself(self) -> None:
        tiles = texts(
            _render(health_week=[{"snapshot_date": "2026-11-05", "resting_hr": 55}]),
            "recovery-tile",
        )

        assert tiles == ["Resting HR 55 bpm 1 reading this week"]

    def test_the_recovery_note_follows_the_tiles(self) -> None:
        section = _section(_render(), "Recovery")

        assert section.index("recovery-tile") < section.index("Thursday's night was the low")

    def test_no_health_data_and_no_notes_leave_the_section_out(self) -> None:
        assert "Recovery" not in _labels(_render(None, health_week=[]))


class TestLoad:
    def test_load_values_come_from_the_latest_load_focus(self) -> None:
        week = [{"snapshot_date": "2026-11-08", "load_focus": _load_focus()}]
        rows = texts(_render(health_week=week), "load-row")

        assert rows == ["Low aerobic 412", "High aerobic 230", "Anaerobic 19"]

    def test_without_a_target_range_there_is_no_bar(self) -> None:
        week = [{"snapshot_date": "2026-11-08", "load_focus": _load_focus()}]

        assert "load-bar" not in _render(health_week=week)

    def test_a_target_range_draws_a_bar_and_names_the_range(self) -> None:
        focus = _load_focus(aerobic_low_target_min=300, aerobic_low_target_max=600)
        html = _render(health_week=[{"snapshot_date": "2026-11-08", "load_focus": focus}])

        assert html.count('class="load-bar"') == 1
        assert "target 300–600" in text(html, "load-row-aerobic_low")

    def test_the_load_note_follows_the_values(self) -> None:
        week = [{"snapshot_date": "2026-11-08", "load_focus": _load_focus()}]
        section = _section(_render(health_week=week), "Load focus")

        assert "Mostly low aerobic work" in section


class TestSetup:
    def test_no_mesocycle_links_to_the_mesocycles_page(self) -> None:
        section = _section(_render(has_mesocycle=False), "Setup")

        assert "Progression is generic until a mesocycle exists." in section
        assert 'href="https://coach.example.com/mesocycles"' in section

    def test_a_failed_plan_generation_links_to_the_job_log(self) -> None:
        section = _section(_render(plan_failed=True), "Setup")

        assert "Next week's plan failed to generate." in section
        assert 'href="https://coach.example.com/api/system/jobs/weekly_plan/runs"' in section

    def test_the_block_suggestion_sits_in_setup(self) -> None:
        notes = NOTES | {"block_suggestion": "Start a 4-week strength block."}

        assert "Start a 4-week strength block." in _section(_render(notes), "Setup")

    def test_nothing_to_set_up_leaves_the_section_out(self) -> None:
        assert "Setup" not in _labels(_render())


class TestWithoutCoachNotes:
    def test_the_code_blocks_go_out_under_a_notice(self) -> None:
        html = _render(None)

        assert "Coach notes unavailable today" in html
        assert text(html, "completion-h1") == "4/5 done"
        assert len(texts(html, "session-row")) == 5
        assert len(texts(html, "lift-row")) == 2
        assert len(texts(html, "recovery-tile")) == 2
        assert "Next week" not in _labels(html)

    def test_a_v4_recap_is_treated_as_no_notes(self) -> None:
        html = _render({"week_summary": "Solid week", "adherence_analysis": "9/9 done"})

        assert "Coach notes unavailable today" in html
        assert "Solid week" not in html


class TestFullRecapLink:
    def test_the_cta_opens_the_insight_page_of_the_recap_it_renders(self) -> None:
        html = _render(insight_id=42)

        assert 'href="https://coach.example.com/insights/42"' in html

    def test_without_coach_notes_the_cta_opens_the_dashboard(self) -> None:
        html = _render(None, insight_id=42)

        assert "/insights/42" not in html
        assert 'href="https://coach.example.com/dashboard"' in html
