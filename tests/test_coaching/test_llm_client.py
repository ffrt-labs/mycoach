"""Tests for the LLM client interface and the provider factory.

Each provider has its own module: test_openrouter_provider.py and
test_claude_code_provider.py.
"""

import pytest

from mycoach.coaching.llm_client import LLMClient, get_llm_client
from mycoach.coaching.providers.claude_code import ClaudeCodeClient
from mycoach.coaching.providers.openrouter import OpenRouterClient


@pytest.fixture(autouse=True)
def _provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYCOACH_OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")


class TestGetLLMClient:
    def test_factory_returns_openrouter_when_no_provider_is_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MYCOACH_LLM_PROVIDER", raising=False)
        client = get_llm_client()
        assert isinstance(client, OpenRouterClient)
        assert client.provider_name == "openrouter"
        assert client.billing == "paid"

    def test_factory_returns_openrouter_when_selected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYCOACH_LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("MYCOACH_OPENROUTER_MODEL_DAILY", "deepseek/deepseek-v4-pro")
        monkeypatch.setenv("MYCOACH_OPENROUTER_MODEL_WEEKLY", "moonshotai/kimi-k3.1-pro")
        client = get_llm_client()
        assert isinstance(client, OpenRouterClient)
        assert client.daily_model == "deepseek/deepseek-v4-pro"
        assert client.weekly_model == "moonshotai/kimi-k3.1-pro"

    def test_factory_returns_claude_code_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MYCOACH_LLM_PROVIDER", "claude_code")
        client = get_llm_client()
        assert isinstance(client, ClaudeCodeClient)
        assert client.daily_model == "sonnet"

    def test_factory_rejects_the_removed_anthropic_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Settings refuse it at boot, naming the providers that do exist."""
        monkeypatch.setenv("MYCOACH_LLM_PROVIDER", "anthropic")
        with pytest.raises(ValueError, match=r"'anthropic'.*claude_code.*openrouter"):
            get_llm_client()

    def test_llm_client_is_abstract(self) -> None:
        """Cannot instantiate LLMClient directly."""
        with pytest.raises(TypeError):
            LLMClient()  # type: ignore[abstract]
