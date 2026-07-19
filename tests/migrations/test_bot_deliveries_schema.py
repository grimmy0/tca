"""Migration checks for bot_deliveries table schema (C092)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQLiteNameRow = tuple[str]
SQLiteIndexListRow = tuple[int, str, int, str, int]
SQLiteIndexInfoRow = tuple[int, int, str]
SQLiteTableInfoRow = tuple[int, str, str, int, str | None, int]


def test_bot_deliveries_table_exists_after_migration(tmp_path: Path) -> None:
    """Ensure bot_deliveries table is created after upgrading to head."""
    db_path = tmp_path / "c092-bot-deliveries.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
    name_rows = cast("list[SQLiteNameRow]", rows)
    table_names = {row[0] for row in name_rows}
    if "bot_deliveries" not in table_names:
        raise AssertionError


def test_bot_deliveries_columns(tmp_path: Path) -> None:
    """Ensure bot_deliveries table has correct columns and properties."""
    db_path = tmp_path / "c092-columns.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        column_rows = connection.execute(
            "PRAGMA table_info('bot_deliveries')",
        ).fetchall()
    typed_column_rows = cast("list[SQLiteTableInfoRow]", column_rows)
    columns = {row[1]: row for row in typed_column_rows}

    # Verify ID column
    id_row = columns.get("id")
    if id_row is None or id_row[2].upper() != "INTEGER" or id_row[5] != 1:
        raise AssertionError

    # Verify cluster_id column
    cluster_id_row = columns.get("cluster_id")
    if cluster_id_row is None or cluster_id_row[2].upper() != "INTEGER" or cluster_id_row[3] != 1:
        raise AssertionError

    # Verify delivered_at column
    delivered_at_row = columns.get("delivered_at")
    if delivered_at_row is None or delivered_at_row[3] != 1:
        raise AssertionError
    delivered_at_default = delivered_at_row[4] or ""
    if "CURRENT_TIMESTAMP" not in delivered_at_default.upper():
        raise AssertionError

    # Verify telegram_message_id column
    telegram_message_id_row = columns.get("telegram_message_id")
    if telegram_message_id_row is None or telegram_message_id_row[3] != 0:
        raise AssertionError


def test_bot_deliveries_foreign_key(tmp_path: Path) -> None:
    """Ensure cluster_id has a foreign key constraint to dedupe_clusters."""
    db_path = tmp_path / "c092-fk.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        # Try to insert into bot_deliveries with non-existent cluster_id
        try:
            connection.execute(
                "INSERT INTO bot_deliveries (cluster_id) VALUES (99999)",
            )
        except sqlite3.IntegrityError:
            return

    raise AssertionError


def test_bot_deliveries_unique_constraint(tmp_path: Path) -> None:
    """Ensure cluster_id has a unique constraint."""
    db_path = tmp_path / "c092-unique.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        # First insert a valid cluster_id. Note: we need a real cluster to satisfy FK.
        connection.execute(
            "INSERT INTO dedupe_clusters (cluster_key, representative_item_id) VALUES ('test-key', 1)",
        )
        connection.execute(
            "INSERT INTO bot_deliveries (cluster_id) VALUES (1)",
        )
        # Try to insert the duplicate cluster_id
        try:
            connection.execute(
                "INSERT INTO bot_deliveries (cluster_id) VALUES (1)",
            )
        except sqlite3.IntegrityError:
            return

    raise AssertionError


def test_bot_deliveries_index_exists(tmp_path: Path) -> None:
    """Ensure index exists on cluster_id column."""
    db_path = tmp_path / "c092-index.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        index_rows = connection.execute(
            "PRAGMA index_list('bot_deliveries')",
        ).fetchall()
    typed_index_rows = cast("list[SQLiteIndexListRow]", index_rows)
    indices = {row[1] for row in typed_index_rows}
    if "ix_bot_deliveries_cluster_id" not in indices:
        raise AssertionError


def test_bot_deliveries_downgrade(tmp_path: Path) -> None:
    """Ensure bot_deliveries table is removed on downgrade to previous revision."""
    db_path = tmp_path / "c092-downgrade.sqlite3"
    _upgrade_to_head(db_path)

    # Downgrade to previous revision (a3b4c5d6e7f8)
    result = _run_alembic_command(db_path, ("downgrade", "a3b4c5d6e7f8"))
    if result.returncode != 0:
        raise AssertionError

    with sqlite3.connect(db_path.as_posix()) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
    name_rows = cast("list[SQLiteNameRow]", rows)
    table_names = {row[0] for row in name_rows}
    if "bot_deliveries" in table_names:
        raise AssertionError


def _upgrade_to_head(db_path: Path) -> None:
    result = _run_alembic_command(db_path, ("upgrade", "head"))
    if result.returncode != 0:
        print("ALEMBIC UPGRADE FAILED:")
        print("Stdout:", result.stdout)
        print("Stderr:", result.stderr)
        raise AssertionError


def _run_alembic_command(
    db_path: Path,
    command_parts: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    alembic_executable = Path(sys.executable).with_name("alembic")
    if not alembic_executable.exists():
        raise AssertionError

    env = os.environ.copy()
    env["TCA_DB_PATH"] = db_path.as_posix()
    return subprocess.run(  # noqa: S603
        [alembic_executable.as_posix(), "-c", "alembic.ini", *command_parts],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
