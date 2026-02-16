"""Logging contract tests that protect structured log field invariants."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, cast

from tca.config.logging import correlation_id, init_logging

if TYPE_CHECKING:
    import pytest


def test_structured_logs_always_include_core_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensure every JSON log line includes core structured keys."""
    init_logging("INFO")
    logger = logging.getLogger("contract_logger")
    logger.info("contract message")

    data = cast("dict[str, object]", json.loads(capsys.readouterr().out.strip()))
    required_fields = {"timestamp", "level", "message", "logger", "correlation_id"}
    if not required_fields.issubset(set(data.keys())):
        raise AssertionError


def test_structured_logs_do_not_allow_core_field_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensure core fields cannot be clobbered via extra payload."""
    init_logging("INFO")
    logger = logging.getLogger("contract_logger")
    token = correlation_id.set("cid-real")
    try:
        logger.info(
            "protected",
            extra={"level": "OVERRIDE", "correlation_id": "cid-forged"},
        )
    finally:
        correlation_id.reset(token)

    data = cast("dict[str, object]", json.loads(capsys.readouterr().out.strip()))
    if data.get("level") != "INFO":
        raise AssertionError
    if data.get("correlation_id") != "cid-real":
        raise AssertionError
    if data.get("extra_level") != "OVERRIDE":
        raise AssertionError
    if data.get("extra_correlation_id") != "cid-forged":
        raise AssertionError
