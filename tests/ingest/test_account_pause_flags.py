"""Tests for account pause flags used by scheduler selection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.storage import (
    AccountPauseRepository,
    ChannelsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def pause_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[AccountPauseRepository, ChannelsRepository, StorageRuntime]]:
    """Create repositories and schema fixture for pause tests."""
    db_path = tmp_path / "account-pause.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_accounts (
                id INTEGER PRIMARY KEY,
                api_id INTEGER NOT NULL,
                api_hash_encrypted BLOB NOT NULL,
                paused_at DATETIME NULL,
                pause_reason TEXT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_channels (
                id INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL,
                telegram_channel_id BIGINT NOT NULL,
                name VARCHAR(255) NOT NULL,
                username VARCHAR(255) NULL,
                is_enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_telegram_channels_account_id
                    FOREIGN KEY (account_id)
                    REFERENCES telegram_accounts(id)
                    ON DELETE CASCADE,
                CONSTRAINT uq_telegram_channels_telegram_channel_id
                    UNIQUE (telegram_channel_id)
            )
            """,
        )

    try:
        yield (
            AccountPauseRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            ChannelsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


async def _seed_account(runtime: StorageRuntime, *, account_id: int) -> None:
    async with runtime.write_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (:id, :api_id, :api_hash_encrypted)
                """,
            ),
            {
                "id": account_id,
                "api_id": 12345,
                "api_hash_encrypted": b"encrypted-api-hash",
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_pause_reason_persisted(
    pause_runtime: tuple[AccountPauseRepository, ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure pause reason is persisted on account pause updates."""
    pause_repo, _, runtime = pause_runtime
    await _seed_account(runtime, account_id=1)

    paused_at = datetime.now(UTC)
    paused = await pause_repo.pause_account(
        account_id=1,
        reason="account-risk",
        paused_at=paused_at,
    )
    if paused is None:
        raise AssertionError
    if paused.pause_reason != "account-risk":
        raise AssertionError
    if paused.paused_at is None:
        raise AssertionError

    loaded = await pause_repo.get_pause_state(account_id=1)
    if loaded is None:
        raise AssertionError
    if loaded.pause_reason != "account-risk":
        raise AssertionError
    if loaded.paused_at is None:
        raise AssertionError


@pytest.mark.asyncio
async def test_resume_clears_pause_state(
    pause_runtime: tuple[AccountPauseRepository, ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure resume clears pause state for account rows."""
    pause_repo, _, runtime = pause_runtime
    await _seed_account(runtime, account_id=2)

    _ = await pause_repo.pause_account(
        account_id=2,
        reason="cooldown",
    )
    resumed = await pause_repo.resume_account(account_id=2)
    if resumed is None:
        raise AssertionError
    if resumed.paused_at is not None:
        raise AssertionError
    if resumed.pause_reason is not None:
        raise AssertionError

    loaded = await pause_repo.get_pause_state(account_id=2)
    if loaded is None:
        raise AssertionError
    if loaded.paused_at is not None:
        raise AssertionError
    if loaded.pause_reason is not None:
        raise AssertionError


@pytest.mark.asyncio
async def test_scheduler_selection_excludes_paused_accounts(
    pause_runtime: tuple[AccountPauseRepository, ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure schedulable channel query skips paused account channels."""
    pause_repo, channel_repo, runtime = pause_runtime
    await _seed_account(runtime, account_id=10)
    await _seed_account(runtime, account_id=20)

    channel_a = await channel_repo.create_channel(
        account_id=10,
        telegram_channel_id=101,
        name="alpha",
        username=None,
    )
    channel_b = await channel_repo.create_channel(
        account_id=20,
        telegram_channel_id=202,
        name="beta",
        username=None,
    )

    _ = await pause_repo.pause_account(account_id=20, reason="risk")

    schedulable = await channel_repo.list_schedulable_channels()
    schedulable_ids = {channel.id for channel in schedulable}
    if channel_a.id not in schedulable_ids:
        raise AssertionError
    if channel_b.id in schedulable_ids:
        raise AssertionError
