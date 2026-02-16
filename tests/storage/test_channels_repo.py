"""Tests for channels repository soft-delete and active query behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.storage import (
    ChannelRecord,
    ChannelsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

DEFAULT_ACCOUNT_ID = 1


@pytest.fixture
async def repository_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[ChannelsRepository, StorageRuntime]]:
    """Create repository and schema fixture for channel storage tests."""
    db_path = tmp_path / "channels-repository.sqlite3"
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

    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (:id, :api_id, :api_hash_encrypted)
                """,
            ),
            {
                "id": DEFAULT_ACCOUNT_ID,
                "api_id": 12345,
                "api_hash_encrypted": b"encrypted-api-hash",
            },
        )
        await session.commit()

    try:
        yield (
            ChannelsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_channel_create_and_update_work(
    repository_runtime: tuple[ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure repository supports create and update operations."""
    repository, _ = repository_runtime
    created = await repository.create_channel(
        account_id=DEFAULT_ACCOUNT_ID,
        telegram_channel_id=10001,
        name="alpha",
        username="alpha_channel",
    )
    _assert_channel(
        created,
        expected_name="alpha",
        expected_username="alpha_channel",
        expected_is_enabled=True,
    )

    updated = await repository.update_channel(
        channel_id=created.id,
        name="alpha-updated",
        username=None,
    )
    if updated is None:
        raise AssertionError
    _assert_channel(
        updated,
        expected_name="alpha-updated",
        expected_username=None,
        expected_is_enabled=True,
    )


@pytest.mark.asyncio
async def test_disable_channel_soft_delete_without_row_deletion(
    repository_runtime: tuple[ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure disabling a channel does not physically delete its row."""
    repository, runtime = repository_runtime
    created = await repository.create_channel(
        account_id=DEFAULT_ACCOUNT_ID,
        telegram_channel_id=10002,
        name="soft-delete-target",
        username=None,
    )
    disabled = await repository.disable_channel(channel_id=created.id)
    if disabled is None:
        raise AssertionError
    if disabled.is_enabled:
        raise AssertionError

    loaded = await repository.get_channel_by_id(channel_id=created.id)
    if loaded is None:
        raise AssertionError
    if loaded.is_enabled:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM telegram_channels
                WHERE id = :channel_id
                """,
            ),
            {"channel_id": created.id},
        )
        row_count = cast("int", result.scalar_one())
    if row_count != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_list_active_channels_excludes_disabled_channels(
    repository_runtime: tuple[ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure active-query helper returns only enabled channels."""
    repository, _ = repository_runtime
    enabled = await repository.create_channel(
        account_id=DEFAULT_ACCOUNT_ID,
        telegram_channel_id=10003,
        name="enabled-channel",
        username=None,
    )
    disabled = await repository.create_channel(
        account_id=DEFAULT_ACCOUNT_ID,
        telegram_channel_id=10004,
        name="disabled-channel",
        username=None,
    )
    _ = await repository.disable_channel(channel_id=disabled.id)

    active_channels = await repository.list_active_channels(
        account_id=DEFAULT_ACCOUNT_ID,
    )
    active_ids = {channel.id for channel in active_channels}
    if enabled.id not in active_ids:
        raise AssertionError
    if disabled.id in active_ids:
        raise AssertionError


@pytest.mark.asyncio
async def test_enable_channel_restores_active_status(
    repository_runtime: tuple[ChannelsRepository, StorageRuntime],
) -> None:
    """Ensure re-enabling a channel includes it again in active results."""
    repository, _ = repository_runtime
    created = await repository.create_channel(
        account_id=DEFAULT_ACCOUNT_ID,
        telegram_channel_id=10005,
        name="toggle-channel",
        username="toggle",
    )
    _ = await repository.disable_channel(channel_id=created.id)
    active_after_disable = await repository.list_active_channels()
    disabled_ids = {channel.id for channel in active_after_disable}
    if created.id in disabled_ids:
        raise AssertionError

    enabled = await repository.enable_channel(channel_id=created.id)
    if enabled is None:
        raise AssertionError
    if not enabled.is_enabled:
        raise AssertionError

    active_after_enable = await repository.list_active_channels()
    enabled_ids = {channel.id for channel in active_after_enable}
    if created.id not in enabled_ids:
        raise AssertionError


def _assert_channel(
    record: ChannelRecord,
    *,
    expected_name: str,
    expected_username: str | None,
    expected_is_enabled: bool,
) -> None:
    if type(record.id) is not int:
        raise AssertionError
    if record.account_id != DEFAULT_ACCOUNT_ID:
        raise AssertionError
    if type(record.telegram_channel_id) is not int:
        raise AssertionError
    if record.name != expected_name:
        raise AssertionError
    if record.username != expected_username:
        raise AssertionError
    if record.is_enabled != expected_is_enabled:
        raise AssertionError
