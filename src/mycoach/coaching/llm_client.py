"""Abstract LLM client interface and shared types.

All provider implementations (Anthropic, Gemini, etc.) must subclass LLMClient
and implement the `call` method. The rest of the coaching engine depends only
on this interface, making providers swappable via configuration.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Result of an LLM API call."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_usd: float
    stop_reason: str | None = None


class LLMClient(ABC):
    """Abstract base class for LLM provider clients.

    Subclasses must implement ``call`` and expose ``daily_model`` / ``weekly_model``
    properties so the coaching engine can select the appropriate model tier.
    """

    @abstractmethod
    def call(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a prompt to the LLM and return the response with usage metadata.

        Args:
            system: System prompt (coaching persona + instructions).
            user_message: User-facing prompt with assembled context.
            model: Model ID override. Defaults to the provider's daily model.
            max_tokens: Maximum tokens in the response.

        Returns:
            LLMResponse with content and usage stats.
        """

    @property
    @abstractmethod
    def daily_model(self) -> str:
        """Model ID used for routine daily tasks (cost-optimised)."""

    @property
    @abstractmethod
    def weekly_model(self) -> str:
        """Model ID used for weekly plan generation (quality-optimised)."""


def get_llm_client() -> LLMClient:
    """Factory — returns the LLM client for the configured provider.

    Reads ``MYCOACH_LLM_PROVIDER`` from settings and instantiates the
    matching provider. Defaults to ``"anthropic"`` for backward compatibility.
    """
    from mycoach.config import get_settings

    settings = get_settings()
    provider = settings.llm_provider.lower()

    if provider == "anthropic":
        from mycoach.coaching.providers.anthropic import AnthropicClient

        return AnthropicClient()
    elif provider == "gemini":
        from mycoach.coaching.providers.gemini import GeminiClient

        return GeminiClient()
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. Supported providers: anthropic, gemini"
        )
