"""One prompt label against one Langfuse dataset, as a Langfuse experiment.

Each dataset item's input is a prompt variable map (see docs/evals.md). The
task compiles the labelled prompt with it, the way ``LangfusePrompts`` does in
production, and runs it through the engine's ``generate`` (the real
``LLMClient``, response parser and retry). The item evaluator emits the same
code-check scores production emits, from the same ``code_checks``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langfuse import Evaluation
from langfuse.model import ChatPromptClient

from mycoach.coaching.engine import CoachingEngine, OperationSpec, code_checks
from mycoach.coaching.prompt_builder import Operation
from mycoach.coaching.prompt_store import (
    LANGFUSE_PROMPT_NAMES,
    InvalidPromptError,
    ServedPrompt,
    compile_langfuse,
)


class HarnessError(Exception):
    """Something to fix before the run can spend any quota."""


def operation_for_dataset(dataset: str) -> Operation:
    """The operation a dataset belongs to, from its name.

    A dataset is named after its prompt (``mycoach/`` optional), then
    optionally ``-`` or ``/`` and a suffix: ``gym-adjustment-golden``,
    ``mycoach/daily-briefing/golden``.
    """
    name = dataset.removeprefix("mycoach/")
    for operation, prompt_name in LANGFUSE_PROMPT_NAMES.items():
        short = prompt_name.removeprefix("mycoach/")
        if name == short or name.startswith((short + "-", short + "/")):
            return operation
    names = ", ".join(sorted(p.removeprefix("mycoach/") for p in LANGFUSE_PROMPT_NAMES.values()))
    raise HarnessError(
        f"dataset {dataset!r} is not named after a prompt; name it "
        f"[mycoach/]<prompt>[-<suffix>] with <prompt> one of {names}"
    )


def item_input(item: Any) -> Any:
    return item["input"] if isinstance(item, dict) else item.input


def select_items(items: Sequence[Any], limit: int) -> list[Any]:
    """The first ``limit`` active items. Slicing keeps each item's dataset
    link, so the run is still a run of the dataset."""
    return [i for i in items if getattr(i, "status", "ACTIVE") != "ARCHIVED"][:limit]


def check_items(prompt: ChatPromptClient, items: Sequence[Any]) -> None:
    """Every item must be a variable map that compiles the prompt strictly.

    Checked for all items before the first LLM call, so a bad item or a
    prompt that drifted from production's variables costs no quota.
    """
    problems: list[str] = []
    for item in items:
        variables = item_input(item)
        item_id = getattr(item, "id", "?")
        if not isinstance(variables, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in variables.items()
        ):
            problems.append(f"{item_id}: input is not a map of variable names to strings")
            continue
        try:
            compile_langfuse(prompt, variables)
        except InvalidPromptError as exc:
            problems.append(f"{item_id}: {exc}")
    if problems:
        raise HarnessError("items that do not fit the prompt:\n  " + "\n  ".join(problems))


def make_task(
    engine: CoachingEngine, spec: OperationSpec[Any], prompt: ChatPromptClient
) -> Callable[..., Any]:
    """The experiment task: one item through ``prompt`` and the engine.

    A failure never raises (the SDK would drop the item and its scores):
    it is returned in the output and scored like production scores it.
    """

    async def task(*, item: Any, **_: Any) -> dict[str, Any]:
        variables: Mapping[str, str] = item_input(item)
        system, user = compile_langfuse(prompt, variables)
        served = ServedPrompt(
            system=system,
            user=user,
            source="langfuse",
            name=prompt.name,
            version=prompt.version,
            link=prompt,
        )
        generation = await engine.generate(spec, prompt=served, variables=variables)
        response = generation.response
        return {
            "parsed": generation.parsed.model_dump() if generation.parsed is not None else None,
            "error": generation.error,
            "text": response.content if response is not None else None,
            "stop_reason": response.stop_reason if response is not None else None,
            "code_checks": dict(code_checks(generation, falls_back=spec.falls_back)),
        }

    return task


def code_check_evaluator(*, output: Mapping[str, Any], **_: Any) -> list[Evaluation]:
    """The task's code checks as BOOLEAN scores, valued as production's (1/0)."""
    return [
        Evaluation(name=name, value=1.0 if value else 0.0, data_type="BOOLEAN")
        for name, value in output["code_checks"].items()
    ]
