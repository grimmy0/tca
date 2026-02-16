"""Migration checks for base content and dedupe schema (C013)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CONTENT_TABLES = frozenset(
    {
        "raw_messages",
        "items",
        "dedupe_clusters",
        "dedupe_members",
        "dedupe_decisions",
    },
)
SQLiteNameRow = tuple[str]
SQLiteIndexListRow = tuple[int, str, int, str, int]
SQLiteIndexInfoRow = tuple[int, int, str]
SQLiteForeignKeyRow = tuple[int, int, str, str, str, str, str, str]


def test_content_and_dedupe_tables_exist_after_migration(tmp_path: Path) -> None:
    """Ensure C013 content and dedupe tables are created."""
    db_path = tmp_path / "c013-content-dedupe.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
    name_rows = cast("list[SQLiteNameRow]", rows)
    table_names = {row[0] for row in name_rows}
    if not EXPECTED_CONTENT_TABLES.issubset(table_names):
        raise AssertionError


def test_items_raw_message_id_fk_uses_set_null_on_delete(tmp_path: Path) -> None:
    """Ensure `items.raw_message_id` FK is configured with `ON DELETE SET NULL`."""
    db_path = tmp_path / "c013-items-raw-fk.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        fk_rows = connection.execute(
            "PRAGMA foreign_key_list('items')",
        ).fetchall()
    typed_fk_rows = cast("list[SQLiteForeignKeyRow]", fk_rows)

    for fk_row in typed_fk_rows:
        if fk_row[3] != "raw_message_id":
            continue
        if fk_row[2] != "raw_messages":
            continue
        if fk_row[4] != "id":
            continue
        if fk_row[6] == "SET NULL":
            return
        raise AssertionError

    raise AssertionError


def test_items_channel_message_uniqueness_exists(tmp_path: Path) -> None:
    """Ensure uniqueness exists on `items(channel_id, message_id)`."""
    db_path = tmp_path / "c013-items-unique.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        if _has_unique_constraint(connection, "items", ["channel_id", "message_id"]):
            return

    raise AssertionError


def test_raw_messages_channel_message_uniqueness_exists(tmp_path: Path) -> None:
    """Ensure uniqueness exists on `raw_messages(channel_id, message_id)`."""
    db_path = tmp_path / "c013-raw-messages-unique.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        if _has_unique_constraint(
            connection,
            "raw_messages",
            ["channel_id", "message_id"],
        ):
            return

    raise AssertionError


def test_dedupe_members_cluster_item_uniqueness_exists(tmp_path: Path) -> None:
    """Ensure uniqueness exists on `dedupe_members(cluster_id, item_id)`."""
    db_path = tmp_path / "c013-members-unique.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        if _has_unique_constraint(
            connection,
            "dedupe_members",
            ["cluster_id", "item_id"],
        ):
            return

    raise AssertionError


def test_content_and_dedupe_tables_are_removed_on_downgrade_to_base(
    tmp_path: Path,
) -> None:
    """Ensure C013 tables are removed when downgrading back to base revision."""
    db_path = tmp_path / "c013-downgrade.sqlite3"
    _upgrade_to_head(db_path)

    result = _run_alembic_command(db_path, ("downgrade", "base"))
    if result.returncode != 0:
        raise AssertionError

    with sqlite3.connect(db_path.as_posix()) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
    name_rows = cast("list[SQLiteNameRow]", rows)
    table_names = {row[0] for row in name_rows}
    if EXPECTED_CONTENT_TABLES & table_names:
        raise AssertionError


def _has_unique_constraint(
    connection: sqlite3.Connection,
    table_name: str,
    expected_columns: list[str],
) -> bool:
    index_rows = connection.execute(
        f"PRAGMA index_list('{table_name}')",
    ).fetchall()
    typed_index_rows = cast("list[SQLiteIndexListRow]", index_rows)

    for index_row in typed_index_rows:
        if index_row[2] != 1:
            continue

        index_name = index_row[1]
        column_rows = connection.execute(
            f"PRAGMA index_info('{index_name}')",
        ).fetchall()
        typed_column_rows = cast("list[SQLiteIndexInfoRow]", column_rows)
        ordered_column_rows = sorted(typed_column_rows, key=lambda row: row[0])
        indexed_columns = [row[2] for row in ordered_column_rows]

        if indexed_columns == expected_columns:
            return True

    return False


def _upgrade_to_head(db_path: Path) -> None:
    result = _run_alembic_command(db_path, ("upgrade", "head"))
    if result.returncode != 0:
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
