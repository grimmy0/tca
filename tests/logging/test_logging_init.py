"""Tests for structured logging initialization and formatting."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, cast

from tca.config.logging import correlation_id, init_logging

if TYPE_CHECKING:
    import pytest


def test_init_logging_sets_level() -> None:
    """Ensure init_logging sets the expected root logger level."""
    init_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG  # noqa: S101

    init_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING  # noqa: S101


def test_json_formatter_outputs_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Ensure JSONFormatter produces parseable JSON with core fields."""
    init_logging("INFO")
    logger = logging.getLogger("test_logger")

    msg = "Test structured message"
    logger.info(msg)

    captured = capsys.readouterr()
    log_line = captured.out.strip()

    data = cast("dict[str, object]", json.loads(log_line))
    assert data["message"] == msg  # noqa: S101
    assert data["level"] == "INFO"  # noqa: S101
    assert data["logger"] == "test_logger"  # noqa: S101
    assert "timestamp" in data  # noqa: S101
    assert data.get("correlation_id") is None  # noqa: S101


def test_json_formatter_includes_correlation_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensure log output includes the current correlation_id from context."""
    init_logging("INFO")
    logger = logging.getLogger("test_corr")

    test_id = "test-request-123"
    token = correlation_id.set(test_id)
    try:
        logger.info("Message with correlation")
    finally:
        correlation_id.reset(token)

    captured = capsys.readouterr()
    data = cast("dict[str, object]", json.loads(captured.out.strip()))
    assert data.get("correlation_id") == test_id  # noqa: S101


def test_json_formatter_includes_extra_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensure 'extra' dictionary fields are merged into the JSON root."""
    init_logging("INFO")
    logger = logging.getLogger("test_extra")

    user_id = 42
    logger.info("Extra data", extra={"user_id": user_id, "action": "login"})

    captured = capsys.readouterr()
    data = cast("dict[str, object]", json.loads(captured.out.strip()))
    assert data.get("user_id") == user_id  # noqa: S101
    assert data.get("action") == "login"  # noqa: S101
