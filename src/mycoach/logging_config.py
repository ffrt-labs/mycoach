"""Structured logging configuration for MyCoach.

Sets up JSON-formatted log output so that log entries are machine-parseable
while remaining human-readable.  Called once at app startup via ``setup_logging()``.
"""

import json
import logging
import sys
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Propagate extra fields attached by callers (e.g. request_id, job run facts)
        for key in (
            "request_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "job_name",
            "job_status",
            "job_error",
        ):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value
        return json.dumps(log_entry, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger with JSON output to stderr.

    Should be called once at application startup, before any log calls.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Remove any existing handlers (e.g. from basicConfig or uvicorn defaults)
    for handler in root.handlers[:]:
        root.handlers.remove(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Quieten noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
