"""Tests for the OpenRouter provider, through the real ``openai`` SDK.

OpenRouter is faked at the HTTP layer (``httpx2.MockTransport``): the tests see
the exact JSON the SDK would send and answer as OpenRouter would, with no
network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2 as httpx  # the openai SDK's HTTP client
import pytest

from mycoach.coaching.exceptions import LLMAuthError, LLMProviderError, LLMQuotaExceeded
from mycoach.coaching.providers.openrouter import OpenRouterClient

Handler = Callable[[httpx.Request], httpx.Response]


def completion(
    content: str = '{"ok": true}',
    *,
    finish_reason: str = "stop",
    prompt_tokens: int = 1200,
    completion_tokens: int = 900,
    reasoning_tokens: int = 600,
    cost: float = 0.00123,
    provider: str = "DeepInfra",
    model: str = "deepseek/deepseek-v4-pro",
) -> dict[str, Any]:
    """An OpenRouter chat completion body (``completion_tokens`` includes reasoning)."""
    return {
        "id": "gen-123",
        "object": "chat.completion",
        "created": 1_763_000_000,
        "model": model,
        "provider": provider,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": cost,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }


class FakeOpenRouter:
    """Records each request body and answers with the queued responses in turn."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def ok(**kwargs: Any) -> httpx.Response:
    return httpx.Response(200, json=completion(**kwargs))


def error(status: int, message: str = "upstream said no") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": message}})


@pytest.fixture(autouse=True)
def _openrouter_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYCOACH_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("MYCOACH_OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("MYCOACH_OPENROUTER_MODEL_DAILY", "deepseek/deepseek-v4-pro")
    monkeypatch.setenv("MYCOACH_OPENROUTER_MODEL_WEEKLY", "moonshotai/kimi-k3.1-pro")


def client_for(fake: FakeOpenRouter) -> OpenRouterClient:
    return OpenRouterClient(
        http_client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
        max_retries=0,
    )


class TestRequest:
    def test_every_request_demands_zero_retention_and_no_training(self) -> None:
        fake = FakeOpenRouter(ok(), ok())
        client = client_for(fake)

        client.call(system="You are a coach.", user_message="Hello")
        client.call(system="S", user_message="U", model="moonshotai/kimi-k3.1-pro")

        for body in fake.bodies:
            assert body["provider"] == {"zdr": True, "data_collection": "deny"}

    def test_no_serving_host_is_pinned(self) -> None:
        fake = FakeOpenRouter(ok())
        client_for(fake).call(system="S", user_message="U")

        provider = fake.bodies[0]["provider"]
        assert "order" not in provider
        assert "only" not in provider

    def test_reasoning_runs_at_medium_effort_and_is_kept_out_of_the_answer(self) -> None:
        fake = FakeOpenRouter(ok())
        client_for(fake).call(system="S", user_message="U")

        assert fake.bodies[0]["reasoning"] == {"effort": "medium", "exclude": True}

    def test_sends_system_and_user_messages_to_the_daily_model_by_default(self) -> None:
        fake = FakeOpenRouter(ok())
        client_for(fake).call(system="You are a coach.", user_message="Hello")

        body = fake.bodies[0]
        assert body["model"] == "deepseek/deepseek-v4-pro"
        assert body["messages"] == [
            {"role": "system", "content": "You are a coach."},
            {"role": "user", "content": "Hello"},
        ]
        assert "response_format" not in body
        assert fake.requests[0].url == "https://openrouter.ai/api/v1/chat/completions"
        assert fake.requests[0].headers["authorization"] == "Bearer sk-or-test"

    def test_max_tokens_adds_reasoning_headroom_to_the_answer_budget(self) -> None:
        """Medium effort lets reasoning take half of ``max_tokens``, so the
        caller's answer budget is sent doubled: 4096 of answer, 4096 to think."""
        fake = FakeOpenRouter(ok(), ok())
        client = client_for(fake)

        client.call(system="S", user_message="U", max_tokens=4096)
        client.call(system="S", user_message="U", max_tokens=8192)

        assert [b["max_tokens"] for b in fake.bodies] == [8192, 16384]


class TestResponse:
    def test_returns_the_answer_with_paid_billing_and_openrouters_own_cost(self) -> None:
        fake = FakeOpenRouter(ok(content='{"verdict": "go_hard"}', cost=0.00321))
        result = client_for(fake).call(system="S", user_message="U")

        assert result.content == '{"verdict": "go_hard"}'
        assert result.billing == "paid"
        assert result.estimated_cost_usd == pytest.approx(0.00321)
        assert result.quota_usage == {"unit": "usd", "amount": pytest.approx(0.00321)}
        assert result.latency_ms >= 0

    def test_reasoning_tokens_are_reported_apart_from_the_answer(self) -> None:
        """``completion_tokens`` includes reasoning; the answer is the rest."""
        fake = FakeOpenRouter(ok(prompt_tokens=1200, completion_tokens=900, reasoning_tokens=600))
        result = client_for(fake).call(system="S", user_message="U")

        assert result.input_tokens == 1200
        assert result.output_tokens == 300
        assert result.reasoning_tokens == 600

    def test_records_the_serving_host_and_the_model_that_ran(self) -> None:
        fake = FakeOpenRouter(ok(provider="Fireworks", model="deepseek/deepseek-v4-pro-0601"))
        result = client_for(fake).call(system="S", user_message="U")

        assert result.serving_host == "Fireworks"
        assert result.model == "deepseek/deepseek-v4-pro-0601"

    def test_a_complete_answer_keeps_its_finish_reason(self) -> None:
        fake = FakeOpenRouter(ok(finish_reason="stop"))
        result = client_for(fake).call(system="S", user_message="U")

        assert result.stop_reason == "stop"

    def test_finish_reason_length_is_a_max_tokens_stop(self) -> None:
        """The engine's doubled-budget truncation retry keys on ``max_tokens``."""
        fake = FakeOpenRouter(ok(content='{"sleep_assess', finish_reason="length"))
        result = client_for(fake).call(system="S", user_message="U")

        assert result.stop_reason == "max_tokens"
        assert result.content == '{"sleep_assess'

    def test_missing_usage_details_count_as_zero(self) -> None:
        body = completion()
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        fake = FakeOpenRouter(httpx.Response(200, json=body))
        result = client_for(fake).call(system="S", user_message="U")

        assert result.output_tokens == 5
        assert result.reasoning_tokens == 0
        assert result.estimated_cost_usd == 0.0


class TestErrors:
    def test_exhausted_credits_are_a_quota_error(self) -> None:
        fake = FakeOpenRouter(error(402, "Insufficient credits"))
        with pytest.raises(LLMQuotaExceeded, match="credits"):
            client_for(fake).call(system="S", user_message="U")

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_key_is_an_auth_error(self, status: int) -> None:
        fake = FakeOpenRouter(error(status, "No auth credentials found"))
        with pytest.raises(LLMAuthError, match="MYCOACH_OPENROUTER_API_KEY"):
            client_for(fake).call(system="S", user_message="U")

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_rate_limits_and_server_errors_are_provider_errors(self, status: int) -> None:
        fake = FakeOpenRouter(error(status))
        with pytest.raises(LLMProviderError) as raised:
            client_for(fake).call(system="S", user_message="U")
        assert type(raised.value) is LLMProviderError
        assert str(status) in str(raised.value)

    def test_no_compliant_endpoint_fails_closed(self) -> None:
        """With no zero-retention, no-training host the call fails; nothing reroutes."""
        fake = FakeOpenRouter(
            error(404, "No endpoints found matching your data policy (Zero data retention)")
        )
        with pytest.raises(LLMProviderError, match="data policy") as raised:
            client_for(fake).call(system="S", user_message="U")
        assert type(raised.value) is LLMProviderError
        assert len(fake.requests) == 1

    def test_an_error_inside_a_200_response_is_a_provider_error(self) -> None:
        """OpenRouter can report an upstream failure in the body of a 200."""
        body = {"error": {"code": 502, "message": "Provider returned error"}, "choices": []}
        fake = FakeOpenRouter(httpx.Response(200, json=body))
        with pytest.raises(LLMProviderError, match="Provider returned error"):
            client_for(fake).call(system="S", user_message="U")

    def test_an_unreachable_openrouter_is_a_provider_error(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = OpenRouterClient(
            http_client=httpx.Client(transport=httpx.MockTransport(refuse)), max_retries=0
        )
        with pytest.raises(LLMProviderError, match="OpenRouter"):
            client.call(system="S", user_message="U")
