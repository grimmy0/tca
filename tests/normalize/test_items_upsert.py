"""Tests for normalized item upsert behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.storage import (
    ItemsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def items_repository(
    tmp_path: Path,
) -> AsyncIterator[tuple[ItemsRepository, StorageRuntime, int]]:
    """Create items repository with minimal schema fixture."""
    db_path = tmp_path / "items-upsert.sqlite3"
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
                    ON DELETE CASCADE
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS raw_messages (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id BIGINT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_raw_messages_channel_id
                    FOREIGN KEY (channel_id)
                    REFERENCES telegram_channels(id)
                    ON DELETE CASCADE,
                CONSTRAINT uq_raw_messages_channel_id_message_id
                    UNIQUE (channel_id, message_id)
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id BIGINT NOT NULL,
                raw_message_id INTEGER NULL,
                published_at DATETIME NULL,
                title TEXT NULL,
                body TEXT NULL,
                canonical_url TEXT NULL,
                canonical_url_hash VARCHAR(64) NULL,
                content_hash VARCHAR(64) NULL,
                dedupe_state VARCHAR(32) NOT NULL DEFAULT 'pending',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_items_channel_id
                    FOREIGN KEY (channel_id)
                    REFERENCES telegram_channels(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_items_raw_message_id
                    FOREIGN KEY (raw_message_id)
                    REFERENCES raw_messages(id)
                    ON DELETE SET NULL,
                CONSTRAINT uq_items_channel_id_message_id
                    UNIQUE (channel_id, message_id),
                CONSTRAINT uq_items_raw_message_id
                    UNIQUE (raw_message_id)
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
                "id": 1,
                "api_id": 12345,
                "api_hash_encrypted": b"encrypted-api-hash",
            },
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_channels (
                    id,
                    account_id,
                    telegram_channel_id,
                    name,
                    username,
                    is_enabled
                )
                VALUES (
                    :id,
                    :account_id,
                    :telegram_channel_id,
                    :name,
                    :username,
                    :is_enabled
                )
                """,
            ),
            {
                "id": 11,
                "account_id": 1,
                "telegram_channel_id": 10001,
                "name": "items-channel",
                "username": None,
                "is_enabled": True,
            },
        )
        await session.commit()

    try:
        yield (
            ItemsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
            11,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_item_upsert_updates_existing_row_without_duplicate(
    items_repository: tuple[ItemsRepository, StorageRuntime, int],
) -> None:
    """Ensure repeated upsert updates existing row rather than inserting duplicate."""
    repository, runtime, channel_id = items_repository
    first = await repository.upsert_item(
        channel_id=channel_id,
        message_id=777,
        raw_message_id=None,
        published_at=None,
        title="first",
        body="alpha",
        canonical_url=None,
        canonical_url_hash=None,
        content_hash=None,
    )
    second = await repository.upsert_item(
        channel_id=channel_id,
        message_id=777,
        raw_message_id=None,
        published_at=None,
        title="second",
        body="beta",
        canonical_url=None,
        canonical_url_hash=None,
        content_hash=None,
    )

    if first.item_id != second.item_id:
        raise AssertionError
    if second.title != "second":
        raise AssertionError

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM items
                WHERE channel_id = :channel_id
                  AND message_id = :message_id
                """,
            ),
            {"channel_id": channel_id, "message_id": 777},
        )
        count = cast("int", result.scalar_one())
    if count != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_item_upsert_preserves_raw_message_id_on_update(
    items_repository: tuple[ItemsRepository, StorageRuntime, int],
) -> None:
    """Ensure raw_message_id remains linked on update when omitted."""
    repository, runtime, channel_id = items_repository
    raw_message_id = await _insert_raw_message(runtime, channel_id, message_id=888)
    _ = await repository.upsert_item(
        channel_id=channel_id,
        message_id=888,
        raw_message_id=raw_message_id,
        published_at=None,
        title="initial",
        body=None,
        canonical_url=None,
        canonical_url_hash=None,
        content_hash=None,
    )
    updated = await repository.upsert_item(
        channel_id=channel_id,
        message_id=888,
        raw_message_id=None,
        published_at=None,
        title="refresh",
        body=None,
        canonical_url=None,
        canonical_url_hash=None,
        content_hash=None,
    )

    if updated.raw_message_id != raw_message_id:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT raw_message_id
                FROM items
                WHERE channel_id = :channel_id
                  AND message_id = :message_id
                """,
            ),
            {"channel_id": channel_id, "message_id": 888},
        )
        stored_raw_id = result.scalar_one()
    if stored_raw_id != raw_message_id:
        raise AssertionError


@pytest.mark.asyncio
async def test_item_upsert_nulls_raw_message_id_on_delete(
    items_repository: tuple[ItemsRepository, StorageRuntime, int],
) -> None:
    """Ensure removing raw message nulls the item raw_message_id."""
    repository, runtime, channel_id = items_repository
    raw_message_id = await _insert_raw_message(runtime, channel_id, message_id=999)
    _ = await repository.upsert_item(
        channel_id=channel_id,
        message_id=999,
        raw_message_id=raw_message_id,
        published_at=None,
        title="linked",
        body=None,
        canonical_url=None,
        canonical_url_hash=None,
        content_hash=None,
    )

    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text("DELETE FROM raw_messages WHERE id = :raw_message_id"),
            {"raw_message_id": raw_message_id},
        )
        await session.commit()

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT raw_message_id
                FROM items
                WHERE channel_id = :channel_id
                  AND message_id = :message_id
                """,
            ),
            {"channel_id": channel_id, "message_id": 999},
        )
        stored_raw_id = result.scalar_one()
    if stored_raw_id is not None:
        raise AssertionError


async def _insert_raw_message(
    runtime: StorageRuntime,
    channel_id: int,
    *,
    message_id: int,
) -> int:
    async with runtime.write_session_factory() as session:
        result = await session.execute(
            text(
                """
                INSERT INTO raw_messages (
                    channel_id,
                    message_id,
                    payload_json
                )
                VALUES (
                    :channel_id,
                    :message_id,
                    :payload_json
                )
                RETURNING id
                """,
            ),
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "payload_json": '{"text":"seeded"}',
            },
        )
        raw_message_id = cast("int", result.scalar_one())
        await session.commit()
    return raw_message_id
