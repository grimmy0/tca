"""Tests for Telethon client manager lifecycle behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI

from tca.api.app import StartupDependencies, create_app, lifespan
from tca.telegram import (
    MissingTelethonClientError,
    TelegramAccount,
    TelethonClientManager,
)
from tests.mocks.mock_telegram_client import MockTelegramClient


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
class CountingFactory:
    """Client factory that tracks creation calls."""

    client: MockTelegramClient
    calls: int = 0

    def __call__(self, account: TelegramAccount) -> MockTelegramClient:
        """Return the configured client and record usage."""
        _ = account
        self.calls += 1
        return self.client


@pytest.fixture(autouse=True)
def _configure_test_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "tca.sqlite3"
    monkeypatch.setenv("TCA_DB_PATH", db_path.as_posix())


def _build_app_with_manager(manager: TelethonClientManager) -> FastAPI:
    app = create_app()
    app.state.dependencies = StartupDependencies(
        db=RecordingDependency(),
        settings=RecordingDependency(),
        auth=RecordingDependency(),
        telethon_manager=manager,
        scheduler=RecordingDependency(),
    )
    return app


@pytest.mark.asyncio
async def test_client_manager_connects_on_startup() -> None:
    """Ensure startup connects all loaded Telegram clients."""
    client = MockTelegramClient(session=None, api_id=123, api_hash="hash")

    async def load_accounts() -> list[TelegramAccount]:
        return [TelegramAccount(account_id=1, api_id=123, api_hash="hash")]

    def _build_client(_: TelegramAccount) -> MockTelegramClient:
        return client

    manager = TelethonClientManager(
        account_loader=load_accounts,
        client_factory=_build_client,
    )

    app = _build_app_with_manager(manager)
    async with lifespan(app):
        pass

    if client.call_counts.get("connect") != 1:
        raise AssertionError("Expected Telethon client to connect on startup.")


@pytest.mark.asyncio
async def test_client_manager_disconnects_on_shutdown() -> None:
    """Ensure shutdown disconnects all managed Telegram clients."""
    client = MockTelegramClient(session=None, api_id=123, api_hash="hash")

    async def load_accounts() -> list[TelegramAccount]:
        return [TelegramAccount(account_id=1, api_id=123, api_hash="hash")]

    def _build_client(_: TelegramAccount) -> MockTelegramClient:
        return client

    manager = TelethonClientManager(
        account_loader=load_accounts,
        client_factory=_build_client,
    )

    app = _build_app_with_manager(manager)
    async with lifespan(app):
        pass

    if client.call_counts.get("disconnect") != 1:
        raise AssertionError("Expected Telethon client to disconnect on shutdown.")


def test_client_manager_does_not_create_client_on_get() -> None:
    """Ensure manager does not create clients implicitly during access."""
    client = MockTelegramClient(session=None, api_id=123, api_hash="hash")
    factory = CountingFactory(client=client)
    manager = TelethonClientManager(client_factory=factory)

    with pytest.raises(MissingTelethonClientError):
        manager.get_client(account_id=999)

    if factory.calls != 0:
        raise AssertionError("Client factory should not run on missing get.")
