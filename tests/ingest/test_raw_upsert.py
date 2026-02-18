"""Tests for ingest raw upsert queue routing behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar, cast

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.ingest import upsert_raw_message
from tca.storage import (
    RawMessageRecord,
    RawMessagesRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

T = TypeVar("T")


def _empty_calls() -> list[tuple[int, int, object]]:
    """Build typed empty call-list for dataclass default factory."""
    return []


@dataclass(slots=True)
class RecordingWriterQueue:
    """Writer queue stub that records submit usage for ingest writes."""

    submit_calls: int = 0

    async def submit(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Record queue submit and execute operation."""
        self.submit_calls += 1
        return await operation()


@dataclass(slots=True)
class RecordingRawMessageRepository:
    """Ingest raw-message write stub for deterministic upsert assertions."""

    calls: list[tuple[int, int, object]] = field(default_factory=_empty_calls)
    return_value: object = "upserted"
    error: Exception | None = None

    async def upsert_raw_message(
        self,
        *,
        channel_id: int,
        message_id: int,
        payload: object,
    ) -> object:
        """Record upsert call and optionally raise configured deterministic error."""
        self.calls.append((channel_id, message_id, payload))
        if self.error is not None:
            raise self.error
        return self.return_value


@pytest.fixture
async def raw_message_repository(
    tmp_path: Path,
) -> AsyncIterator[tuple[RawMessagesRepository, StorageRuntime, int]]:
    """Create raw message repository with minimal schema fixture."""
    db_path = tmp_path / "raw-upsert.sqlite3"
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
                "name": "raw-channel",
                "username": None,
                "is_enabled": True,
            },
        )
        await session.commit()

    try:
        yield (
            RawMessagesRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
            11,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_raw_upsert_uses_writer_queue_for_ingest_write_serialization() -> None:
    """Ensure ingest write path calls writer queue instead of direct repository call."""
    queue = RecordingWriterQueue()
    repository = RecordingRawMessageRepository(return_value={"id": 11})

    result = await upsert_raw_message(
        queue,
        repository,
        channel_id=77,
        message_id=9001,
        payload={"text": "hello"},
    )

    if result != {"id": 11}:
        raise AssertionError
    if queue.submit_calls != 1:
        raise AssertionError
    if repository.calls != [(77, 9001, {"text": "hello"})]:
        raise AssertionError


@pytest.mark.asyncio
async def test_raw_upsert_propagates_repository_error_deterministically() -> None:
    """Ensure ingest write failures surface through queue submit completion path."""
    queue = RecordingWriterQueue()
    repository = RecordingRawMessageRepository(
        error=RuntimeError("forced-upsert-error"),
    )

    with pytest.raises(RuntimeError, match="forced-upsert-error"):
        _ = await upsert_raw_message(
            queue,
            repository,
            channel_id=5,
            message_id=6,
            payload={"text": "fail"},
        )

    if queue.submit_calls != 1:
        raise AssertionError
    if repository.calls != [(5, 6, {"text": "fail"})]:
        raise AssertionError


@pytest.mark.asyncio
async def test_raw_upsert_updates_existing_row_without_duplicate(
    raw_message_repository: tuple[RawMessagesRepository, StorageRuntime, int],
) -> None:
    """Ensure repeated upsert updates existing row rather than inserting duplicate."""
    repository, runtime, channel_id = raw_message_repository
    first = await repository.upsert_raw_message(
        channel_id=channel_id,
        message_id=777,
        payload={"text": "first"},
    )
    second = await repository.upsert_raw_message(
        channel_id=channel_id,
        message_id=777,
        payload={"text": "second"},
    )

    if first.raw_message_id != second.raw_message_id:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM raw_messages
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
async def test_raw_upsert_replaces_payload_with_latest_version(
    raw_message_repository: tuple[RawMessagesRepository, StorageRuntime, int],
) -> None:
    """Ensure upsert replaces stored payload with the latest version."""
    repository, runtime, channel_id = raw_message_repository
    _ = await repository.upsert_raw_message(
        channel_id=channel_id,
        message_id=888,
        payload={"text": "old"},
    )
    updated = await repository.upsert_raw_message(
        channel_id=channel_id,
        message_id=888,
        payload={"text": "new"},
    )

    _assert_payload(updated, expected={"text": "new"})

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT payload_json
                FROM raw_messages
                WHERE channel_id = :channel_id
                  AND message_id = :message_id
                """,
            ),
            {"channel_id": channel_id, "message_id": 888},
        )
        payload_json = cast("str", result.scalar_one())
    if payload_json != '{"text":"new"}':
        raise AssertionError


@pytest.mark.asyncio
async def test_raw_upsert_handles_unique_constraint_conflict(
    raw_message_repository: tuple[RawMessagesRepository, StorageRuntime, int],
) -> None:
    """Ensure unique constraint conflicts do not raise during upsert."""
    repository, runtime, channel_id = raw_message_repository
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
                "message_id": 999,
                "payload_json": '{"text":"seeded"}',
            },
        )
        seeded_id = cast("int", result.scalar_one())
        await session.commit()

    upserted = await repository.upsert_raw_message(
        channel_id=channel_id,
        message_id=999,
        payload={"text": "fresh"},
    )

    if upserted.raw_message_id != seeded_id:
        raise AssertionError


def _assert_payload(record: RawMessageRecord, *, expected: object) -> None:
    if record.payload != expected:
        raise AssertionError
