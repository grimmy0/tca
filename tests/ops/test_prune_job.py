"""Tests for ordered retention prune job behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.ops import (
    DELETE_BATCH_SIZE,
    OrderedRetentionPruneJob,
)
from tca.storage import StorageRuntime, create_storage_runtime, dispose_storage_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

REMAINING_CLUSTER_REPRESENTATIVE_ITEM_ID = 102


@pytest.fixture
async def storage_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[StorageRuntime]:
    """Build a SQLite runtime with prune job tables."""
    db_path = tmp_path / "prune-job.sqlite3"
    monkeypatch.setenv("TCA_DB_PATH", db_path.as_posix())
    settings = load_settings()
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
                    ON DELETE CASCADE
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
                    ON DELETE SET NULL
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS dedupe_clusters (
                id INTEGER PRIMARY KEY,
                cluster_key VARCHAR(36) NOT NULL,
                representative_item_id INTEGER NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_dedupe_clusters_representative_item_id
                    FOREIGN KEY (representative_item_id)
                    REFERENCES items(id)
                    ON DELETE SET NULL
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS dedupe_members (
                cluster_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_dedupe_members_cluster_id
                    FOREIGN KEY (cluster_id)
                    REFERENCES dedupe_clusters(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_dedupe_members_item_id
                    FOREIGN KEY (item_id)
                    REFERENCES items(id)
                    ON DELETE CASCADE
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS dedupe_decisions (
                id INTEGER PRIMARY KEY,
                item_id INTEGER NOT NULL,
                cluster_id INTEGER NULL,
                candidate_item_id INTEGER NULL,
                strategy_name VARCHAR(64) NOT NULL,
                outcome VARCHAR(32) NOT NULL,
                reason_code VARCHAR(128) NULL,
                score FLOAT NULL,
                metadata_json TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS ingest_errors (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NULL,
                stage VARCHAR(32) NOT NULL,
                error_code VARCHAR(128) NOT NULL,
                error_message TEXT NOT NULL,
                payload_ref TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                key VARCHAR(255) NOT NULL UNIQUE,
                value_json TEXT NOT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )

    try:
        yield runtime
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_prune_job_executes_steps_in_designed_order(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure six prune steps run in required design order."""
    now = datetime.now(UTC).replace(microsecond=0)
    await _insert_base_account_and_channel(runtime=storage_runtime)
    await _insert_setting(
        runtime=storage_runtime,
        key="retention.raw_messages_days",
        value=30,
    )
    await _insert_setting(
        runtime=storage_runtime,
        key="retention.items_days",
        value=365,
    )
    await _insert_setting(
        runtime=storage_runtime,
        key="retention.ingest_errors_days",
        value=90,
    )
    await _insert_raw_message(
        runtime=storage_runtime,
        raw_message_id=1,
        channel_id=1,
        message_id=1001,
        created_at=now - timedelta(days=31),
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=101,
        channel_id=1,
        message_id=101,
        created_at=now - timedelta(days=400),
    )
    await _insert_ingest_error(
        runtime=storage_runtime,
        error_id=1,
        created_at=now - timedelta(days=95),
    )

    job = OrderedRetentionPruneJob(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
        now_provider=lambda: now,
    )
    summary = await job.run_once()

    if summary.executed_steps != (
        "delete_expired_raw_messages",
        "delete_expired_items",
        "recompute_cluster_representatives",
        "delete_empty_clusters",
        "delete_orphaned_rows",
        "delete_expired_ingest_errors",
    ):
        raise AssertionError


@pytest.mark.asyncio
async def test_prune_job_respects_batch_size_for_raw_messages_and_items(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure old raw/items rows are removed in bounded 500-row batches."""
    now = datetime.now(UTC).replace(microsecond=0)
    await _insert_base_account_and_channel(runtime=storage_runtime)
    await _insert_setting(
        runtime=storage_runtime,
        key="retention.raw_messages_days",
        value=30,
    )
    await _insert_setting(
        runtime=storage_runtime,
        key="retention.items_days",
        value=365,
    )
    await _insert_setting(
        runtime=storage_runtime,
        key="retention.ingest_errors_days",
        value=90,
    )

    for raw_message_id in range(1, 1201):
        await _insert_raw_message(
            runtime=storage_runtime,
            raw_message_id=raw_message_id,
            channel_id=1,
            message_id=10000 + raw_message_id,
            created_at=now - timedelta(days=45),
        )
    for item_id in range(1, 1201):
        await _insert_item(
            runtime=storage_runtime,
            item_id=item_id,
            channel_id=1,
            message_id=20000 + item_id,
            created_at=now - timedelta(days=800),
        )

    job = OrderedRetentionPruneJob(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
        now_provider=lambda: now,
    )
    summary = await job.run_once()

    if summary.raw_messages_deleted != 1200:  # noqa: PLR2004
        raise AssertionError
    if summary.items_deleted != 1200:  # noqa: PLR2004
        raise AssertionError
    if summary.raw_message_batch_sizes != (500, 500, 200):
        raise AssertionError
    if summary.item_batch_sizes != (500, 500, 200):
        raise AssertionError
    if any(size > DELETE_BATCH_SIZE for size in summary.raw_message_batch_sizes):
        raise AssertionError
    if any(size > DELETE_BATCH_SIZE for size in summary.item_batch_sizes):
        raise AssertionError


@pytest.mark.asyncio
async def test_prune_job_recomputes_cluster_representatives_and_removes_empty_clusters(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure prune item deletions recompute representatives and remove empties."""
    now = datetime.now(UTC).replace(microsecond=0)
    old_created_at = now - timedelta(days=370)
    recent_created_at = now - timedelta(days=5)
    await _insert_base_account_and_channel(runtime=storage_runtime)
    await _insert_setting(
        runtime=storage_runtime,
        key="retention.raw_messages_days",
        value=30,
    )
    await _insert_setting(
        runtime=storage_runtime,
        key="retention.items_days",
        value=365,
    )
    await _insert_setting(
        runtime=storage_runtime,
        key="retention.ingest_errors_days",
        value=90,
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=101,
        channel_id=1,
        message_id=501,
        created_at=old_created_at,
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=102,
        channel_id=1,
        message_id=502,
        created_at=recent_created_at,
        canonical_url="https://example.com/102",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=201,
        channel_id=1,
        message_id=601,
        created_at=old_created_at,
    )
    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=10,
        cluster_key="cluster-10",
        representative_item_id=101,
    )
    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=20,
        cluster_key="cluster-20",
        representative_item_id=201,
    )
    await _insert_member(runtime=storage_runtime, cluster_id=10, item_id=101)
    await _insert_member(runtime=storage_runtime, cluster_id=10, item_id=102)
    await _insert_member(runtime=storage_runtime, cluster_id=20, item_id=201)

    job = OrderedRetentionPruneJob(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
        now_provider=lambda: now,
    )
    summary = await job.run_once()

    if summary.recomputed_cluster_count != 2:  # noqa: PLR2004
        raise AssertionError
    if summary.deleted_empty_cluster_count != 1:
        raise AssertionError
    if (
        await _read_cluster_representative(storage_runtime, cluster_id=10)
        != REMAINING_CLUSTER_REPRESENTATIVE_ITEM_ID
    ):
        raise AssertionError
    if await _read_cluster_exists(storage_runtime, cluster_id=20):
        raise AssertionError


async def _insert_base_account_and_channel(*, runtime: StorageRuntime) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (1, 12345, :api_hash_encrypted)
                """,
            ),
            {"api_hash_encrypted": b"encrypted"},
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_channels (
                    id,
                    account_id,
                    telegram_channel_id,
                    name,
                    is_enabled
                )
                VALUES (1, 1, 10001, 'alpha', 1)
                """,
            ),
        )
        await session.commit()


async def _insert_setting(
    *,
    runtime: StorageRuntime,
    key: str,
    value: int,
) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO settings (key, value_json)
                VALUES (:key, :value_json)
                """,
            ),
            {"key": key, "value_json": str(value)},
        )
        await session.commit()


async def _insert_raw_message(
    *,
    runtime: StorageRuntime,
    raw_message_id: int,
    channel_id: int,
    message_id: int,
    created_at: datetime,
) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO raw_messages (
                    id,
                    channel_id,
                    message_id,
                    payload_json,
                    created_at
                )
                VALUES (
                    :id,
                    :channel_id,
                    :message_id,
                    '{}',
                    :created_at
                )
                """,
            ),
            {
                "id": raw_message_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "created_at": created_at,
            },
        )
        await session.commit()


async def _insert_item(  # noqa: PLR0913
    *,
    runtime: StorageRuntime,
    item_id: int,
    channel_id: int,
    message_id: int,
    created_at: datetime,
    canonical_url: str | None = None,
) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO items (
                    id,
                    channel_id,
                    message_id,
                    title,
                    body,
                    canonical_url,
                    created_at
                )
                VALUES (
                    :id,
                    :channel_id,
                    :message_id,
                    'title',
                    'body',
                    :canonical_url,
                    :created_at
                )
                """,
            ),
            {
                "id": item_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "canonical_url": canonical_url,
                "created_at": created_at,
            },
        )
        await session.commit()


async def _insert_cluster(
    *,
    runtime: StorageRuntime,
    cluster_id: int,
    cluster_key: str,
    representative_item_id: int | None,
) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO dedupe_clusters (
                    id,
                    cluster_key,
                    representative_item_id
                )
                VALUES (:id, :cluster_key, :representative_item_id)
                """,
            ),
            {
                "id": cluster_id,
                "cluster_key": cluster_key,
                "representative_item_id": representative_item_id,
            },
        )
        await session.commit()


async def _insert_member(
    *,
    runtime: StorageRuntime,
    cluster_id: int,
    item_id: int,
) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO dedupe_members (cluster_id, item_id)
                VALUES (:cluster_id, :item_id)
                """,
            ),
            {"cluster_id": cluster_id, "item_id": item_id},
        )
        await session.commit()


async def _insert_ingest_error(
    *,
    runtime: StorageRuntime,
    error_id: int,
    created_at: datetime,
) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO ingest_errors (
                    id,
                    channel_id,
                    stage,
                    error_code,
                    error_message,
                    created_at
                )
                VALUES (
                    :id,
                    1,
                    'fetch',
                    'E_TEST',
                    'test error',
                    :created_at
                )
                """,
            ),
            {"id": error_id, "created_at": created_at},
        )
        await session.commit()


async def _read_cluster_representative(
    runtime: StorageRuntime,
    *,
    cluster_id: int,
) -> int | None:
    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT representative_item_id
                FROM dedupe_clusters
                WHERE id = :cluster_id
                """,
            ),
            {"cluster_id": cluster_id},
        )
        value = result.scalar_one_or_none()
    return cast("int | None", value)


async def _read_cluster_exists(runtime: StorageRuntime, *, cluster_id: int) -> bool:
    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM dedupe_clusters
                WHERE id = :cluster_id
                """,
            ),
            {"cluster_id": cluster_id},
        )
        count = cast("int", result.scalar_one())
    return count == 1
