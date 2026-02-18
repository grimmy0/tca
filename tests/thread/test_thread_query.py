"""Thread timeline query repository behavior tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import event

from tca.config.settings import load_settings
from tca.storage import (
    StorageRuntime,
    ThreadQueryRepository,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def storage_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[StorageRuntime]:
    """Create storage runtime with minimal schema for thread query tests."""
    db_path = tmp_path / "thread-query.sqlite3"
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
                    UNIQUE (channel_id, message_id)
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
                    ON DELETE SET NULL,
                CONSTRAINT uq_dedupe_clusters_cluster_key
                    UNIQUE (cluster_key)
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
                    ON DELETE CASCADE,
                CONSTRAINT pk_dedupe_members
                    PRIMARY KEY (cluster_id, item_id)
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
            VALUES (1, 11111, X'00')
            """,
        )

    try:
        yield runtime
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_results_order_by_representative_published_at_descending(
    storage_runtime: StorageRuntime,
) -> None:
    """Thread rows should sort by representative published_at descending."""
    repository = _build_repository(storage_runtime=storage_runtime)
    await _insert_channel(
        runtime=storage_runtime,
        channel_id=10,
        account_id=1,
        telegram_channel_id=10010,
        name="alpha",
    )
    await _insert_channel(
        runtime=storage_runtime,
        channel_id=11,
        account_id=1,
        telegram_channel_id=10011,
        name="beta",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=101,
        channel_id=10,
        message_id=101,
        published_at="2026-02-01 12:00:00",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=201,
        channel_id=11,
        message_id=201,
        published_at="2026-01-01 12:00:00",
    )
    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=1,
        representative_item_id=101,
    )
    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=2,
        representative_item_id=201,
    )
    await _insert_member(runtime=storage_runtime, cluster_id=1, item_id=101)
    await _insert_member(runtime=storage_runtime, cluster_id=2, item_id=201)

    page = await repository.list_entries(page=1, page_size=10)

    if [row.cluster_id for row in page] != [1, 2]:
        raise AssertionError


@pytest.mark.asyncio
async def test_query_uses_single_explicit_sql_statement_without_lazy_load(
    storage_runtime: StorageRuntime,
) -> None:
    """Thread query should execute one explicit SQL statement with all joins."""
    repository = _build_repository(storage_runtime=storage_runtime)
    await _insert_channel(
        runtime=storage_runtime,
        channel_id=20,
        account_id=1,
        telegram_channel_id=10020,
        name="gamma",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=301,
        channel_id=20,
        message_id=301,
        published_at="2026-02-05 12:00:00",
    )
    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=3,
        representative_item_id=301,
    )
    await _insert_member(runtime=storage_runtime, cluster_id=3, item_id=301)

    captured_selects: list[str] = []

    def _capture(*args: object) -> None:
        statement = cast("str", args[2])
        if statement.lstrip().upper().startswith("SELECT"):
            captured_selects.append(statement)

    event.listen(
        storage_runtime.read_engine.sync_engine,
        "before_cursor_execute",
        _capture,
    )
    try:
        _ = await repository.list_entries(page=1, page_size=10)
    finally:
        event.remove(
            storage_runtime.read_engine.sync_engine,
            "before_cursor_execute",
            _capture,
        )

    if len(captured_selects) != 1:
        raise AssertionError
    statement = captured_selects[0].lower()
    required_joins = ("join dedupe_members", "join items", "join telegram_channels")
    for required_join in required_joins:
        if required_join not in statement:
            raise AssertionError


@pytest.mark.asyncio
async def test_pagination_returns_deterministic_pages(
    storage_runtime: StorageRuntime,
) -> None:
    """Stable tie-breakers should return deterministic pagination slices."""
    repository = _build_repository(storage_runtime=storage_runtime)
    await _insert_channel(
        runtime=storage_runtime,
        channel_id=30,
        account_id=1,
        telegram_channel_id=10030,
        name="delta",
    )
    for cluster_id in range(1, 6):
        item_id = 400 + cluster_id
        await _insert_item(
            runtime=storage_runtime,
            item_id=item_id,
            channel_id=30,
            message_id=item_id,
            published_at="2026-02-10 08:00:00",
        )
        await _insert_cluster(
            runtime=storage_runtime,
            cluster_id=cluster_id,
            representative_item_id=item_id,
        )
        await _insert_member(
            runtime=storage_runtime,
            cluster_id=cluster_id,
            item_id=item_id,
        )

    first_run = [
        [row.cluster_id for row in await repository.list_entries(page=1, page_size=2)],
        [row.cluster_id for row in await repository.list_entries(page=2, page_size=2)],
        [row.cluster_id for row in await repository.list_entries(page=3, page_size=2)],
    ]
    second_run = [
        [row.cluster_id for row in await repository.list_entries(page=1, page_size=2)],
        [row.cluster_id for row in await repository.list_entries(page=2, page_size=2)],
        [row.cluster_id for row in await repository.list_entries(page=3, page_size=2)],
    ]

    if first_run != [[5, 4], [3, 2], [1]]:
        raise AssertionError
    if second_run != first_run:
        raise AssertionError


def _build_repository(*, storage_runtime: StorageRuntime) -> ThreadQueryRepository:
    return ThreadQueryRepository(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
    )


async def _insert_channel(
    *,
    runtime: StorageRuntime,
    channel_id: int,
    account_id: int,
    telegram_channel_id: int,
    name: str,
) -> None:
    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            INSERT INTO telegram_channels (
                id,
                account_id,
                telegram_channel_id,
                name,
                username,
                is_enabled
            )
            VALUES (:id, :account_id, :telegram_channel_id, :name, NULL, 1)
            """,
            {
                "id": channel_id,
                "account_id": account_id,
                "telegram_channel_id": telegram_channel_id,
                "name": name,
            },
        )


async def _insert_item(
    *,
    runtime: StorageRuntime,
    item_id: int,
    channel_id: int,
    message_id: int,
    published_at: str | None,
) -> None:
    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            INSERT INTO items (
                id,
                channel_id,
                message_id,
                published_at
            )
            VALUES (:id, :channel_id, :message_id, :published_at)
            """,
            {
                "id": item_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "published_at": published_at,
            },
        )


async def _insert_cluster(
    *,
    runtime: StorageRuntime,
    cluster_id: int,
    representative_item_id: int,
) -> None:
    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            INSERT INTO dedupe_clusters (id, cluster_key, representative_item_id)
            VALUES (:id, :cluster_key, :representative_item_id)
            """,
            {
                "id": cluster_id,
                "cluster_key": f"cluster-{cluster_id}",
                "representative_item_id": representative_item_id,
            },
        )


async def _insert_member(
    *,
    runtime: StorageRuntime,
    cluster_id: int,
    item_id: int,
) -> None:
    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            INSERT INTO dedupe_members (cluster_id, item_id)
            VALUES (:cluster_id, :item_id)
            """,
            {
                "cluster_id": cluster_id,
                "item_id": item_id,
            },
        )
