"""The weekly plan email on v5: focus, Guardrails, exercise and block tables (#243)."""

import re
from html import unescape
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mycoach.coaching.email_schemas import PlanNotes
from mycoach.config import Settings
from mycoach.email.sender import send_weekly_plan

NOTES = {
    "focus": "One quality day: Thursday's threshold swim.",
    "guardrails": [
        {"when": "HRV < 68 ms on Thu", "then": "Swap main set to 6×100 at RPE 5"},
        {"when": "Legs heavy on Sunday", "then": "Skip the strides"},
    ],
    "goal_assessment": "On track.",
}


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


def _block(phase: str, set_: str, **fields: object) -> dict[str, object]:
    return {"phase": phase, "set": set_, "cue": None, "target": None, "rest_s": None, **fields}


SWIM = {
    "day_of_week": 1,
    "day_name": "Tuesday",
    "title": "Aerobic base swim",
    "sport": "swimming",
    "duration_minutes": 45,
    "intent": "aerobic_base",
    "notes": "Keep HR in Z2. Log 200 m splits.",
    "details": {
        "blocks": [
            _block("warmup", "300 + 4×50", cue="Easy free, then build", target="RPE 3", rest_s=15),
            _block("main", "5×200", cue="Even splits", target="RPE 4–5", rest_s=25),
            _block("cooldown", "200"),
        ],
        "foot": "Total ~2000 m.",
    },
}


def _gym(*exercises: dict[str, Any], notes: str | None = "Start at last working weights.") -> dict:  # type: ignore[type-arg]
    return {
        "day_of_week": 4,
        "day_name": "Friday",
        "title": "Deadlift",
        "sport": "gym",
        "duration_minutes": 65,
        "intent": "strength",
        "notes": notes,
        "details": {"exercises": list(exercises)},
    }


def _ex(name: str, sets: int, reps: str, **fields: object) -> dict[str, object]:
    return {"name": name, "sets": sets, "reps": reps, **fields}


@pytest.fixture
def mock_send() -> MagicMock:
    with patch("mycoach.email.sender.send_email", return_value=True) as send:
        yield send


def _html(mock_send: MagicMock, **kwargs: object) -> str:
    args: dict[str, object] = {
        "summary": "Weekly training plan",
        "sessions": [SWIM],
        "week_start": "2026-11-16",
        "settings": _settings(),
        "notes": NOTES,
    }
    args.update(kwargs)
    send_weekly_plan(**args)  # type: ignore[arg-type]
    return str(mock_send.call_args[0][2])


def _text(html: str) -> str:
    """The rendered text, tags dropped and whitespace collapsed."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _rows(html: str, table: str) -> list[str]:
    """The text of each row of the ``data-table=table`` tables, header included."""
    rows = []
    for body in re.findall(rf'<table[^>]*data-table="{table}"[^>]*>(.*?)</table>', html, re.S):
        rows += [_text(r) for r in re.findall(r"<tr\b.*?</tr>", body, re.S)]
    return rows


def test_the_plan_focus_is_the_lead(mock_send: MagicMock) -> None:
    text = _text(_html(mock_send))
    assert "One quality day: Thursday's threshold swim." in text
    assert "Weekly training plan" not in text


def test_the_notes_validate_as_plan_notes_too(mock_send: MagicMock) -> None:
    text = _text(_html(mock_send, notes=PlanNotes.model_validate(NOTES)))
    assert "One quality day" in text


def test_guardrails_render_as_rules_this_week(mock_send: MagicMock) -> None:
    rows = _rows(_html(mock_send), "rules")
    assert rows == [
        "IF HRV < 68 ms on Thu → Swap main set to 6×100 at RPE 5",
        "IF Legs heavy on Sunday → Skip the strides",
    ]
    assert "Rules this week" in _text(_html(mock_send))


def test_no_guardrails_no_rules_section(mock_send: MagicMock) -> None:
    html = _html(mock_send, notes={**NOTES, "guardrails": []})
    assert "Rules this week" not in _text(html)


def test_without_notes_the_lead_is_the_summary_and_no_notice(mock_send: MagicMock) -> None:
    """A gym-only week has no cardio plan, so no Coach notes were expected."""
    text = _text(_html(mock_send, notes=None))
    assert "Weekly training plan" in text
    assert "Rules this week" not in text
    assert "Coach notes unavailable" not in text


def test_a_cardio_fallback_says_coach_notes_are_unavailable(mock_send: MagicMock) -> None:
    text = _text(_html(mock_send, notes=None, notes_unavailable=True))
    assert "Coach Coach notes unavailable today." in text


def test_the_goal_assessment_stays_off_the_email(mock_send: MagicMock) -> None:
    assert "On track." not in _text(_html(mock_send))


def test_a_cardio_card_shows_its_focus_block_table_and_foot(mock_send: MagicMock) -> None:
    html = _html(mock_send)
    text = _text(html)
    assert "Keep HR in Z2. Log 200 m splits." in text
    assert _rows(html, "blocks") == [
        "Phase Set Target Rest",
        "Warm-up 300 + 4×50 Easy free, then build RPE 3 15s",
        "Main 5×200 Even splits RPE 4–5 25s",
        "Cool-down 200 — —",
    ]
    assert "Total ~2000 m." in text


def test_the_day_card_header_carries_the_minutes(mock_send: MagicMock) -> None:
    assert "45′" in _text(_html(mock_send))


def test_a_gym_card_shows_its_focus_and_exercise_table(mock_send: MagicMock) -> None:
    html = _html(
        mock_send,
        sessions=[
            _gym(
                _ex("Squat (Barbell)", 6, "5", rpe=7, target_weight_kg=102.5),
                _ex("Face Pull", 3, "8-10"),
            )
        ],
    )
    assert "Start at last working weights." in _text(html)
    assert _rows(html, "exercises") == [
        "Exercise Sets × reps RPE Load",
        "Squat (Barbell) 6 × 5 7 102.5 kg",
        "Face Pull 3 × 8–10 — log",
    ]


def test_the_load_reads_log_until_a_target_exists(mock_send: MagicMock) -> None:
    html = _html(mock_send, sessions=[_gym(_ex("Deadlift", 3, "5", rpe=7))])
    assert _rows(html, "exercises")[1] == "Deadlift 3 × 5 7 log"


def test_a_routine_error_shows_as_planned_with_a_footnote(mock_send: MagicMock) -> None:
    html = _html(
        mock_send,
        sessions=[
            _gym(
                _ex("Deadlift", 36, "5"),
                _ex("Pull Up", 36, "5"),
                _ex("Bulgarian Split Squat", 32, "8-10"),
                _ex("Leg Raise", 3, "10"),
            )
        ],
    )
    assert _rows(html, "exercises")[1:] == [
        "Deadlift 3* × 5 — log",
        "Pull Up 3* × 5 — log",
        "Bulgarian Split Squat 3* × 8–10 — log",
        "Leg Raise 3 × 10 — log",
    ]
    assert "* Routine says 36 / 32 sets — assumed 3 until fixed." in _text(html)


FAILED_SWIM = {
    **SWIM,
    "title": "Swimming Session (plan generation failed)",
    "duration_minutes": None,
    "intent": None,
    "notes": "LLM plan generation failed — please reschedule.",
    "details": None,
    "generation_error": "provider timeout",
}


def test_a_session_that_failed_to_generate_is_a_notice_not_coaching(
    mock_send: MagicMock,
) -> None:
    """Story 5: an app problem renders as a Notice with a link to fix it."""
    html = _html(mock_send, sessions=[FAILED_SWIM])
    card = html.split("Swimming Session (plan generation failed)", 1)[1]

    assert "please reschedule" not in html
    assert 'class="coach"' not in card
    notice = card.split('class="notice', 1)[1].split("<!-- /notice -->", 1)[0]
    assert "Plan failed" in _text(notice)
    assert "This session failed to generate." in _text(notice)
    assert 'href="https://coach.example.com/plan"' in notice


def test_a_gym_session_whose_adjustment_failed_says_it_is_the_base_routine(
    mock_send: MagicMock,
) -> None:
    session = {
        **_gym(_ex("Deadlift", 3, "5"), notes="LLM adjustment failed — using base routine."),
        "generation_error": "provider timeout",
    }
    html = _html(mock_send, sessions=[session])

    assert "LLM adjustment failed" not in html
    notice = _text(html.split('class="notice', 1)[1].split("<!-- /notice -->", 1)[0])
    assert "Plan failed" in notice
    assert "The load and RPE adjustment failed to generate: this is the base routine." in notice
    assert _rows(html, "exercises")[1] == "Deadlift 3 × 5 — log"


def test_a_card_without_details_has_no_table(mock_send: MagicMock) -> None:
    session = {**SWIM, "details": None, "notes": None}
    html = _html(mock_send, sessions=[session])
    assert _rows(html, "blocks") == []
    assert _rows(html, "exercises") == []


def test_v4_free_text_details_are_not_rendered(mock_send: MagicMock) -> None:
    session = {**SWIM, "details": {"type": "easy", "description": "Zone 2 easy run"}}
    assert "Zone 2 easy run" not in _text(_html(mock_send, sessions=[session]))
