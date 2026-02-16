"""Structured logging configuration and initialization."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast, override

if TYPE_CHECKING:
    from tca.config.settings import LogLevel

# Correlation ID context variable for request tracing
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


class JSONFormatter(logging.Formatter):
    """Custom formatter to output logs as single-line JSON."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a JSON string."""
        log_data: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=UTC,
            ).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "correlation_id": correlation_id.get(),
        }

        # Include exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Include stack info if present
        if record.stack_info:
            log_data["stack_trace"] = self.formatStack(record.stack_info)

        # Include extra fields from the record
        protected_attrs = set(log_data.keys())
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
        }

        # record.__dict__ contains Any, cast it to avoid BasedPyright issues
        record_dict = cast("dict[str, object]", record.__dict__)
        for key, value in record_dict.items():
            if key in standard_attrs or key.startswith("_"):
                continue
            if key in protected_attrs:
                log_data[f"extra_{key}"] = value
                continue
            log_data[key] = value

        return json.dumps(log_data)


def init_logging(level: LogLevel) -> None:
    """Initialize structured logging for the application."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove any existing handlers to avoid double logging,
    # but preserve pytest handlers if they exist.
    for h in root_logger.handlers[:]:
        if type(h).__name__ != "LogCaptureHandler":
            root_logger.removeHandler(h)

    root_logger.addHandler(handler)
