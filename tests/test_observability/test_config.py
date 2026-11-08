"""Langfuse is on only when all three of its variables are set."""

import logging
from collections.abc import Generator
from typing import Any

import pytest
from langfuse._client.resource_manager import LangfuseResourceManager

from mycoach.config import Settings
from mycoach.observability.tracing import announce_langfuse_config, build_tracer


@pytest.fixture(autouse=True)
def _reset_langfuse() -> Generator[None, None, None]:
    LangfuseResourceManager.reset()
    yield
    LangfuseResourceManager.reset()


def _settings(**langfuse: Any) -> Settings:
    return Settings(llm_provider="anthropic", claude_api_key="test", env="test", **langfuse)


@pytest.mark.parametrize(
    ("given", "missing"),
    [
        ({}, "LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY"),
        # Keys without a base URL must not fall through to Langfuse Cloud.
        ({"langfuse_public_key": "pk-lf", "langfuse_secret_key": "sk-lf"}, "LANGFUSE_BASE_URL"),
        (
            {"langfuse_base_url": "http://langfuse-web:3000", "langfuse_public_key": "pk-lf"},
            "LANGFUSE_SECRET_KEY",
        ),
    ],
)
def test_disabled_unless_all_three_are_set(
    given: dict[str, str], missing: str, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(**given)

    assert build_tracer(settings).enabled is False

    with caplog.at_level(logging.INFO, logger="mycoach.observability.tracing"):
        announce_langfuse_config(settings)
    assert f"Langfuse disabled: missing {missing}" in caplog.text
    assert "pk-lf" not in caplog.text and "sk-lf" not in caplog.text


def test_enabled_with_all_three(caplog: pytest.LogCaptureFixture) -> None:
    settings = _settings(
        langfuse_base_url="http://langfuse-web:3000",
        langfuse_public_key="pk-lf-config-test",
        langfuse_secret_key="sk-lf-config-test",
    )

    tracer = build_tracer(settings)
    try:
        assert tracer.enabled is True
    finally:
        tracer.shutdown()

    with caplog.at_level(logging.INFO, logger="mycoach.observability.tracing"):
        announce_langfuse_config(settings)
    assert "Langfuse enabled: http://langfuse-web:3000" in caplog.text
    assert "pk-lf-config-test" not in caplog.text
    assert "sk-lf-config-test" not in caplog.text
