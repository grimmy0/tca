"""Tests for FastAPI application factory and lifespan hooks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from tca.api.app import StartupDependencies, create_app

if TYPE_CHECKING:
    from _pytest.logging import LogCaptureFixture


@dataclass(slots=True)
class RecordingDependency:
    """Lifecycle hook recorder for startup/shutdown call assertions."""

    startup_calls: int = 0
    shutdown_calls: int = 0

    async def startup(self) -> None:
        """Record startup invocation."""
        self.startup_calls += 1

    async def shutdown(self) -> None:
        """Record shutdown invocation."""
        self.shutdown_calls += 1


def test_app_lifespan_triggers_logging_and_hooks_once(
    caplog: LogCaptureFixture,
) -> None:
    """Ensure app startup/shutdown logs and hooks run exactly once per lifecycle."""
    caplog.set_level(logging.INFO)
    app = create_app()
    db = RecordingDependency()
    settings = RecordingDependency()
    telethon_manager = RecordingDependency()
    scheduler = RecordingDependency()
    app.state.dependencies = StartupDependencies(
        db=db,
        settings=settings,
        telethon_manager=telethon_manager,
        scheduler=scheduler,
    )

    with TestClient(app) as client:
        response = client.get("/health")
        if response.status_code != HTTPStatus.OK:
            raise AssertionError

    if db.startup_calls != 1 or telethon_manager.startup_calls != 1:
        raise AssertionError
    if settings.startup_calls != 1:
        raise AssertionError
    if scheduler.startup_calls != 1:
        raise AssertionError
    if db.shutdown_calls != 1 or telethon_manager.shutdown_calls != 1:
        raise AssertionError
    if settings.shutdown_calls != 1:
        raise AssertionError
    if scheduler.shutdown_calls != 1:
        raise AssertionError

    if not any(
        "Starting TCA in secure-interactive mode" in record.message
        for record in caplog.records
    ):
        raise AssertionError
    if not any("Shutting down TCA" in record.message for record in caplog.records):
        raise AssertionError


def test_create_app_initializes_logger_level() -> None:
    """Ensure create_app sets the logger level from settings."""
    app = create_app()
    if app.title != "TCA":
        raise AssertionError
    if app.version != "0.1.0":
        raise AssertionError

    if logging.getLogger().level != logging.INFO:
        raise AssertionError


def test_lifespan_fails_fast_on_missing_dependency_container() -> None:
    """Ensure missing app.state.dependencies fails startup with clear error."""
    app = create_app()
    del app.state.dependencies

    with (
        pytest.raises(
            RuntimeError,
            match=r"Missing startup dependency container: app\.state\.dependencies\.",
        ),
        TestClient(app),
    ):
        pass


def test_lifespan_fails_fast_on_missing_named_dependency() -> None:
    """Ensure missing dependency entries fail startup with explicit name."""
    app = create_app()
    app.state.dependencies = object()

    with (
        pytest.raises(
            RuntimeError,
            match=r"Missing startup dependency: db\.",
        ),
        TestClient(app),
    ):
        pass
