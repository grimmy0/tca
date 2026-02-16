"""Tests for FastAPI application factory and lifespan hooks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import AsyncMock, patch

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


@dataclass(slots=True)
class FailingStartupDependency:
    """Lifecycle hook that raises on startup for failure-path tests."""

    startup_calls: int = 0
    shutdown_calls: int = 0
    error_message: str = "forced-startup-failure"

    async def startup(self) -> None:
        """Raise deterministic startup failure."""
        self.startup_calls += 1
        raise RuntimeError(self.error_message)

    async def shutdown(self) -> None:
        """Record shutdown invocation."""
        self.shutdown_calls += 1


@dataclass(slots=True)
class InvalidWriterQueueRuntime:
    """Queue-like object with non-callable lifecycle attributes."""

    submit: int = 1
    close: int = 2


def test_app_lifespan_triggers_logging_and_hooks_once(
    caplog: LogCaptureFixture,
) -> None:
    """Ensure app startup/shutdown logs and hooks run exactly once per lifecycle."""
    caplog.set_level(logging.INFO)
    app = create_app()
    db = RecordingDependency()
    settings = RecordingDependency()
    auth = RecordingDependency()
    telethon_manager = RecordingDependency()
    scheduler = RecordingDependency()
    app.state.dependencies = StartupDependencies(
        db=db,
        settings=settings,
        auth=auth,
        telethon_manager=telethon_manager,
        scheduler=scheduler,
    )

    with TestClient(app) as client:
        response = client.get("/health")
        if response.status_code != HTTPStatus.OK:
            raise AssertionError

    _assert_dependency_call_counts(
        dependency=db,
        expected_startup=1,
        expected_shutdown=1,
    )
    _assert_dependency_call_counts(
        dependency=settings,
        expected_startup=1,
        expected_shutdown=1,
    )
    _assert_dependency_call_counts(
        dependency=auth,
        expected_startup=1,
        expected_shutdown=1,
    )
    _assert_dependency_call_counts(
        dependency=telethon_manager,
        expected_startup=1,
        expected_shutdown=1,
    )
    _assert_dependency_call_counts(
        dependency=scheduler,
        expected_startup=1,
        expected_shutdown=1,
    )

    _assert_log_contains(
        caplog=caplog,
        fragment="Starting TCA in secure-interactive mode",
    )
    _assert_log_contains(caplog=caplog, fragment="Shutting down TCA")


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


def test_lifespan_shuts_down_started_dependencies_on_startup_failure() -> None:
    """Ensure started dependencies are torn down when a later startup hook fails."""
    app = create_app()
    db = RecordingDependency()
    settings = RecordingDependency()
    auth = RecordingDependency()
    telethon_manager = FailingStartupDependency(
        error_message="forced-telethon-startup-failure",
    )
    scheduler = RecordingDependency()
    app.state.dependencies = StartupDependencies(
        db=db,
        settings=settings,
        auth=auth,
        telethon_manager=telethon_manager,
        scheduler=scheduler,
    )

    with (
        pytest.raises(RuntimeError, match=r"forced-telethon-startup-failure"),
        TestClient(app),
    ):
        pass

    _assert_dependency_call_counts(
        dependency=db,
        expected_startup=1,
        expected_shutdown=1,
    )
    _assert_dependency_call_counts(
        dependency=settings,
        expected_startup=1,
        expected_shutdown=1,
    )
    _assert_dependency_call_counts(
        dependency=auth,
        expected_startup=1,
        expected_shutdown=1,
    )
    _assert_dependency_call_counts(
        dependency=telethon_manager,
        expected_startup=1,
        expected_shutdown=0,
    )
    _assert_dependency_call_counts(
        dependency=scheduler,
        expected_startup=0,
        expected_shutdown=0,
    )


def test_lifespan_disposes_runtime_when_writer_queue_factory_is_invalid() -> None:
    """Ensure runtime teardown still runs when queue factory object is invalid."""
    app = create_app()
    app.state.writer_queue_factory = object()
    runtime = object()

    with (
        patch(
            "tca.api.app.create_storage_runtime",
            return_value=runtime,
        ),
        patch(
            "tca.api.app.dispose_storage_runtime",
            new_callable=AsyncMock,
        ) as dispose_runtime,
        pytest.raises(
            RuntimeError,
            match=r"Invalid writer queue factory: expected callable on app\.state\.",
        ),
        TestClient(app),
    ):
        pass

    _assert_dispose_runtime_called_once(
        dispose_runtime=dispose_runtime,
        runtime=runtime,
    )


def test_lifespan_disposes_runtime_when_writer_queue_runtime_is_invalid() -> None:
    """Ensure runtime teardown still runs when queue object has invalid methods."""
    app = create_app()
    app.state.writer_queue_factory = _build_invalid_writer_queue_runtime
    runtime = object()

    with (
        patch(
            "tca.api.app.create_storage_runtime",
            return_value=runtime,
        ),
        patch(
            "tca.api.app.dispose_storage_runtime",
            new_callable=AsyncMock,
        ) as dispose_runtime,
        pytest.raises(
            RuntimeError,
            match=(
                r"Invalid writer queue: expected submit\(\.\.\.\) "
                r"and close\(\) methods\."
            ),
        ),
        TestClient(app),
    ):
        pass

    _assert_dispose_runtime_called_once(
        dispose_runtime=dispose_runtime,
        runtime=runtime,
    )


def _assert_dispose_runtime_called_once(
    *,
    dispose_runtime: AsyncMock,
    runtime: object,
) -> None:
    """Assert runtime teardown hook is awaited exactly once with expected runtime."""
    if dispose_runtime.await_count != 1:
        raise AssertionError

    await_args = dispose_runtime.await_args
    if await_args is None:
        raise AssertionError
    if await_args.args != (runtime,):
        raise AssertionError


def _build_invalid_writer_queue_runtime() -> InvalidWriterQueueRuntime:
    """Build invalid queue runtime object for startup validation tests."""
    return InvalidWriterQueueRuntime()


def _assert_log_contains(*, caplog: LogCaptureFixture, fragment: str) -> None:
    """Assert captured logs contain expected fragment at least once."""
    if not any(fragment in record.message for record in caplog.records):
        raise AssertionError


def _assert_dependency_call_counts(
    *,
    dependency: DependencyCallCounts,
    expected_startup: int,
    expected_shutdown: int,
) -> None:
    """Assert startup/shutdown counters for a lifecycle dependency."""
    if dependency.startup_calls != expected_startup:
        raise AssertionError
    if dependency.shutdown_calls != expected_shutdown:
        raise AssertionError


@runtime_checkable
class DependencyCallCounts(Protocol):
    """Runtime-checkable protocol for lifecycle call counters."""

    startup_calls: int
    shutdown_calls: int
