import json
import logging

import pytest

from mycoach.logging_config import JSONFormatter, setup_logging


class TestJSONFormatter:
    def test_basic_format(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="mycoach.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "mycoach.test"
        assert data["message"] == "hello world"
        assert "timestamp" in data

    def test_exception_included(self) -> None:
        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                name="mycoach.test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="failed",
                args=(),
                exc_info=True,
            )
            # Must call format with the record that captured exc_info
            import sys

            record.exc_info = sys.exc_info()
        output = formatter.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError: boom" in data["exception"]

    def test_extra_fields_propagated(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="mycoach.access",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="GET /api/test 200",
            args=(),
            exc_info=None,
        )
        record.method = "GET"  # type: ignore[attr-defined]
        record.path = "/api/test"  # type: ignore[attr-defined]
        record.status_code = 200  # type: ignore[attr-defined]
        record.duration_ms = 12.3  # type: ignore[attr-defined]
        output = formatter.format(record)
        data = json.loads(output)
        assert data["method"] == "GET"
        assert data["path"] == "/api/test"
        assert data["status_code"] == 200
        assert data["duration_ms"] == 12.3

    def test_output_is_single_line(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="multi\nline\nmessage",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        # The JSON itself should be one line (no pretty-printing)
        assert "\n" not in output
        data = json.loads(output)
        assert data["message"] == "multi\nline\nmessage"


class TestSetupLogging:
    def teardown_method(self) -> None:
        # Restore root logger after each test
        root = logging.getLogger()
        root.setLevel(logging.WARNING)
        for handler in root.handlers[:]:
            root.handlers.remove(handler)

    def test_sets_root_level(self) -> None:
        setup_logging("DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_adds_json_handler(self) -> None:
        setup_logging("INFO")
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_case_insensitive(self) -> None:
        setup_logging("warning")
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_quietens_noisy_loggers(self) -> None:
        setup_logging("DEBUG")
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
        assert logging.getLogger("apscheduler").level == logging.WARNING

    def test_replaces_existing_handlers(self) -> None:
        root = logging.getLogger()
        before_count = len(root.handlers)
        root.addHandler(logging.StreamHandler())
        root.addHandler(logging.StreamHandler())
        assert len(root.handlers) == before_count + 2
        setup_logging("INFO")
        # All previous handlers replaced with exactly one JSON handler
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JSONFormatter)


class TestRequestLoggingMiddleware:
    @pytest.mark.asyncio
    async def test_logs_request(self, client, caplog) -> None:  # type: ignore[no-untyped-def]
        with caplog.at_level(logging.INFO, logger="mycoach.access"):
            resp = await client.get("/api/system/status")
        assert resp.status_code == 200
        assert any("GET" in r.message and "/api/system/status" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_logs_status_code(self, client, caplog) -> None:  # type: ignore[no-untyped-def]
        with caplog.at_level(logging.INFO, logger="mycoach.access"):
            resp = await client.get("/api/health/today")
        # 404 expected (no health data)
        assert resp.status_code == 404
        matching = [r for r in caplog.records if "/api/health/today" in r.message]
        assert len(matching) == 1
        assert "404" in matching[0].message
