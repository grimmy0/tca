"""Tests for BotDeliveryService."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest

from tca.bot import BotDeliveryService
from tca.config.settings import load_settings
from tca.storage import (
    create_storage_runtime,
    dispose_storage_runtime,
    run_startup_migrations,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from tca.storage.db import StorageRuntime


@pytest.fixture
async def storage_runtime(tmp_path: Path) -> AsyncIterator[StorageRuntime]:
    """Create initialized storage runtime."""
    db_path = tmp_path / "bot-service-test.sqlite3"
    os.environ["TCA_DB_PATH"] = db_path.as_posix()
    run_startup_migrations()

    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    try:
        yield runtime
    finally:
        await dispose_storage_runtime(runtime)
        os.environ.pop("TCA_DB_PATH", None)


@pytest.mark.asyncio
async def test_bot_delivery_service_lifecycle(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure startup, shutdown, and idempotency work on BotDeliveryService."""
    service = BotDeliveryService(
        runtime_provider=lambda: storage_runtime,
        writer_queue_provider=None,
        delivery_interval_seconds=10,
        tick_interval_seconds=0.01,
    )

    assert not service.is_running

    # Startup the service
    await service.startup()
    assert service.is_running

    # Idempotent startup
    first_task = service._task
    await service.startup()
    assert service._task is first_task
    assert service.is_running

    # Shutdown the service
    await service.shutdown()
    assert not service.is_running
    assert service._task is None
