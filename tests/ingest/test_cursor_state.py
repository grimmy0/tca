"""Tests for channel cursor persistence in ingest state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.storage import (
    ChannelCursor,
    ChannelStateDecodeError,
    ChannelStateRepository,
    ChannelsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def cursor_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[ChannelStateRepository, ChannelsRepository, StorageRuntime]]:
    """Create repositories and schema fixture for cursor persistence tests."""
    db_path = tmp_path / "cursor-state.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_accounts (
                id INTEGER PRIMARY KEY,
                api_id INTEGER NOT NULL,
                api_hash_encrypted BLOB NOT NULL
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
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS channel_state (
                channel_id INTEGER NOT NULL,
                cursor_json TEXT NULL,
                paused_until DATETIME NULL,
                last_success_at DATETIME NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_channel_state_channel_id
                    FOREIGN KEY (channel_id)
                    REFERENCES telegram_channels(id)
                    ON DELETE CASCADE,
                CONSTRAINT pk_channel_state PRIMARY KEY (channel_id)
            )
            """,
        )

    try:
        yield (
            ChannelStateRepository(
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
async def test_cursor_updates_after_successful_poll(
    cursor_runtime: tuple[ChannelStateRepository, ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure cursor is persisted after a successful poll update."""
    state_repo, channels_repo, runtime = cursor_runtime
    await _seed_account(runtime, account_id=1)
    channel = await channels_repo.create_channel(
        account_id=1,
        telegram_channel_id=1001,
        name="cursor-channel",
        username=None,
    )

    cursor = ChannelCursor(
        last_message_id=451,
        next_offset_id=None,
        last_polled_at=datetime(2026, 2, 15, 18, 30, tzinfo=timezone.utc),
    )

    updated = await state_repo.update_cursor(channel_id=channel.id, cursor=cursor)
    if updated.cursor != cursor:
        raise AssertionError

    loaded = await state_repo.get_state(channel_id=channel.id)
    if loaded is None:
        raise AssertionError
    if loaded.cursor != cursor:
        raise AssertionError


@pytest.mark.asyncio
async def test_cursor_read_resumes_from_previous_state(
    cursor_runtime: tuple[ChannelStateRepository, ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure cursor can be read back on a subsequent run."""
    state_repo, channels_repo, runtime = cursor_runtime
    await _seed_account(runtime, account_id=2)
    channel = await channels_repo.create_channel(
        account_id=2,
        telegram_channel_id=2002,
        name="resume-channel",
        username=None,
    )

    cursor = ChannelCursor(
        last_message_id=998,
        next_offset_id=990,
        last_polled_at=datetime(2026, 2, 16, 9, 15, tzinfo=timezone.utc),
    )
    _ = await state_repo.update_cursor(channel_id=channel.id, cursor=cursor)

    reloaded_repo = ChannelStateRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    loaded = await reloaded_repo.get_state(channel_id=channel.id)
    if loaded is None:
        raise AssertionError
    if loaded.cursor != cursor:
        raise AssertionError


@pytest.mark.asyncio
async def test_cursor_schema_validation_rejects_malformed_payload(
    cursor_runtime: tuple[ChannelStateRepository, ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure malformed cursor JSON is rejected during decode."""
    state_repo, channels_repo, runtime = cursor_runtime
    await _seed_account(runtime, account_id=3)
    channel = await channels_repo.create_channel(
        account_id=3,
        telegram_channel_id=3003,
        name="invalid-cursor-channel",
        username=None,
    )

    async with runtime.write_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO channel_state (channel_id, cursor_json)
                VALUES (:channel_id, :cursor_json)
                """,
            ),
            {
                "channel_id": channel.id,
                "cursor_json": '{"last_message_id": 12}',
            },
        )
        await session.commit()

    with pytest.raises(ChannelStateDecodeError, match="cursor_json missing"):
        _ = await state_repo.get_state(channel_id=channel.id)
