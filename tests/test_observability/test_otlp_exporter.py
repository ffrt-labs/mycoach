"""MyCoach builds the span exporter itself (to wrap it), so it must match the SDK's.

The SDK has no public way to wrap its own exporter: passing ``span_exporter``
replaces it, headers included. This test fails when a Langfuse upgrade
changes what the SDK's own exporter sends.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from langfuse._client import span_processor

from mycoach.observability import tracing

BASE_URL = "http://langfuse-web:3000"


def _exporter_kwargs(monkeypatch: pytest.MonkeyPatch, module: Any, build: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def record(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(module, "OTLPSpanExporter", record)
    built = build()
    if isinstance(built, span_processor.LangfuseSpanProcessor):
        built.shutdown()  # stop its export thread
    return captured


def test_our_exporter_sends_what_the_sdks_own_exporter_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGFUSE_OTEL_TRACES_EXPORT_PATH", raising=False)
    sdk = _exporter_kwargs(
        monkeypatch,
        span_processor,
        lambda: span_processor.LangfuseSpanProcessor(
            public_key="pk-lf", secret_key="sk-lf", base_url=BASE_URL
        ),
    )
    ours = _exporter_kwargs(
        monkeypatch, tracing, lambda: tracing._otlp_exporter(BASE_URL, "pk-lf", "sk-lf")
    )

    assert ours["headers"] == sdk["headers"]
    assert ours["endpoint"] == sdk["endpoint"]
    assert ours["compression"] == sdk["compression"]
