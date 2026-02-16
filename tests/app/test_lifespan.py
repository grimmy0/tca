"""Tests for FastAPI application factory and lifespan hooks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from _pytest.logging import LogCaptureFixture


def test_app_lifespan_triggers_logging(caplog: LogCaptureFixture) -> None:
    """Ensure app startup and shutdown emit expected logs via lifespan."""
    caplog.set_level(logging.INFO)
    app = create_app()

    with TestClient(app):
        # Triggering lifespan by entering the context
        pass

    # Check for startup log
    assert any(  # noqa: S101
        "Starting TCA in secure-interactive mode" in record.message
        for record in caplog.records
    )

    # Check for shutdown log
    assert any(  # noqa: S101
        "Shutting down TCA" in record.message for record in caplog.records
    )


def test_create_app_initializes_logger_level() -> None:
    """Ensure create_app sets the logger level from settings."""
    app = create_app()
    assert app.title == "TCA"  # noqa: S101
    assert app.version == "0.1.0"  # noqa: S101

    assert logging.getLogger().level == logging.INFO  # noqa: S101
