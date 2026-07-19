"""Tests for bot delivery dynamic settings defaults seeding (C093)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tca.config.settings import load_settings
from tca.storage import (
    SettingsRepository,
    create_storage_runtime,
    dispose_storage_runtime,
)
from tca.storage.settings_seed import seed_default_dynamic_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def settings_repository(tmp_path: Path) -> AsyncIterator[SettingsRepository]:
    """Create settings repository with schema initialized."""
    db_path = tmp_path / "settings-seed.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                key VARCHAR(255) NOT NULL,
                value_json TEXT NOT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_settings_key UNIQUE (key)
            )
            """,
        )
    try:
        yield SettingsRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_bot_delivery_settings_seeded_successfully(
    settings_repository: SettingsRepository,
) -> None:
    """Ensure bot settings are seeded with correct default values."""
    await seed_default_dynamic_settings(repository=settings_repository)

    interval = await settings_repository.get_by_key(key="bot.delivery_interval_seconds")
    if interval is None:
        raise AssertionError
    if not isinstance(interval.value, int) or interval.value <= 0:
        raise AssertionError
    if interval.value != 60:
        raise AssertionError

    batch_size = await settings_repository.get_by_key(key="bot.delivery_batch_size")
    if batch_size is None:
        raise AssertionError
    if not isinstance(batch_size.value, int) or batch_size.value <= 0:
        raise AssertionError
    if batch_size.value != 10:
        raise AssertionError


@pytest.mark.asyncio
async def test_existing_bot_delivery_settings_unchanged_on_reseed(
    settings_repository: SettingsRepository,
) -> None:
    """Ensure existing bot settings are not overwritten during seeding."""
    # Pre-create with custom values
    _ = await settings_repository.create(key="bot.delivery_interval_seconds", value=120)

    # Run seed
    await seed_default_dynamic_settings(repository=settings_repository)

    # Verify custom value is preserved
    interval = await settings_repository.get_by_key(key="bot.delivery_interval_seconds")
    if interval is None or interval.value != 120:
        raise AssertionError

    # Verify other settings are still seeded
    batch_size = await settings_repository.get_by_key(key="bot.delivery_batch_size")
    if batch_size is None or batch_size.value != 10:
        raise AssertionError
