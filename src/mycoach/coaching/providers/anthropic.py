"""Anthropic Claude provider implementation."""

import logging
import time

import anthropic

from mycoach.coaching.llm_client import LLMClient, LLMResponse
from mycoach.config import get_settings

logger = logging.getLogger(__name__)

# Pricing per million tokens (as of 2025)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5-20250929": {"input": 3.0, "output": 15.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, {"input": 15.0, "output": 75.0})
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


class AnthropicClient(LLMClient):
    """LLM client backed by the Anthropic Claude API."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.claude_api_key
        self._client = anthropic.Anthropic(api_key=self._api_key)
        self._model_daily = settings.claude_model_daily
        self._model_weekly = settings.claude_model_weekly

    def call(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        model = model or self._model_daily
        start = time.monotonic()

        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )

        latency_ms = int((time.monotonic() - start) * 1000)
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        content = ""
        for block in response.content:
            if block.type == "text":
                content += block.text

        cost = _estimate_cost(model, input_tokens, output_tokens)
        logger.info(
            "LLM call: model=%s input=%d output=%d cost=$%.4f latency=%dms",
            model,
            input_tokens,
            output_tokens,
            cost,
            latency_ms,
        )

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
        )

    @property
    def daily_model(self) -> str:
        return self._model_daily

    @property
    def weekly_model(self) -> str:
        return self._model_weekly
