"""Claude Code provider: coaching through the ``claude`` CLI in headless mode.

Bills against a Claude Pro subscription rather than per-token API credit. A
subscription carries no API key, so the only way a program can use it is the
Claude Code CLI, authenticated with the long-lived token from
``claude setup-token``.
"""

import json
import logging
import os
import re
import subprocess
import time
from typing import Any

from mycoach.coaching.exceptions import LLMAuthError, LLMProviderError, LLMQuotaExceeded
from mycoach.coaching.llm_client import LLMClient, LLMResponse
from mycoach.config import get_settings

logger = logging.getLogger(__name__)

# How the CLI words a rejected token and an exhausted subscription. The
# subscription limit ("You've hit your limit · resets 3pm") arrives as plain
# result text, often with no API status code to go by.
_AUTH_FAILURE = re.compile(r"authenticat|oauth token|/login|invalid api key", re.IGNORECASE)
_USAGE_LIMIT = re.compile(r"usage limit|hit your .{0,20}limit|limit reached", re.IGNORECASE)

# A briefing takes 30-90 s; anything near this is a hung CLI, and the
# scheduler job waiting on it must get its thread back.
CALL_TIMEOUT_SECONDS = 300


def _argv(*, system: str, model: str) -> list[str]:
    """The headless invocation: a plain text completion and nothing more.

    No tools, no MCP servers, no settings files, no saved session: a coaching
    prompt must not be able to touch files or the network in the container.
    Never ``--bare``: bare mode only accepts API keys, not the subscription's
    OAuth token. The user message goes on stdin, not here.
    """
    return [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--system-prompt",
        system,
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--no-session-persistence",
    ]


def _parse_result(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        return payload
    if completed.returncode != 0:
        raise _exit_error(completed)
    raise LLMProviderError(f"Claude Code: unparseable output: {completed.stdout[:200]!r}")


def _is_truncated(payload: dict[str, Any]) -> bool:
    if payload.get("stop_reason") == "max_tokens":
        return True
    # Hitting CLAUDE_CODE_MAX_OUTPUT_TOKENS can instead surface as an error
    # result: "Claude's response exceeded the N output token maximum".
    return bool(payload.get("is_error")) and "output token maximum" in str(payload.get("result"))


def _raise_for_error(payload: dict[str, Any], completed: subprocess.CompletedProcess[str]) -> None:
    if payload.get("is_error") or payload.get("subtype") != "success":
        message = str(payload.get("result") or payload.get("subtype") or "unknown error")
        status = payload.get("api_error_status")
        if status in (401, 403) or _AUTH_FAILURE.search(message):
            raise LLMAuthError(
                f"Claude Code: authentication failed ({message}). The subscription "
                "token has expired or been revoked: run `claude setup-token` on a "
                "logged-in machine and replace the CLAUDE_CODE_OAUTH_TOKEN secret."
            )
        if status == 429 or _USAGE_LIMIT.search(message):
            raise LLMQuotaExceeded(
                f"Claude Code: usage limit reached ({message}). Coaching resumes "
                "once the Claude Pro usage limit resets."
            )
        raise LLMProviderError(f"Claude Code: {message}")
    if completed.returncode != 0:
        raise _exit_error(completed)


def _exit_error(completed: subprocess.CompletedProcess[str]) -> LLMProviderError:
    stderr_tail = (completed.stderr or "").strip()[-500:] or "no error output"
    return LLMProviderError(f"Claude Code: exit {completed.returncode}: {stderr_tail}")


class ClaudeCodeClient(LLMClient):
    """LLM client backed by ``claude -p``, one subprocess per call."""

    def __init__(self, oauth_token: str | None = None) -> None:
        settings = get_settings()
        self._oauth_token = oauth_token or settings.claude_code_oauth_token
        self._model_daily = settings.claude_code_model_daily
        self._model_weekly = settings.claude_code_model_weekly

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

        try:
            completed = subprocess.run(
                _argv(system=system, model=model),
                input=user_message,
                capture_output=True,
                text=True,
                env=self._env(max_tokens),
                timeout=CALL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as e:
            raise LLMProviderError(f"Claude Code: timed out after {CALL_TIMEOUT_SECONDS} s") from e
        except FileNotFoundError as e:
            raise LLMProviderError("Claude Code: the `claude` CLI is not installed") from e

        latency_ms = int((time.monotonic() - start) * 1000)
        payload = _parse_result(completed)
        # A truncated response comes back as a response, not an error, so the
        # engine's doubled-budget retry fires as it does for other providers.
        truncated = _is_truncated(payload)
        if not truncated:
            _raise_for_error(payload, completed)

        usage = payload.get("usage") or {}
        input_tokens = (
            usage.get("input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
        )
        output_tokens = usage.get("output_tokens", 0)
        cost = float(payload.get("total_cost_usd") or 0.0)
        # The CLI takes an alias ("sonnet"); modelUsage names the model it ran.
        resolved_model = next(iter(payload.get("modelUsage") or {}), model)

        logger.info(
            "LLM call: model=%s input=%d output=%d cost=$%.4f latency=%dms",
            resolved_model,
            input_tokens,
            output_tokens,
            cost,
            latency_ms,
        )

        return LLMResponse(
            content=payload.get("result") or "",
            model=resolved_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            stop_reason="max_tokens" if truncated else payload.get("stop_reason"),
        )

    def _env(self, max_tokens: int) -> dict[str, str]:
        env = dict(os.environ)
        # Claude Code prefers an API key over the subscription token. One left
        # in the environment would quietly move billing onto a paid API.
        for paid_credential in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(paid_credential, None)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = self._oauth_token
        # The CLI has no --max-tokens flag; this is how the engine's budget
        # (and its doubled retry budget on truncation) reaches the model.
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_tokens)
        env["DISABLE_AUTOUPDATER"] = "1"
        return env

    @property
    def daily_model(self) -> str:
        return self._model_daily

    @property
    def weekly_model(self) -> str:
        return self._model_weekly
