"""OpenRouter provider: coaching through OpenRouter's OpenAI-compatible API.

Pay-as-you-go against prepaid credits that stop at zero (ADR 0004). The rule
that the provider must not train on or keep the health context is enforced
here, on every request, not only by the account's privacy setting.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import openai

from mycoach.coaching.exceptions import LLMAuthError, LLMProviderError, LLMQuotaExceeded
from mycoach.coaching.llm_client import LLMClient, LLMResponse
from mycoach.coaching.strict_schema import ResponseSchema
from mycoach.config import get_settings

if TYPE_CHECKING:
    import httpx2  # the openai SDK's HTTP client

logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"

# Only endpoints that keep nothing (zero data retention) and never train on
# prompts. No host is pinned: OpenRouter routes among the compliant ones, and
# with none available the request fails rather than going elsewhere.
PROVIDER_PREFERENCES: dict[str, Any] = {"zdr": True, "data_collection": "deny"}

# The model thinks, but only the answer comes back.
REASONING: dict[str, Any] = {"effort": "medium", "exclude": True}

# The least room left for reasoning on top of the answer budget.
REASONING_BUDGET_TOKENS = 8192

# Opts in to ``openrouter_metadata`` on the response, which is where OpenRouter
# documents the endpoint that served the call.
METADATA_HEADERS = {"X-OpenRouter-Metadata": "enabled"}

# A briefing takes well under a minute; anything near this is a hung request.
CALL_TIMEOUT_SECONDS = 300


def _upstream_max_tokens(answer_tokens: int) -> int:
    """The caller's ``max_tokens`` is the answer budget; reasoning gets its own.

    Reasoning tokens count toward the request's ``max_tokens``. OpenRouter only
    documents ``effort: medium`` as capping reasoning at half the limit for
    OpenAI and Grok models, not for the DeepSeek, Kimi or GLM models MyCoach
    uses, so reasoning gets a fixed floor rather than a share of the limit.
    """
    return answer_tokens + max(answer_tokens, REASONING_BUDGET_TOKENS)


# The ``supported_parameters`` entry of a model that takes a strict JSON schema.
STRUCTURED_OUTPUTS = "structured_outputs"


def _response_format(schema: ResponseSchema) -> dict[str, Any]:
    """A strict ``json_schema`` response format (spec #230, prompt v5)."""
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.name, "strict": True, "schema": schema.schema},
    }


def _status_error(e: openai.APIStatusError) -> LLMProviderError:
    """Map an HTTP error onto MyCoach's provider-error vocabulary.

    Everything else, including 429, 5xx and a 404 when no endpoint meets the
    data policy, is a plain provider error: the request is never re-sent
    somewhere less private.
    """
    status = e.status_code
    if status == 402:
        return LLMQuotaExceeded(
            f"OpenRouter: credits exhausted ({e.message}). Coaching resumes once "
            "the OpenRouter account is topped up."
        )
    if status in (401, 403):
        return LLMAuthError(
            f"OpenRouter: authentication failed ({e.message}). Check the "
            "MYCOACH_OPENROUTER_API_KEY secret."
        )
    return LLMProviderError(f"OpenRouter: HTTP {status}: {e.message}")


def _extra(obj: Any, field: str) -> Any:
    """A field OpenRouter adds to the OpenAI schema (the SDK keeps extras)."""
    if obj is None:
        return None
    return (getattr(obj, "model_extra", None) or {}).get(field)


def _serving_host(response: Any) -> str | None:
    """The provider that served the call.

    Read from the endpoint ``openrouter_metadata`` marks ``selected``; the
    undocumented top-level ``provider`` field is the fallback.
    """
    metadata = _extra(response, "openrouter_metadata")
    if isinstance(metadata, dict):
        endpoints = (metadata.get("endpoints") or {}).get("available") or []
        for endpoint in endpoints:
            if isinstance(endpoint, dict) and endpoint.get("selected"):
                return endpoint.get("provider")
    host = _extra(response, "provider")
    return host if isinstance(host, str) else None


class OpenRouterClient(LLMClient):
    """LLM client backed by OpenRouter's chat completions endpoint."""

    provider_name = "openrouter"
    # Prepaid credits: what each call spends is money.
    billing = "paid"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        http_client: httpx2.Client | None = None,
        max_retries: int = 2,
    ) -> None:
        settings = get_settings()
        self._client = openai.OpenAI(
            api_key=api_key or settings.openrouter_api_key,
            base_url=BASE_URL,
            timeout=CALL_TIMEOUT_SECONDS,
            max_retries=max_retries,
            http_client=http_client,
        )
        self._model_daily = settings.openrouter_model_daily
        self._model_weekly = settings.openrouter_model_weekly
        # Model id -> its supported_parameters, read once on the first call with a schema.
        self._model_parameters: dict[str, frozenset[str]] | None = None

    def call(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None = None,
        max_tokens: int = 4096,
        response_schema: ResponseSchema | None = None,
    ) -> LLMResponse:
        model = model or self._model_daily
        extra_body: dict[str, Any] = {"provider": PROVIDER_PREFERENCES, "reasoning": REASONING}
        if response_schema is not None and self._supports_structured_outputs(model):
            # Not ``require_parameters``: that could leave no zero-retention
            # endpoint. One that ignores the schema is caught by Pydantic.
            extra_body["response_format"] = _response_format(response_schema)
        start = time.monotonic()

        try:
            response = self._client.chat.completions.create(
                model=model,
                max_tokens=_upstream_max_tokens(max_tokens),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message},
                ],
                extra_body=extra_body,
                extra_headers=METADATA_HEADERS,
            )
        except openai.APIStatusError as e:
            raise _status_error(e) from e
        except openai.APIError as e:
            raise LLMProviderError(f"OpenRouter: {e}") from e

        latency_ms = int((time.monotonic() - start) * 1000)
        upstream_error = _extra(response, "error")
        if upstream_error or not response.choices:
            message = (
                upstream_error.get("message", upstream_error)
                if isinstance(upstream_error, dict)
                else upstream_error or "no choices in the response"
            )
            raise LLMProviderError(f"OpenRouter: {message}")
        choice = response.choices[0]
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        details = usage.completion_tokens_details if usage else None
        reasoning_tokens = (details.reasoning_tokens or 0) if details else 0
        answer_tokens = completion_tokens - reasoning_tokens
        # OpenRouter's own charge for the call, not a price table.
        cost = float(_extra(usage, "cost") or 0.0)
        serving_host = _serving_host(response)

        logger.info(
            "LLM call: model=%s host=%s input=%d output=%d reasoning=%d cost=$%.4f latency=%dms",
            response.model,
            serving_host,
            input_tokens,
            answer_tokens,
            reasoning_tokens,
            cost,
            latency_ms,
        )

        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            input_tokens=input_tokens,
            output_tokens=answer_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            # A truncated answer comes back as a response, so the engine's
            # doubled-budget retry fires on it.
            stop_reason="max_tokens" if choice.finish_reason == "length" else choice.finish_reason,
            billing=self.billing,
            quota_usage={"unit": "usd", "amount": cost},
            reasoning_tokens=reasoning_tokens,
            serving_host=serving_host,
        )

    def _supports_structured_outputs(self, model: str) -> bool:
        """Whether OpenRouter lists ``structured_outputs`` for ``model``.

        The model list is read once per client. A failed read is not cached and
        sends this call without a schema: the prompt still carries it.
        """
        if self._model_parameters is None:
            try:
                listed = self._client.models.list()
            except openai.APIError as e:
                logger.warning("OpenRouter: model list unavailable (%s); no strict schema", e)
                return False
            self._model_parameters = {
                m.id: frozenset(_extra(m, "supported_parameters") or ()) for m in listed
            }
        return STRUCTURED_OUTPUTS in self._model_parameters.get(model, frozenset())

    @property
    def daily_model(self) -> str:
        return self._model_daily

    @property
    def weekly_model(self) -> str:
        return self._model_weekly
