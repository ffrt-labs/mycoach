"""Abstract LLM client interface and shared types.

All provider implementations (Anthropic, Claude Code, etc.) must subclass LLMClient
and implement the `call` method. The rest of the coaching engine depends only
on this interface, making providers swappable via configuration.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Literal, TypedDict

logger = logging.getLogger(__name__)

Billing = Literal["subscription", "free-tier", "paid"]
"""How a provider charges: what runs out is quota, unless it is ``paid``."""


class QuotaUsage(TypedDict):
    """What one call consumed of the provider's quota, in the provider's own unit.

    Claude Code reports a notional ``total_cost_usd`` (``usd_notional``); a
    free tier would count neurons or requests.
    """

    unit: str
    amount: float


@dataclass
class LLMResponse:
    """Result of an LLM API call.

    ``estimated_cost_usd`` is money spent only when ``billing`` is ``paid``;
    otherwise it is informational and ``quota_usage`` is what the call used up.
    """

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_usd: float
    stop_reason: str | None = None
    billing: Billing = "subscription"
    quota_usage: QuotaUsage | None = None


class LLMClient(ABC):
    """Abstract base class for LLM provider clients.

    Subclasses must implement ``call`` and expose ``daily_model`` / ``weekly_model``
    properties so the coaching engine can select the appropriate model tier.
    ``provider_name`` (the ``MYCOACH_LLM_PROVIDER`` value) is recorded on each
    Langfuse generation. ``billing`` is how the provider charges; a provider
    that does not say is taken to be ``paid``, so nothing spends money by
    omission (the evals harness refuses a paid provider without
    ``--allow-paid``).
    """

    provider_name: ClassVar[str] = ""
    billing: ClassVar[Billing] = "paid"

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
    elif provider == "claude_code":
        from mycoach.coaching.providers.claude_code import ClaudeCodeClient

        return ClaudeCodeClient()
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. Supported providers: anthropic, claude_code"
        )
