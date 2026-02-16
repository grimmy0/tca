"""Migration checks for base ops/config schema (C014)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_OPS_TABLES = frozenset({"ingest_errors", "notifications", "settings"})
SQLiteNameRow = tuple[str]
SQLiteIndexListRow = tuple[int, str, int, str, int]
SQLiteIndexInfoRow = tuple[int, int, str]
SQLiteTableInfoRow = tuple[int, str, str, int, str | None, int]


def test_ops_config_tables_exist_after_migration(tmp_path: Path) -> None:
    """Ensure C014 ops/config tables are created."""
    db_path = tmp_path / "c014-ops.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
    name_rows = cast("list[SQLiteNameRow]", rows)
    table_names = {row[0] for row in name_rows}
    if not EXPECTED_OPS_TABLES.issubset(table_names):
        raise AssertionError


def test_settings_key_uniqueness_exists(tmp_path: Path) -> None:
    """Ensure uniqueness exists on `settings.key`."""
    db_path = tmp_path / "c014-settings-unique.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        if _has_unique_constraint(connection, "settings", ["key"]):
            return

    raise AssertionError


def test_ingest_errors_has_required_stage_and_timestamp_fields(
    tmp_path: Path,
) -> None:
    """Ensure `ingest_errors` has non-null stage and timestamp fields."""
    db_path = tmp_path / "c014-ingest-errors-columns.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        column_rows = connection.execute(
            "PRAGMA table_info('ingest_errors')",
        ).fetchall()
    typed_column_rows = cast("list[SQLiteTableInfoRow]", column_rows)
    columns = {row[1]: row for row in typed_column_rows}

    stage_row = columns.get("stage")
    created_at_row = columns.get("created_at")
    if stage_row is None or created_at_row is None:
        raise AssertionError

    if stage_row[3] != 1:
        raise AssertionError
    if created_at_row[3] != 1:
        raise AssertionError

    created_at_default = created_at_row[4] or ""
    if "CURRENT_TIMESTAMP" not in created_at_default.upper():
        raise AssertionError


def test_ops_config_tables_are_removed_on_downgrade_to_base(tmp_path: Path) -> None:
    """Ensure C014 tables are removed when downgrading back to base revision."""
    db_path = tmp_path / "c014-downgrade.sqlite3"
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
    if EXPECTED_OPS_TABLES & table_names:
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
