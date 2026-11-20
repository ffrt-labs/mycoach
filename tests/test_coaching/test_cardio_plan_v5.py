"""The cardio plan's Coach notes on the structured-output contract (prompt v5, #243)."""

import json
import logging
from typing import Any

import pytest

from mycoach.coaching.email_schemas import CardioPlanV5
from mycoach.coaching.response_parser import parse_response


def _session(**fields: Any) -> dict[str, Any]:
    return {
        "day_of_week": 1,
        "sport": "swimming",
        "intent": "aerobic_base",
        "title": "Aerobic base swim",
        "duration_minutes": 45,
        "focus": "Keep HR in Z2.",
        "blocks": [
            {"phase": "warmup", "set": "300", "cue": None, "target": "RPE 3", "rest_s": 15},
            {"phase": "main", "set": "5×200", "cue": "Even splits", "target": None, "rest_s": 25},
        ],
        "foot": "Total ~2000 m.",
        **fields,
    }


def _plan(*sessions: dict[str, Any], **fields: Any) -> str:
    return json.dumps(
        {
            "sessions": list(sessions) or [_session()],
            "focus": "One quality day: Thursday's threshold swim.",
            "guardrails": [{"when": "Legs heavy on Sunday", "then": "Skip the strides"}],
            "goal_assessment": "On track for the 1500 m.",
            **fields,
        }
    )


def test_a_cardio_session_parses_with_its_blocks() -> None:
    plan = parse_response(_plan(), CardioPlanV5)

    session = plan.sessions[0]
    assert [(b.phase, b.set) for b in session.blocks] == [("warmup", "300"), ("main", "5×200")]
    assert session.focus == "Keep HR in Z2."
    assert session.foot == "Total ~2000 m."
    assert plan.focus == "One quality day: Thursday's threshold swim."
    assert [(g.when, g.then) for g in plan.guardrails] == [
        ("Legs heavy on Sunday", "Skip the strides")
    ]


def test_an_unknown_intent_is_none_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        plan = parse_response(_plan(_session(intent="tempo")), CardioPlanV5)

    assert plan.sessions[0].intent is None
    assert "tempo" in caplog.text


def test_a_session_needs_one_to_six_blocks() -> None:
    with pytest.raises(ValueError, match="validation"):
        parse_response(_plan(_session(blocks=[])), CardioPlanV5)
    block = {"phase": "main", "set": "1×100", "cue": None, "target": None, "rest_s": None}
    with pytest.raises(ValueError, match="validation"):
        parse_response(_plan(_session(blocks=[block] * 7)), CardioPlanV5)


def test_at_most_three_guardrails() -> None:
    rule = {"when": "Tired", "then": "Go easy"}
    with pytest.raises(ValueError, match="validation"):
        parse_response(_plan(guardrails=[rule] * 4), CardioPlanV5)


def test_the_old_free_text_details_are_off_contract() -> None:
    with pytest.raises(ValueError, match="validation"):
        parse_response(_plan(_session(details={"description": "4×100"})), CardioPlanV5)
