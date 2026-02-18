"""Representative recompute selection behavior across merge and purge flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.storage import (
    ChannelsRepository,
    DedupeClustersRepository,
    StorageRuntime,
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
    """Create storage runtime with required schema for representative tests."""
    db_path = tmp_path / "representative-selection.sqlite3"
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
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_dedupe_decisions_item_id
                    FOREIGN KEY (item_id)
                    REFERENCES items(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_dedupe_decisions_cluster_id
                    FOREIGN KEY (cluster_id)
                    REFERENCES dedupe_clusters(id)
                    ON DELETE SET NULL,
                CONSTRAINT fk_dedupe_decisions_candidate_item_id
                    FOREIGN KEY (candidate_item_id)
                    REFERENCES items(id)
                    ON DELETE SET NULL
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY,
                type VARCHAR(64) NOT NULL,
                severity VARCHAR(32) NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )

    await _seed_channel_rows(runtime=runtime)

    try:
        yield runtime
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_representative_rule_order_matches_design_after_merge(
    storage_runtime: StorageRuntime,
) -> None:
    """Merge recompute should follow canonical, completeness, time, and id order."""
    repository = DedupeClustersRepository(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
    )

    await _insert_item(
        runtime=storage_runtime,
        item_id=101,
        channel_id=11,
        message_id=101,
        canonical_url=None,
        title="t" * 80,
        body="b" * 80,
        published_at="2024-01-01 00:00:00",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=102,
        channel_id=11,
        message_id=102,
        canonical_url="https://example.com/102",
        title="t",
        body="b",
        published_at="2026-01-01 00:00:00",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=103,
        channel_id=11,
        message_id=103,
        canonical_url="https://example.com/103",
        title="t" * 20,
        body="b" * 20,
        published_at="2026-02-01 00:00:00",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=104,
        channel_id=12,
        message_id=104,
        canonical_url="https://example.com/104",
        title="t" * 20,
        body="b" * 20,
        published_at="2025-01-01 00:00:00",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=105,
        channel_id=12,
        message_id=105,
        canonical_url="https://example.com/105",
        title="t" * 20,
        body="b" * 20,
        published_at="2025-01-01 00:00:00",
    )

    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=1,
        representative_item_id=101,
    )
    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=2,
        representative_item_id=102,
    )
    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=3,
        representative_item_id=104,
    )

    for cluster_id, item_id in ((1, 101), (2, 102), (2, 103), (3, 104), (3, 105)):
        await _insert_member(
            runtime=storage_runtime,
            cluster_id=cluster_id,
            item_id=item_id,
        )

    _ = await repository.merge_clusters(matched_cluster_ids=[1, 2, 3])

    representative_item_id = await _read_cluster_representative(
        runtime=storage_runtime,
        cluster_id=1,
    )
    if representative_item_id != 104:  # noqa: PLR2004
        raise AssertionError


@pytest.mark.asyncio
async def test_merge_recompute_prefers_lowest_item_id_on_full_tie(
    storage_runtime: StorageRuntime,
) -> None:
    """Merge recompute must pick lowest item id when all prior rules tie."""
    repository = DedupeClustersRepository(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
    )

    for item_id in (301, 302):
        await _insert_item(
            runtime=storage_runtime,
            item_id=item_id,
            channel_id=12,
            message_id=item_id,
            canonical_url="https://example.com/tie",
            title="same",
            body="same",
            published_at="2025-01-01 00:00:00",
        )

    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=11,
        representative_item_id=302,
    )
    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=12,
        representative_item_id=302,
    )
    await _insert_member(runtime=storage_runtime, cluster_id=11, item_id=302)
    await _insert_member(runtime=storage_runtime, cluster_id=12, item_id=301)

    _ = await repository.merge_clusters(matched_cluster_ids=[11, 12])

    representative_item_id = await _read_cluster_representative(
        runtime=storage_runtime,
        cluster_id=11,
    )
    if representative_item_id != 301:  # noqa: PLR2004
        raise AssertionError


@pytest.mark.asyncio
async def test_recompute_runs_after_purge_operation(
    storage_runtime: StorageRuntime,
) -> None:
    """Purge path should recompute representative for affected non-empty clusters."""
    repository = ChannelsRepository(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
    )

    await _insert_item(
        runtime=storage_runtime,
        item_id=401,
        channel_id=11,
        message_id=401,
        canonical_url="https://example.com/removed",
        title="remove",
        body="remove",
        published_at="2024-01-01 00:00:00",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=501,
        channel_id=12,
        message_id=501,
        canonical_url="https://example.com/keep-a",
        title="same",
        body="same",
        published_at="2025-01-01 00:00:00",
    )
    await _insert_item(
        runtime=storage_runtime,
        item_id=502,
        channel_id=12,
        message_id=502,
        canonical_url="https://example.com/keep-b",
        title="same",
        body="same",
        published_at="2025-01-01 00:00:00",
    )

    await _insert_cluster(
        runtime=storage_runtime,
        cluster_id=21,
        representative_item_id=401,
    )
    await _insert_member(runtime=storage_runtime, cluster_id=21, item_id=401)
    await _insert_member(runtime=storage_runtime, cluster_id=21, item_id=501)
    await _insert_member(runtime=storage_runtime, cluster_id=21, item_id=502)

    purged = await repository.purge_channel(channel_id=11)
    if purged is None:
        raise AssertionError

    representative_item_id = await _read_cluster_representative(
        runtime=storage_runtime,
        cluster_id=21,
    )
    if representative_item_id != 501:  # noqa: PLR2004
        raise AssertionError


async def _seed_channel_rows(*, runtime: StorageRuntime) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (1, 111, X'01')
                """,
            ),
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
                VALUES (11, 1, 1111, 'chan-a', 'chan_a', 1)
                """,
            ),
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
                VALUES (12, 1, 2222, 'chan-b', 'chan_b', 1)
                """,
            ),
        )
        await session.commit()


async def _insert_item(  # noqa: PLR0913
    *,
    runtime: StorageRuntime,
    item_id: int,
    channel_id: int,
    message_id: int,
    canonical_url: str | None,
    title: str,
    body: str,
    published_at: str | None,
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
                    published_at,
                    canonical_url_hash,
                    content_hash,
                    dedupe_state
                )
                VALUES (
                    :id,
                    :channel_id,
                    :message_id,
                    :title,
                    :body,
                    :canonical_url,
                    :published_at,
                    NULL,
                    NULL,
                    'pending'
                )
                """,
            ),
            {
                "id": item_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "title": title,
                "body": body,
                "canonical_url": canonical_url,
                "published_at": published_at,
            },
        )
        await session.commit()


async def _insert_cluster(
    *,
    runtime: StorageRuntime,
    cluster_id: int,
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
                "cluster_key": f"cluster-{cluster_id}",
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


async def _read_cluster_representative(
    *,
    runtime: StorageRuntime,
    cluster_id: int,
) -> int | None:
    async with runtime.read_session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        """
                        SELECT representative_item_id
                        FROM dedupe_clusters
                        WHERE id = :cluster_id
                        """,
                    ),
                    {"cluster_id": cluster_id},
                )
            )
            .mappings()
            .one()
        )
    return cast("int | None", row["representative_item_id"])
