"""Coaching principles from the Huberman Lab Neural Network Digest Vol. 1 (#219).

The principles ride in the shared system message, so every coaching operation
(daily briefing, weekly plan, post-workout, weekly recap) sees them, bundled or
seeded into Langfuse.
"""

import re

import pytest

from mycoach.coaching.prompt_builder import (
    PROMPT_VARIABLES,
    Operation,
    coaching_principles,
    compile_prompt,
)
from mycoach.coaching.prompt_store import langfuse_messages
from tests.test_coaching.prompt_cases import CASES


def _variables(operation: Operation) -> dict[str, str]:
    inputs = next(kw for op, kw in CASES.values() if op == operation)
    return PROMPT_VARIABLES[operation](**inputs)


@pytest.mark.parametrize("operation", list(Operation))
def test_every_operation_system_message_carries_the_principles(operation: Operation) -> None:
    system = compile_prompt(operation, _variables(operation)).system

    assert coaching_principles() in system
    assert "Neural Network Digest Vol. 1" in system


@pytest.mark.parametrize("operation", list(Operation))
def test_the_langfuse_seed_carries_the_principles_too(operation: Operation) -> None:
    system = langfuse_messages(operation)[0]["content"]

    assert "Wake at the same time every day" in system


def test_irregular_wake_times_reach_the_briefing_with_the_anchoring_principle() -> None:
    """The evals case: the model sees both the swinging wake times and the
    principle that tells it to nudge toward one wake time."""
    operation, inputs = CASES["daily_briefing_irregular_wake"]
    prompt = compile_prompt(Operation(operation), PROMPT_VARIABLES[Operation(operation)](**inputs))

    for wake in ["09:40", "06:05", "10:15", "06:20"]:
        assert f"- Wake time: {wake}" in prompt.user
    assert "wake times in the health data vary by more than about 1 h" in prompt.system


def test_principles_cover_the_tickets_in_list() -> None:
    text = coaching_principles()

    for phrase in [
        "Wake at the same time every day",
        "8-10 h before bed",
        "Alcohol",
        "cool, dark",
        "under 90 min",
        "alert about 1 h before",
        "NSDR",
        "6-8 h after",
        "180-200 min",
        "perfect form",
        "Physiological sigh",
        "slow breathing",
        "Static stretching",
        "11 min per week",
        "End with cold",
        "50%",
        "3-7 days",
    ]:
        assert phrase in text, phrase


def test_principles_never_recommend_supplements_or_doses() -> None:
    text = coaching_principles().lower()

    for banned in [
        "magnesium",
        "apigenin",
        "theanine",
        "glycine",
        "gaba",
        "alpha-gpc",
        "tyrosine",
        "whey",
        " mg",
        "5 g ",
    ]:
        assert banned not in text, banned
    assert "never recommend a supplement" in text


def test_principles_have_no_template_braces() -> None:
    """The system message goes through the Langfuse template conversion untouched."""
    assert not re.search(r"[{}]", coaching_principles())
