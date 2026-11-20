"""The compile step renders an operation's variable map into model messages.

Golden files were captured from the string builders before they were split
into variable maps (#171); the compiled messages must match them exactly.
One intended change since: the post-workout output schema has single braces
(#174), where the old builder leaked str.format's doubled ones to the model.
And the system message now ends with the coaching principles (#219).
An operation on the structured-output contract (prompt v5, #238) compiles
from ``prompts/v5`` and its system message is ``system_v5.txt``.
"""

import json
from pathlib import Path
from string import Formatter

import pytest

from mycoach.coaching.email_schemas import PostWorkoutEmail
from mycoach.coaching.prompt_builder import (
    PROMPT_VARIABLES,
    Operation,
    bundled_version,
    compile_prompt,
    operation_template,
)
from mycoach.coaching.strict_schema import strict_json_schema
from tests.test_coaching.prompt_cases import CASES

GOLDEN = Path(__file__).parent / "golden_prompts"


@pytest.mark.parametrize("case", sorted(CASES))
def test_compiled_messages_match_pre_refactor_builders(case: str) -> None:
    operation, inputs = CASES[case]
    variables = PROMPT_VARIABLES[Operation(operation)](**inputs)

    messages = compile_prompt(Operation(operation), variables)

    system = "system.txt" if bundled_version(Operation(operation)) == "v4" else "system_v5.txt"
    assert messages.system == (GOLDEN / system).read_text()
    assert messages.user == (GOLDEN / f"{case}.txt").read_text()


def test_every_operation_is_covered_by_a_golden_case() -> None:
    assert {Operation(op) for op, _ in CASES.values()} == set(Operation)


@pytest.mark.parametrize("operation", list(Operation))
def test_variable_map_holds_only_strings(operation: Operation) -> None:
    """The map is what tracing records as generation input, so it must be plain text."""
    inputs = next(kw for op, kw in CASES.values() if op == operation)
    variables = PROMPT_VARIABLES[operation](**inputs)
    assert variables
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in variables.items())


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


@pytest.mark.parametrize("operation", list(Operation))
def test_published_v3_keeps_its_variable_set(operation: Operation) -> None:
    """ADR 0003: adding a variable makes a new bundled version (v4), so v3
    keeps the variables it was seeded with."""
    v3 = _placeholders(operation_template(operation, "v3"))
    v4 = _placeholders(operation_template(operation, "v4"))
    assert not v3 & {"open_this_week", "life_context"}
    assert v4 - v3 <= {"open_this_week", "life_context"}


def test_post_workout_is_bundled_on_v5_and_the_rest_on_v4() -> None:
    assert {op: bundled_version(op) for op in Operation} == {
        Operation.DAILY_BRIEFING: "v4",
        Operation.GYM_ADJUSTMENT: "v4",
        Operation.CARDIO_PLAN: "v4",
        Operation.POST_WORKOUT: "v5",
        Operation.WEEKLY_RECAP: "v4",
    }


def test_v5_post_workout_keeps_the_v4_variables() -> None:
    """A Langfuse version still on v4 text compiles against the same map."""
    assert _placeholders(operation_template(Operation.POST_WORKOUT, "v5")) == _placeholders(
        operation_template(Operation.POST_WORKOUT, "v4")
    )


def test_the_v5_post_workout_prompt_carries_its_strict_schema() -> None:
    """The schema reaches every provider as prompt text; claude_code has nothing else."""
    _, inputs = CASES["post_workout_swim_unplanned"]
    variables = PROMPT_VARIABLES[Operation.POST_WORKOUT](**inputs)

    messages = compile_prompt(Operation.POST_WORKOUT, variables)

    assert json.loads(variables["output_schema"]) == strict_json_schema(PostWorkoutEmail)
    assert variables["output_schema"] in messages.user


def test_the_v5_system_message_keeps_observed_numbers_out_of_coach_notes() -> None:
    _, inputs = CASES["post_workout_swim_unplanned"]
    variables = PROMPT_VARIABLES[Operation.POST_WORKOUT](**inputs)

    system = compile_prompt(Operation.POST_WORKOUT, variables).system

    assert (
        "Never write an observed or derived number, count or status that the context "
        "does not contain; never compute a completion figure." in " ".join(system.split())
    )


def test_missing_variable_fails_to_compile() -> None:
    with pytest.raises(KeyError):
        compile_prompt(Operation.CARDIO_PLAN, {"cardio_slots": "x"})
