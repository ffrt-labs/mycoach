"""``python -m mycoach.evals run``: a prompt label against a Langfuse dataset.

The harness runs against a real Langfuse client whose API is the in-memory
fake and whose spans go to an in-memory exporter; scores are recorded on
``client.score_calls``. The LLM is a fake ``LLMClient``.
"""

from collections.abc import Generator
from typing import Any

import httpx
import pytest
from langfuse import Langfuse
from langfuse._client.resource_manager import LangfuseResourceManager
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mycoach.coaching.llm_client import Billing, LLMClient, LLMResponse
from mycoach.coaching.prompt_builder import PROMPT_VARIABLES, Operation, compile_prompt
from mycoach.coaching.prompt_store import langfuse_messages
from mycoach.config import Settings
from mycoach.evals.cli import LIMIT_CAP, main
from mycoach.evals.harness import HarnessError, operation_for_dataset
from tests.fake_langfuse import FakeLangfuseAPI
from tests.test_coaching.prompt_cases import CASES
from tests.test_coaching.test_engine import VALID_LLM_RESPONSE

DATASET = "mycoach/daily-briefing/golden"


class FakeLLM(LLMClient):
    """Answers with ``replies`` in order and records every call."""

    provider_name = "fake"
    billing: Billing = "free-tier"

    def __init__(self, *replies: LLMResponse) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {"system": system, "user": user_message, "model": model, "max_tokens": max_tokens}
        )
        return self.replies.pop(0)

    @property
    def daily_model(self) -> str:
        return "daily-model"

    @property
    def weekly_model(self) -> str:
        return "weekly-model"


class PaidLLM(FakeLLM):
    """A fake provider that bills per call, as ``anthropic`` does."""

    billing = "paid"


def reply(content: str, stop_reason: str | None = None) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="daily-model",
        input_tokens=100,
        output_tokens=50,
        latency_ms=10,
        estimated_cost_usd=0.0,
        stop_reason=stop_reason,
        billing="free-tier",
    )


def settings(provider: str = "anthropic") -> Settings:
    return Settings(
        llm_provider=provider,
        claude_api_key="test",
        claude_code_oauth_token="test",
        env="test",
    )


def briefing_variables(case: str) -> dict[str, str]:
    operation, inputs = CASES[case]
    return PROMPT_VARIABLES[Operation(operation)](**inputs)


@pytest.fixture
def fake() -> FakeLangfuseAPI:
    fake = FakeLangfuseAPI()
    fake.add("mycoach/daily-briefing", langfuse_messages(Operation.DAILY_BRIEFING), ["candidate"])
    fake.add_item(DATASET, briefing_variables("daily_briefing_full"))
    fake.add_item(DATASET, {"stale": "archived item"}, status="ARCHIVED")
    fake.add_item(DATASET, briefing_variables("daily_briefing_empty"))
    return fake


@pytest.fixture
def langfuse(fake: FakeLangfuseAPI) -> Generator[Any, None, None]:
    """A real client: API calls go to ``fake``, spans to memory, scores to a list."""
    LangfuseResourceManager.reset()
    exporter = InMemorySpanExporter()
    client: Any = Langfuse(
        public_key="pk-lf-evals",
        secret_key="sk-lf-evals",
        base_url="http://langfuse.invalid",
        httpx_client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
        span_exporter=exporter,
        tracer_provider=TracerProvider(),
    )
    client.exporter = exporter
    client.score_calls = []
    client.create_score = lambda **kwargs: client.score_calls.append(kwargs)
    yield client
    LangfuseResourceManager.reset()


def run(*argv: str, langfuse: Any, llm: LLMClient, provider: str = "anthropic") -> int:
    return main(["run", *argv], settings=settings(provider), langfuse=langfuse, llm_client=llm)


def scores_by_item(langfuse: Any) -> list[dict[str, float]]:
    """Each item's scores (name to value), in the order its trace first scored."""
    by_trace: dict[str, dict[str, float]] = {}
    for call in langfuse.score_calls:
        if call.get("trace_id"):
            by_trace.setdefault(call["trace_id"], {})[call["name"]] = call["value"]
    return list(by_trace.values())


class TestRun:
    def test_each_item_is_scored_with_the_production_code_checks(
        self, langfuse: Any, fake: FakeLangfuseAPI
    ) -> None:
        llm = FakeLLM(
            reply(VALID_LLM_RESPONSE),
            reply('{"sleep_assessment": "Good', stop_reason="max_tokens"),
            reply(VALID_LLM_RESPONSE),
        )

        code = run(
            "--dataset", DATASET, "--label", "candidate", "--limit", "5", langfuse=langfuse, llm=llm
        )

        assert code == 0
        scores = sorted(scores_by_item(langfuse), key=lambda s: s["needed_retry"])
        assert scores == [
            {"parse_ok": 1.0, "needed_retry": 0.0, "truncated": 0.0},
            {"parse_ok": 1.0, "needed_retry": 1.0, "truncated": 1.0},
        ]
        assert {c["data_type"] for c in langfuse.score_calls} == {"BOOLEAN"}

    def test_the_labelled_prompt_is_compiled_with_each_item_and_called_as_production(
        self, langfuse: Any
    ) -> None:
        llm = FakeLLM(reply(VALID_LLM_RESPONSE))

        run(
            "--dataset", DATASET, "--label", "candidate", "--limit", "1", langfuse=langfuse, llm=llm
        )

        expected = compile_prompt(
            Operation.DAILY_BRIEFING, briefing_variables("daily_briefing_full")
        )
        assert llm.calls == [
            {
                "system": expected.system,
                "user": expected.user,
                "model": "daily-model",
                "max_tokens": 4096,
            }
        ]

    def test_each_generation_is_traced_in_its_items_trace_and_linked_to_the_prompt(
        self, langfuse: Any
    ) -> None:
        run(
            "--dataset",
            DATASET,
            "--label",
            "candidate",
            "--limit",
            "1",
            langfuse=langfuse,
            llm=FakeLLM(reply(VALID_LLM_RESPONSE)),
        )

        langfuse.flush()
        spans = langfuse.exporter.get_finished_spans()
        generations = [
            s for s in spans if s.attributes.get("langfuse.observation.type") == "generation"
        ]
        assert [g.name for g in generations] == ["daily_briefing"]
        generation = generations[0]
        assert generation.attributes["langfuse.observation.prompt.name"] == (
            "mycoach/daily-briefing"
        )
        scored = {c["trace_id"] for c in langfuse.score_calls}
        assert scored == {format(generation.context.trace_id, "032x")}

    def test_archived_items_are_skipped_and_limit_caps_the_items_run(
        self, langfuse: Any, fake: FakeLangfuseAPI
    ) -> None:
        fake.add_item(DATASET, briefing_variables("daily_briefing_full"))
        llm = FakeLLM(*(reply(VALID_LLM_RESPONSE) for _ in range(3)))

        run(
            "--dataset", DATASET, "--label", "candidate", "--limit", "2", langfuse=langfuse, llm=llm
        )

        assert len(llm.calls) == 2
        assert len(scores_by_item(langfuse)) == 2

    def test_an_item_that_is_not_the_prompts_variable_map_stops_the_run_before_any_call(
        self, langfuse: Any, fake: FakeLangfuseAPI
    ) -> None:
        fake.add_item(DATASET, {"health_today": "only one variable"})
        llm = FakeLLM()

        code = run(
            "--dataset", DATASET, "--label", "candidate", "--limit", "5", langfuse=langfuse, llm=llm
        )

        assert code != 0
        assert llm.calls == []

    def test_a_dataset_not_named_after_a_prompt_is_rejected(self, langfuse: Any) -> None:
        llm = FakeLLM()

        code = run(
            "--dataset",
            "golden",
            "--label",
            "candidate",
            "--limit",
            "5",
            langfuse=langfuse,
            llm=llm,
        )

        assert code != 0
        assert llm.calls == []


class TestProviderRefusal:
    def test_claude_code_is_refused_before_any_call(self, langfuse: Any) -> None:
        llm = FakeLLM(reply(VALID_LLM_RESPONSE))

        code = run(
            "--dataset",
            DATASET,
            "--label",
            "candidate",
            "--limit",
            "1",
            langfuse=langfuse,
            llm=llm,
            provider="claude_code",
        )

        assert code != 0
        assert llm.calls == []
        assert langfuse.score_calls == []

    def test_claude_code_is_refused_even_with_allow_paid(self, langfuse: Any) -> None:
        llm = FakeLLM(reply(VALID_LLM_RESPONSE))

        code = run(
            *("--dataset", DATASET, "--label", "candidate", "--limit", "1", "--allow-paid"),
            langfuse=langfuse,
            llm=llm,
            provider="claude_code",
        )

        assert code != 0
        assert llm.calls == []

    def test_a_paid_provider_is_refused_without_allow_paid(
        self, langfuse: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        llm = PaidLLM(reply(VALID_LLM_RESPONSE))

        code = run(
            "--dataset", DATASET, "--label", "candidate", "--limit", "1", langfuse=langfuse, llm=llm
        )

        assert code != 0
        assert llm.calls == []
        assert langfuse.score_calls == []
        assert "--allow-paid" in capsys.readouterr().err

    def test_a_paid_provider_runs_with_allow_paid(self, langfuse: Any) -> None:
        llm = PaidLLM(reply(VALID_LLM_RESPONSE))

        code = run(
            *("--dataset", DATASET, "--label", "candidate", "--limit", "1", "--allow-paid"),
            langfuse=langfuse,
            llm=llm,
        )

        assert code == 0
        assert len(llm.calls) == 1


class TestLimit:
    def test_limit_is_required(self, langfuse: Any) -> None:
        with pytest.raises(SystemExit) as exc:
            run("--dataset", DATASET, "--label", "candidate", langfuse=langfuse, llm=FakeLLM())
        assert exc.value.code == 2

    def test_limit_above_the_cap_is_rejected(self, langfuse: Any) -> None:
        llm = FakeLLM()
        with pytest.raises(SystemExit) as exc:
            run(
                "--dataset",
                DATASET,
                "--label",
                "candidate",
                "--limit",
                str(LIMIT_CAP + 1),
                langfuse=langfuse,
                llm=llm,
            )
        assert exc.value.code == 2
        assert llm.calls == []

    @pytest.mark.parametrize("limit", ["0", "-1", "ten"])
    def test_limit_must_be_a_positive_whole_number(self, langfuse: Any, limit: str) -> None:
        with pytest.raises(SystemExit):
            run(
                "--dataset",
                DATASET,
                "--label",
                "candidate",
                "--limit",
                limit,
                langfuse=langfuse,
                llm=FakeLLM(),
            )


class TestDatasetNames:
    @pytest.mark.parametrize(
        ("dataset", "operation"),
        [
            ("gym-adjustment-golden", Operation.GYM_ADJUSTMENT),
            ("cardio-plan", Operation.CARDIO_PLAN),
            ("mycoach/post-workout/golden", Operation.POST_WORKOUT),
            ("mycoach/weekly-recap-edge-cases", Operation.WEEKLY_RECAP),
        ],
    )
    def test_a_dataset_is_named_after_its_prompt(self, dataset: str, operation: Operation) -> None:
        assert operation_for_dataset(dataset) is operation

    @pytest.mark.parametrize("dataset", ["golden", "daily-briefings", "mycoach/gym"])
    def test_other_names_are_rejected(self, dataset: str) -> None:
        with pytest.raises(HarnessError):
            operation_for_dataset(dataset)
