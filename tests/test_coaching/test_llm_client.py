"""Tests for LLM client wrapper — cost estimation, call method, properties."""

from unittest.mock import MagicMock, patch

import pytest

from mycoach.coaching.llm_client import LLMClient, LLMResponse, _estimate_cost


class TestEstimateCost:
    def test_known_sonnet_model(self) -> None:
        cost = _estimate_cost("claude-sonnet-4-5-20250929", input_tokens=1000, output_tokens=500)
        # input: 1000 * 3.0 / 1M = 0.003, output: 500 * 15.0 / 1M = 0.0075
        assert cost == pytest.approx(0.0105)

    def test_known_opus_model(self) -> None:
        cost = _estimate_cost("claude-opus-4-6", input_tokens=1000, output_tokens=500)
        # input: 1000 * 15.0 / 1M = 0.015, output: 500 * 75.0 / 1M = 0.0375
        assert cost == pytest.approx(0.0525)

    def test_unknown_model_uses_opus_pricing(self) -> None:
        """Unknown models default to Opus-level pricing (most expensive)."""
        cost = _estimate_cost("claude-unknown-model", input_tokens=1000, output_tokens=500)
        expected = _estimate_cost("claude-opus-4-6", input_tokens=1000, output_tokens=500)
        assert cost == expected

    def test_zero_tokens(self) -> None:
        cost = _estimate_cost("claude-sonnet-4-5-20250929", input_tokens=0, output_tokens=0)
        assert cost == 0.0


class TestLLMClient:
    @patch("mycoach.coaching.llm_client.get_settings")
    @patch("mycoach.coaching.llm_client.anthropic.Anthropic")
    def test_call_returns_llm_response(
        self, mock_anthropic_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        settings = MagicMock()
        settings.claude_api_key = "test-key"
        settings.claude_model_daily = "claude-sonnet-4-5-20250929"
        settings.claude_model_weekly = "claude-opus-4-6"
        mock_settings.return_value = settings

        # Mock the API response
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Hello from Claude"

        mock_response = MagicMock()
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_response.content = [mock_text_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        client = LLMClient()
        result = client.call(system="You are a coach.", user_message="Hello")

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello from Claude"
        assert result.model == "claude-sonnet-4-5-20250929"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.latency_ms >= 0
        assert result.estimated_cost_usd > 0

        mock_client.messages.create.assert_called_once_with(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            system="You are a coach.",
            messages=[{"role": "user", "content": "Hello"}],
        )

    @patch("mycoach.coaching.llm_client.get_settings")
    @patch("mycoach.coaching.llm_client.anthropic.Anthropic")
    def test_call_with_explicit_model(
        self, mock_anthropic_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        settings = MagicMock()
        settings.claude_api_key = "test-key"
        settings.claude_model_daily = "claude-sonnet-4-5-20250929"
        settings.claude_model_weekly = "claude-opus-4-6"
        mock_settings.return_value = settings

        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Response"

        mock_response = MagicMock()
        mock_response.usage.input_tokens = 200
        mock_response.usage.output_tokens = 100
        mock_response.content = [mock_text_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        client = LLMClient()
        result = client.call(
            system="System", user_message="Msg", model="claude-opus-4-6", max_tokens=8192
        )

        assert result.model == "claude-opus-4-6"
        mock_client.messages.create.assert_called_once_with(
            model="claude-opus-4-6",
            max_tokens=8192,
            system="System",
            messages=[{"role": "user", "content": "Msg"}],
        )

    @patch("mycoach.coaching.llm_client.get_settings")
    @patch("mycoach.coaching.llm_client.anthropic.Anthropic")
    def test_call_concatenates_multiple_text_blocks(
        self, mock_anthropic_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        settings = MagicMock()
        settings.claude_api_key = "test-key"
        settings.claude_model_daily = "claude-sonnet-4-5-20250929"
        settings.claude_model_weekly = "claude-opus-4-6"
        mock_settings.return_value = settings

        block1 = MagicMock()
        block1.type = "text"
        block1.text = "Part 1. "
        block2 = MagicMock()
        block2.type = "text"
        block2.text = "Part 2."

        mock_response = MagicMock()
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 30
        mock_response.content = [block1, block2]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        client = LLMClient()
        result = client.call(system="Sys", user_message="Msg")
        assert result.content == "Part 1. Part 2."

    @patch("mycoach.coaching.llm_client.get_settings")
    @patch("mycoach.coaching.llm_client.anthropic.Anthropic")
    def test_call_skips_non_text_blocks(
        self, mock_anthropic_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        settings = MagicMock()
        settings.claude_api_key = "test-key"
        settings.claude_model_daily = "claude-sonnet-4-5-20250929"
        settings.claude_model_weekly = "claude-opus-4-6"
        mock_settings.return_value = settings

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Only text"
        tool_block = MagicMock()
        tool_block.type = "tool_use"

        mock_response = MagicMock()
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 30
        mock_response.content = [tool_block, text_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        client = LLMClient()
        result = client.call(system="Sys", user_message="Msg")
        assert result.content == "Only text"

    @patch("mycoach.coaching.llm_client.get_settings")
    @patch("mycoach.coaching.llm_client.anthropic.Anthropic")
    def test_daily_and_weekly_model_properties(
        self, mock_anthropic_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        settings = MagicMock()
        settings.claude_api_key = "test-key"
        settings.claude_model_daily = "claude-sonnet-4-5-20250929"
        settings.claude_model_weekly = "claude-opus-4-6"
        mock_settings.return_value = settings

        client = LLMClient()
        assert client.daily_model == "claude-sonnet-4-5-20250929"
        assert client.weekly_model == "claude-opus-4-6"

    @patch("mycoach.coaching.llm_client.get_settings")
    @patch("mycoach.coaching.llm_client.anthropic.Anthropic")
    def test_init_with_explicit_api_key(
        self, mock_anthropic_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        settings = MagicMock()
        settings.claude_api_key = "default-key"
        settings.claude_model_daily = "claude-sonnet-4-5-20250929"
        settings.claude_model_weekly = "claude-opus-4-6"
        mock_settings.return_value = settings

        LLMClient(api_key="custom-key")
        mock_anthropic_cls.assert_called_once_with(api_key="custom-key")
