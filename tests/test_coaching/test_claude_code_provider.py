"""Tests for the Claude Code provider, at the subprocess boundary.

No test runs the real ``claude`` CLI: ``subprocess.run`` is patched in the
provider module, and each test asserts what goes into the CLI and what
``LLMResponse`` (or error) comes back out.
"""

import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mycoach.coaching.exceptions import LLMAuthError, LLMProviderError, LLMQuotaExceeded
from mycoach.coaching.llm_client import LLMResponse
from mycoach.coaching.providers.claude_code import ClaudeCodeClient

RUN = "mycoach.coaching.providers.claude_code.subprocess.run"


def _result_payload(**overrides: Any) -> dict[str, Any]:
    """A ``claude -p --output-format json`` result, shaped like the real CLI's."""
    payload: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": '{"readiness_verdict": "go_hard"}',
        "stop_reason": "end_turn",
        "total_cost_usd": 0.0421,
        "usage": {
            "input_tokens": 12,
            "cache_creation_input_tokens": 3000,
            "cache_read_input_tokens": 500,
            "output_tokens": 640,
        },
        "modelUsage": {"claude-sonnet-4-5-20250929": {"inputTokens": 12, "outputTokens": 640}},
    }
    payload.update(overrides)
    return payload


def _completed(
    payload: dict[str, Any] | None = None, *, returncode: int = 0, stdout: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["claude"],
        returncode=returncode,
        stdout=stdout if stdout is not None else json.dumps(payload or _result_payload()),
        stderr="",
    )


def _call(**patch_kwargs: Any) -> LLMResponse:
    """One call through a client whose ``subprocess.run`` is patched as given."""
    with patch(RUN, **patch_kwargs):
        return _client().call(system="Sys", user_message="Msg")


def _client() -> ClaudeCodeClient:
    with patch("mycoach.coaching.providers.claude_code.get_settings") as mock_settings:
        settings = MagicMock()
        settings.claude_code_oauth_token = "sk-ant-oat01-test"
        settings.claude_code_model_daily = "sonnet"
        settings.claude_code_model_weekly = "opus"
        mock_settings.return_value = settings
        return ClaudeCodeClient()


class TestSuccessfulCall:
    def test_maps_cli_result_to_llm_response(self) -> None:
        with patch(RUN, return_value=_completed()):
            result = _client().call(system="You are a coach.", user_message="Brief me.")

        assert isinstance(result, LLMResponse)
        assert result.content == '{"readiness_verdict": "go_hard"}'
        # The prompt's full size: uncached + cache-written + cache-read tokens.
        assert result.input_tokens == 3512
        assert result.output_tokens == 640
        assert result.estimated_cost_usd == pytest.approx(0.0421)
        assert result.stop_reason == "end_turn"
        assert result.latency_ms >= 0

    def test_records_the_model_the_alias_resolved_to(self) -> None:
        with patch(RUN, return_value=_completed()):
            result = _client().call(system="Sys", user_message="Msg")

        assert result.model == "claude-sonnet-4-5-20250929"

    def test_falls_back_to_the_requested_model_without_model_usage(self) -> None:
        with patch(RUN, return_value=_completed(_result_payload(modelUsage={}))):
            result = _client().call(system="Sys", user_message="Msg", model="opus")

        assert result.model == "opus"


class TestInvocation:
    def _invoke(self, **call_kwargs: Any) -> MagicMock:
        with patch(RUN, return_value=_completed()) as mock_run:
            _client().call(
                **{"system": "You are a coach.", "user_message": "Brief me.", **call_kwargs}
            )
        return mock_run

    def test_runs_headless_with_model_and_system_prompt(self) -> None:
        argv = self._invoke(model="opus").call_args.args[0]

        assert argv[:2] == ["claude", "-p"]
        assert argv[argv.index("--output-format") + 1] == "json"
        assert argv[argv.index("--model") + 1] == "opus"
        assert argv[argv.index("--system-prompt") + 1] == "You are a coach."

    def test_defaults_to_the_daily_model(self) -> None:
        argv = self._invoke().call_args.args[0]

        assert argv[argv.index("--model") + 1] == "sonnet"

    def test_runs_as_a_plain_completion_with_nothing_to_act_through(self) -> None:
        argv = self._invoke().call_args.args[0]

        assert argv[argv.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in argv
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert "--no-session-persistence" in argv

    def test_never_runs_bare_because_bare_mode_ignores_the_oauth_token(self) -> None:
        argv = self._invoke().call_args.args[0]

        assert "--bare" not in argv

    def test_sends_the_prompt_on_stdin_not_argv(self) -> None:
        mock_run = self._invoke(user_message="Brief me on today.")

        assert mock_run.call_args.kwargs["input"] == "Brief me on today."
        assert "Brief me on today." not in mock_run.call_args.args[0]

    def test_passes_token_and_output_budget_through_the_environment(self) -> None:
        env = self._invoke(max_tokens=8192).call_args.kwargs["env"]

        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-test"
        assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8192"
        assert env["DISABLE_AUTOUPDATER"] == "1"

    def test_strips_api_credentials_so_it_never_bills_a_paid_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Claude Code prefers an API key over the subscription token when both exist."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-paid")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "paid-token")

        env = self._invoke().call_args.kwargs["env"]

        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_bounds_each_call_with_a_timeout(self) -> None:
        assert self._invoke().call_args.kwargs["timeout"] == 300


class TestTruncation:
    def test_truncated_response_reports_max_tokens(self) -> None:
        payload = _result_payload(stop_reason="max_tokens")
        with patch(RUN, return_value=_completed(payload)):
            result = _client().call(system="Sys", user_message="Msg")

        assert result.stop_reason == "max_tokens"

    def test_output_cap_error_is_reported_as_truncation_not_raised(self) -> None:
        """The engine's doubled-budget retry needs a response, not an exception."""
        payload = _result_payload(
            is_error=True,
            stop_reason=None,
            result=(
                "API Error: Claude's response exceeded the 4096 output token maximum. "
                "To configure this behavior, set the CLAUDE_CODE_MAX_OUTPUT_TOKENS "
                "environment variable."
            ),
        )
        with patch(RUN, return_value=_completed(payload, returncode=1)):
            result = _client().call(system="Sys", user_message="Msg")

        assert result.stop_reason == "max_tokens"


class TestFailures:
    def test_usage_limit_raises_quota_error(self) -> None:
        payload = _result_payload(
            is_error=True, result="You've hit your limit · resets 3pm (Europe/London)"
        )
        with pytest.raises(LLMQuotaExceeded, match="Claude Code: usage limit reached"):
            _call(return_value=_completed(payload, returncode=1))

    def test_rate_limited_api_status_raises_quota_error(self) -> None:
        payload = _result_payload(is_error=True, api_error_status=429, result="API Error: 429")
        with pytest.raises(LLMQuotaExceeded):
            _call(return_value=_completed(payload, returncode=1))

    def test_rejected_token_raises_auth_error_naming_the_remedy(self) -> None:
        # Verbatim from the real CLI given an invalid CLAUDE_CODE_OAUTH_TOKEN.
        payload = _result_payload(
            is_error=True,
            api_error_status=401,
            result="Failed to authenticate. API Error: 401 Invalid bearer token",
        )
        with pytest.raises(LLMAuthError, match="CLAUDE_CODE_OAUTH_TOKEN") as exc_info:
            _call(return_value=_completed(payload, returncode=1))

        assert "claude setup-token" in str(exc_info.value)

    def test_other_cli_errors_raise_provider_error(self) -> None:
        payload = _result_payload(is_error=True, subtype="error_during_execution", result="")
        with pytest.raises(LLMProviderError, match="error_during_execution"):
            _call(return_value=_completed(payload, returncode=1))

    def test_typed_errors_are_provider_errors(self) -> None:
        assert issubclass(LLMQuotaExceeded, LLMProviderError)
        assert issubclass(LLMAuthError, LLMProviderError)
        assert not issubclass(LLMQuotaExceeded, LLMAuthError)

    def test_timeout_raises_provider_error(self) -> None:
        with pytest.raises(LLMProviderError, match="timed out after 300 s"):
            _call(side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300))

    def test_missing_binary_raises_provider_error(self) -> None:
        with pytest.raises(LLMProviderError, match="not installed"):
            _call(side_effect=FileNotFoundError("claude"))

    def test_crash_with_no_json_raises_provider_error_with_stderr(self) -> None:
        crashed = subprocess.CompletedProcess(
            args=["claude"], returncode=139, stdout="", stderr="Segmentation fault"
        )
        with pytest.raises(LLMProviderError, match="exit 139.*Segmentation fault"):
            _call(return_value=crashed)

    def test_non_zero_exit_without_an_error_flag_still_raises(self) -> None:
        with pytest.raises(LLMProviderError, match="exit 2"):
            _call(return_value=_completed(returncode=2))

    def test_unparseable_output_raises_provider_error(self) -> None:
        with pytest.raises(LLMProviderError, match="unparseable"):
            _call(return_value=_completed(stdout="Welcome to Claude Code!\n"))
