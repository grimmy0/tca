"""Tests for BotDeliveryCoreLoop."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from tca.bot import BotApiClient
from tca.bot.delivery import BotDeliveryCoreLoop
from tca.config.settings import load_settings
from tca.storage import (
    BotDeliveriesRepository,
    BotDeliveryEntryRecord,
    SettingsRepository,
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
    db_path = tmp_path / "bot-delivery-test.sqlite3"
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
async def test_delivery_core_loop_when_disabled(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure run_once returns empty list if bot is disabled."""
    settings_repo = SettingsRepository(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
    )
    # bot.enabled is not set (defaults to False)

    bot_deliveries_repo = MagicMock(spec=BotDeliveriesRepository)
    bot_api_client = MagicMock(spec=BotApiClient)

    core_loop = BotDeliveryCoreLoop(
        settings_repo=settings_repo,
        bot_deliveries_repo=bot_deliveries_repo,
        bot_api_client=bot_api_client,
    )

    records = await core_loop.run_once()
    assert len(records) == 0
    bot_deliveries_repo.list_undelivered_entries.assert_not_called()


@pytest.mark.asyncio
async def test_delivery_core_loop_when_enabled_sends_messages(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure run_once delivers messages when bot is configured and enabled."""
    settings_repo = SettingsRepository(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
    )
    # Enable and configure bot
    await settings_repo.create(key="bot.enabled", value=True)
    await settings_repo.create(key="bot.token", value="123:ABC")
    await settings_repo.create(key="bot.chat_id", value="@mychat")
    await settings_repo.create(key="bot.delivery_batch_size", value=5)

    bot_deliveries_repo = MagicMock(spec=BotDeliveriesRepository)
    entry = BotDeliveryEntryRecord(
        cluster_id=42,
        representative_title="Test Cluster",
        representative_body="Some body",
        representative_canonical_url="https://url",
        representative_published_at=None,
        channel_name="Test Channel",
        channel_username=None,
        duplicate_count=1,
    )
    bot_deliveries_repo.list_undelivered_entries = AsyncMock(return_value=[entry])

    from tca.bot import SentMessage
    bot_api_client = MagicMock(spec=BotApiClient)
    bot_api_client.send_message = AsyncMock(return_value=SentMessage(message_id=987))

    # Mock record_delivery returning a record
    from tca.storage import BotDeliveryRecord
    from datetime import datetime, UTC
    record = BotDeliveryRecord(
        delivery_id=1,
        cluster_id=42,
        delivered_at=datetime.now(UTC),
        telegram_message_id="987",
    )
    bot_deliveries_repo.record_delivery = AsyncMock(return_value=record)

    core_loop = BotDeliveryCoreLoop(
        settings_repo=settings_repo,
        bot_deliveries_repo=bot_deliveries_repo,
        bot_api_client=bot_api_client,
    )

    records = await core_loop.run_once()

    assert len(records) == 1
    assert records[0].cluster_id == 42
    bot_api_client.send_message.assert_called_once_with(
        token="123:ABC",
        chat_id="@mychat",
        text=core_loop._formatter(entry),
    )
    bot_deliveries_repo.record_delivery.assert_called_once_with(
        cluster_id=42,
        telegram_message_id="987",
    )
