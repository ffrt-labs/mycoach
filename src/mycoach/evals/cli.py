"""``python -m mycoach.evals run --dataset <name> --label <label> --limit N``.

Runs a Langfuse prompt label against a Langfuse dataset as a dataset run, so
a candidate label can be compared with a ``production`` run of the same
dataset before it is promoted. See docs/evals.md.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langfuse import Langfuse

    from mycoach.coaching.llm_client import LLMClient
    from mycoach.config import Settings

LIMIT_CAP = 30
"""The most items one run may take: a golden dataset's size (about 30 items).
Each item costs one or two LLM calls from the provider's free daily quota."""

REFUSED_PROVIDERS = frozenset({"claude_code"})
"""Providers an experiment may never use: ``claude_code`` spends the Claude
Pro window the daily coaching runs depend on."""


def _limit(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"must be a whole number of items, got {value!r}"
        ) from None
    if not 1 <= n <= LIMIT_CAP:
        raise argparse.ArgumentTypeError(f"must be between 1 and {LIMIT_CAP}, got {n}")
    return n


def _label(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must name a prompt label, e.g. staging")
    return value.strip()


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m mycoach.evals",
        description="Prompt experiments on Langfuse datasets.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="run a prompt label against a dataset as a Langfuse dataset run"
    )
    run.add_argument(
        "--dataset",
        required=True,
        help="dataset named after its prompt, e.g. mycoach/daily-briefing/golden",
    )
    run.add_argument(
        "--label", type=_label, required=True, help="prompt label to run, e.g. staging"
    )
    run.add_argument(
        "--limit",
        type=_limit,
        required=True,
        help=f"how many active items to run, at most {LIMIT_CAP} (each spends provider quota)",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    langfuse: Langfuse | None = None,
    llm_client: LLMClient | None = None,
) -> int:
    args = parse_args(argv)
    # Imported here so --help and argument errors need no settings or SDK.
    from mycoach.config import get_settings
    from mycoach.evals.harness import HarnessError

    settings = settings or get_settings()
    provider = settings.llm_provider.lower()
    if provider in REFUSED_PROVIDERS:
        print(
            f"MYCOACH_LLM_PROVIDER={provider} is refused: experiments would use up the "
            "Claude Pro window daily coaching relies on. Configure a free-tier provider.",
            file=sys.stderr,
        )
        return 2
    try:
        return _run(args, settings, langfuse, llm_client)
    except HarnessError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _run(
    args: argparse.Namespace,
    settings: Settings,
    langfuse: Langfuse | None,
    llm_client: LLMClient | None,
) -> int:
    from langfuse.api import NotFoundError
    from langfuse.model import ChatPromptClient

    from mycoach.coaching.engine import OPERATION_SPECS, CoachingEngine
    from mycoach.coaching.llm_client import get_llm_client
    from mycoach.coaching.prompt_store import LANGFUSE_PROMPT_NAMES, BundledPrompts
    from mycoach.evals.harness import (
        HarnessError,
        check_items,
        code_check_evaluator,
        make_task,
        operation_for_dataset,
        select_items,
    )
    from mycoach.observability.scores import NoopScoreSink
    from mycoach.observability.tracing import LangfuseTracer, build_tracer

    operation = operation_for_dataset(args.dataset)
    spec = OPERATION_SPECS[operation]
    prompt_name = LANGFUSE_PROMPT_NAMES[operation]

    if langfuse is None:
        tracer = build_tracer(settings)
        if not isinstance(tracer, LangfuseTracer):
            raise HarnessError(
                "Langfuse is not configured: set LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY "
                "and LANGFUSE_SECRET_KEY"
            )
        langfuse = tracer.client

    try:
        prompt = langfuse.get_prompt(
            prompt_name, label=args.label, type="chat", cache_ttl_seconds=0
        )
    except NotFoundError:
        raise HarnessError(f"prompt {prompt_name} has no version labelled {args.label!r}") from None
    if not isinstance(prompt, ChatPromptClient):
        raise HarnessError(f"prompt {prompt_name} is not a chat prompt")

    try:
        dataset = langfuse.get_dataset(args.dataset)
    except NotFoundError:
        raise HarnessError(f"dataset {args.dataset} does not exist") from None
    items = select_items(dataset.items, args.limit)
    if not items:
        raise HarnessError(f"dataset {args.dataset} has no active items")
    check_items(prompt, items)

    llm = llm_client or get_llm_client()
    # The engine's real generate path, tracing into the experiment's traces.
    # Production scores (the score sink) and the prompt store stay out of it.
    engine = CoachingEngine(
        llm_client=llm,
        tracer=LangfuseTracer(langfuse),
        scores=NoopScoreSink(),
        prompts=BundledPrompts(),
    )
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = langfuse.run_experiment(
        name=f"{prompt_name} {args.label}",
        run_name=f"{args.label}-v{prompt.version}-{stamp}",
        description=(
            f"{prompt_name} label {args.label} (v{prompt.version}) "
            f"on {len(items)} items of {args.dataset}"
        ),
        data=items,
        task=make_task(engine, spec, prompt),
        evaluators=[code_check_evaluator],
        max_concurrency=1,  # free tiers: no bursts
        metadata={
            "prompt_label": args.label,
            "prompt_version": str(prompt.version),
            "provider": settings.llm_provider.lower(),
            "model": spec.model(llm),
            "limit": str(args.limit),
        },
    )
    print(result.format())
    if result.experiment_url:
        print(result.experiment_url)
    langfuse.flush()
    failed = len(items) - len(result.item_results)
    if failed:
        print(f"{failed} of {len(items)} items failed (see the log above)", file=sys.stderr)
        return 1
    return 0
