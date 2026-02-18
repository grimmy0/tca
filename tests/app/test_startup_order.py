"""Tests for startup sequence enforcement and logging boundaries (C085)."""

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
class OrderedDependency:
    """Lifecycle dependency that records startup/shutdown ordering."""

    name: str
    events: list[str]
    fail_on_startup: bool = False
    error_message: str = "forced-startup-failure"

    async def startup(self) -> None:
        """Append startup event and optionally fail deterministically."""
        self.events.append(f"{self.name}.startup")
        if self.fail_on_startup:
            raise RuntimeError(self.error_message)

    async def shutdown(self) -> None:
        """Append shutdown event for teardown order assertions."""
        self.events.append(f"{self.name}.shutdown")


def test_startup_refuses_to_serve_when_migration_step_fails() -> None:
    """Ensure app startup aborts before handling requests if migrations fail."""
    events: list[str] = []
    app = create_app()
    app.state.dependencies = StartupDependencies(
        db=OrderedDependency(
            name="db",
            events=events,
            fail_on_startup=True,
            error_message="forced-migration-failure",
        ),
        settings=OrderedDependency(name="settings", events=events),
        auth=OrderedDependency(name="auth", events=events),
        telethon_manager=OrderedDependency(name="telethon_manager", events=events),
        scheduler=OrderedDependency(name="scheduler", events=events),
    )

    with (
        pytest.raises(RuntimeError, match=r"forced-migration-failure"),
        TestClient(app),
    ):
        pass

    if events != ["db.startup"]:
        raise AssertionError


def test_startup_seeds_settings_before_first_request_handling() -> None:
    """Ensure settings startup step completes before first request executes."""
    events: list[str] = []
    app = create_app()
    app.state.dependencies = StartupDependencies(
        db=OrderedDependency(name="db", events=events),
        settings=OrderedDependency(name="settings", events=events),
        auth=OrderedDependency(name="auth", events=events),
        telethon_manager=OrderedDependency(name="telethon_manager", events=events),
        scheduler=OrderedDependency(name="scheduler", events=events),
    )

    @app.get("/__startup-order-probe")
    def startup_order_probe() -> dict[str, list[str]]:
        events.append("request.handled")
        return {"events": list(events)}

    with TestClient(app) as client:
        response = client.get("/__startup-order-probe")
        if response.status_code != HTTPStatus.OK:
            raise AssertionError

    observed_events = response.json()["events"]
    if observed_events[:2] != ["db.startup", "settings.startup"]:
        raise AssertionError
    if observed_events.index("settings.startup") > observed_events.index(
        "request.handled",
    ):
        raise AssertionError


def test_startup_logs_expose_step_boundaries(caplog: LogCaptureFixture) -> None:
    """Ensure startup logs include begin/complete boundaries per step."""
    caplog.set_level(logging.INFO)
    events: list[str] = []
    app = create_app()
    app.state.dependencies = StartupDependencies(
        db=OrderedDependency(name="db", events=events),
        settings=OrderedDependency(name="settings", events=events),
        auth=OrderedDependency(name="auth", events=events),
        telethon_manager=OrderedDependency(name="telethon_manager", events=events),
        scheduler=OrderedDependency(name="scheduler", events=events),
    )

    with TestClient(app) as client:
        response = client.get("/health")
        if response.status_code != HTTPStatus.OK:
            raise AssertionError

    _assert_log_contains(caplog=caplog, fragment="Startup step begin: migrations")
    _assert_log_contains(caplog=caplog, fragment="Startup step complete: migrations")
    _assert_log_contains(caplog=caplog, fragment="Startup step begin: settings_seed")
    _assert_log_contains(
        caplog=caplog,
        fragment="Startup step complete: settings_seed",
    )
    _assert_log_contains(
        caplog=caplog,
        fragment="Startup sequence complete; app is ready to serve requests.",
    )


def _assert_log_contains(*, caplog: LogCaptureFixture, fragment: str) -> None:
    """Assert log capture includes expected fragment at least once."""
    if not any(fragment in record.getMessage() for record in caplog.records):
        raise AssertionError
