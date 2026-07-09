"""Google Gemini provider implementation."""

import logging
import time

from google import genai
from google.genai import types

from mycoach.coaching.llm_client import LLMClient, LLMResponse
from mycoach.config import get_settings

logger = logging.getLogger(__name__)

# Pricing per million tokens (as of 2026)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, {"input": 1.25, "output": 10.0})
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


class GeminiClient(LLMClient):
    """LLM client backed by the Google Gemini API."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.gemini_api_key
        self._client = genai.Client(api_key=self._api_key)
        self._model_daily = settings.gemini_model_daily
        self._model_weekly = settings.gemini_model_weekly

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

        response = self._client.models.generate_content(
            model=model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )

        latency_ms = int((time.monotonic() - start) * 1000)

        content = response.text or ""
        input_tokens = response.usage_metadata.prompt_token_count or 0
        output_tokens = response.usage_metadata.candidates_token_count or 0

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
