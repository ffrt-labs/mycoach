"""The compile step renders an operation's variable map into model messages.

Golden files were captured from the string builders before they were split
into variable maps (#171); the compiled messages must match them exactly.
One intended change since: the post-workout output schema has single braces
(#174), where the old builder leaked str.format's doubled ones to the model.
"""

from pathlib import Path

import pytest

from mycoach.coaching.prompt_builder import (
    PROMPT_VARIABLES,
    Operation,
    compile_prompt,
)
from tests.test_coaching.prompt_cases import CASES

GOLDEN = Path(__file__).parent / "golden_prompts"


@pytest.mark.parametrize("case", sorted(CASES))
def test_compiled_messages_match_pre_refactor_builders(case: str) -> None:
    operation, inputs = CASES[case]
    variables = PROMPT_VARIABLES[Operation(operation)](**inputs)

    messages = compile_prompt(Operation(operation), variables)

    assert messages.system == (GOLDEN / "system.txt").read_text()
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


def test_missing_variable_fails_to_compile() -> None:
    with pytest.raises(KeyError):
        compile_prompt(Operation.CARDIO_PLAN, {"cardio_slots": "x"})
