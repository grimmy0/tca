"""End-to-end smoke test for retention prune and backup jobs."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.ops import NightlySQLiteBackupJob, OrderedRetentionPruneJob
from tca.storage import (
    SettingsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

EXPECTED_ITEMS_DELETED = 2


@pytest.fixture
async def ops_runtime(tmp_path: Path) -> AsyncIterator[tuple[StorageRuntime, Path]]:
    """Build runtime and schema for retention/backup integration smoke test."""
    db_path = tmp_path / "integration-retention-backup.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    await _create_ops_schema(runtime=runtime)

    try:
        yield runtime, db_path
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_retention_backup_smoke_prune_then_backup(
    ops_runtime: tuple[StorageRuntime, Path],
    tmp_path: Path,
) -> None:
    """Prune should preserve fresh rows, repair clusters, and backup validated DB."""
    runtime, db_path = ops_runtime
    now = datetime.now(UTC).replace(microsecond=0)
    old_created_at = now - timedelta(days=40)
    recent_created_at = now - timedelta(days=1)

    await _seed_base_records(runtime=runtime)
    await _seed_retention_settings(runtime=runtime)
    await _seed_prune_fixtures(
        runtime=runtime,
        old_created_at=old_created_at,
        recent_created_at=recent_created_at,
    )

    prune_job = OrderedRetentionPruneJob(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
        now_provider=lambda: now,
    )
    prune_summary = await prune_job.run_once()

    if prune_summary.raw_messages_deleted != 1:
        raise AssertionError
    if prune_summary.items_deleted != EXPECTED_ITEMS_DELETED:
        raise AssertionError
    if prune_summary.ingest_errors_deleted != 1:
        raise AssertionError

    backup_dir = tmp_path / "backups"
    backup_job = NightlySQLiteBackupJob(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
        db_path=db_path,
        backup_dir=backup_dir,
        now_provider=lambda: now,
    )
    backup_summary = await backup_job.run_once()

    if backup_summary.integrity_check_result != "ok":
        raise AssertionError
    if not backup_summary.backup_path.exists():
        raise AssertionError

    _assert_post_prune_cluster_invariants(db_path=db_path)
    _assert_backup_contains_pruned_state(db_path=backup_summary.backup_path)


async def _create_ops_schema(*, runtime: StorageRuntime) -> None:
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


async def _seed_base_records(*, runtime: StorageRuntime) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (1, 12345, :api_hash)
                """,
            ),
            {"api_hash": b"encrypted-api-hash"},
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_channels (
                    id,
                    account_id,
                    telegram_channel_id,
                    name,
                    username
                )
                VALUES (1, 1, 88001, 'retention-smoke', 'retention_smoke')
                """,
            ),
        )
        await session.commit()


async def _seed_retention_settings(*, runtime: StorageRuntime) -> None:
    repository = SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    _ = await repository.create(key="retention.raw_messages_days", value=30)
    _ = await repository.create(key="retention.items_days", value=30)
    _ = await repository.create(key="retention.ingest_errors_days", value=7)
    _ = await repository.create(key="backup.retain_count", value=2)


async def _seed_prune_fixtures(
    *,
    runtime: StorageRuntime,
    old_created_at: datetime,
    recent_created_at: datetime,
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
                VALUES
                    (1, 1, 1001, '{}', :old_created_at),
                    (2, 1, 1002, '{}', :recent_created_at)
                """,
            ),
            {"old_created_at": old_created_at, "recent_created_at": recent_created_at},
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO items (
                    id,
                    channel_id,
                    message_id,
                    raw_message_id,
                    published_at,
                    title,
                    body,
                    canonical_url,
                    dedupe_state,
                    created_at
                )
                VALUES
                    (
                        101, 1, 5001, 1, :old_created_at, 'old-title-a',
                        'old-body-a', 'https://example.com/old-a',
                        'clustered', :old_created_at
                    ),
                    (
                        102, 1, 5002, 2, :recent_created_at, 'new-title-a',
                        'new-body-a', 'https://example.com/new-a',
                        'clustered', :recent_created_at
                    ),
                    (
                        201, 1, 5003, NULL, :old_created_at, 'old-title-b',
                        'old-body-b', 'https://example.com/old-b',
                        'clustered', :old_created_at
                    )
                """,
            ),
            {"old_created_at": old_created_at, "recent_created_at": recent_created_at},
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO dedupe_clusters (id, cluster_key, representative_item_id)
                VALUES
                    (10, 'cluster-10', 101),
                    (20, 'cluster-20', 201)
                """,
            ),
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO dedupe_members (cluster_id, item_id)
                VALUES
                    (10, 101),
                    (10, 102),
                    (20, 201)
                """,
            ),
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO dedupe_decisions (
                    id,
                    item_id,
                    cluster_id,
                    candidate_item_id,
                    strategy_name,
                    outcome,
                    reason_code,
                    score,
                    metadata_json,
                    created_at
                )
                VALUES
                    (
                        1, 101, 10, 102, 'url_match', 'duplicate', 'url_match',
                        1.0, '{}', :old_created_at
                    ),
                    (
                        2, 102, 10, 101, 'url_match', 'duplicate', 'url_match',
                        1.0, '{}', :recent_created_at
                    )
                """,
            ),
            {"old_created_at": old_created_at, "recent_created_at": recent_created_at},
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO ingest_errors (
                    id,
                    channel_id,
                    stage,
                    error_code,
                    error_message,
                    payload_ref,
                    created_at
                )
                VALUES
                    (1, 1, 'poll', '429', 'old-flood-wait', 'raw/1', :old_created_at),
                    (
                        2, 1, 'poll', '500', 'recent-provider-error',
                        'raw/2', :recent_created_at
                    )
                """,
            ),
            {"old_created_at": old_created_at, "recent_created_at": recent_created_at},
        )
        await session.commit()


def _assert_post_prune_cluster_invariants(*, db_path: Path) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        item_rows = connection.execute(
            "SELECT id FROM items ORDER BY id ASC",
        ).fetchall()
        if item_rows != [(102,)]:
            raise AssertionError

        raw_rows = connection.execute(
            "SELECT id FROM raw_messages ORDER BY id ASC",
        ).fetchall()
        if raw_rows != [(2,)]:
            raise AssertionError

        error_rows = connection.execute(
            "SELECT id FROM ingest_errors ORDER BY id ASC",
        ).fetchall()
        if error_rows != [(2,)]:
            raise AssertionError

        cluster_rows = connection.execute(
            "SELECT id, representative_item_id FROM dedupe_clusters ORDER BY id ASC",
        ).fetchall()
        if cluster_rows != [(10, 102)]:
            raise AssertionError

        member_rows = connection.execute(
            """
            SELECT cluster_id, item_id
            FROM dedupe_members
            ORDER BY cluster_id ASC, item_id ASC
            """,
        ).fetchall()
        if member_rows != [(10, 102)]:
            raise AssertionError

        orphaned_member_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM dedupe_members AS dm
            LEFT JOIN items AS i ON i.id = dm.item_id
            LEFT JOIN dedupe_clusters AS dc ON dc.id = dm.cluster_id
            WHERE i.id IS NULL OR dc.id IS NULL
            """,
        ).fetchone()
        if orphaned_member_count != (0,):
            raise AssertionError

        orphaned_decision_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM dedupe_decisions AS dd
            LEFT JOIN items AS i ON i.id = dd.item_id
            LEFT JOIN dedupe_clusters AS dc ON dc.id = dd.cluster_id
            WHERE i.id IS NULL OR (dd.cluster_id IS NOT NULL AND dc.id IS NULL)
            """,
        ).fetchone()
        if orphaned_decision_count != (0,):
            raise AssertionError


def _assert_backup_contains_pruned_state(*, db_path: Path) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity_row != ("ok",):
            raise AssertionError

        cluster_rows = connection.execute(
            "SELECT id, representative_item_id FROM dedupe_clusters ORDER BY id ASC",
        ).fetchall()
        if cluster_rows != [(10, 102)]:
            raise AssertionError
