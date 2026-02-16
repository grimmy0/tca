"""Migration checks for base account/channel/group schema (C012)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_BASE_TABLES = frozenset(
    {
        "telegram_accounts",
        "telegram_channels",
        "channel_groups",
        "channel_group_members",
        "channel_state",
    },
)
SQLiteNameRow = tuple[str]
SQLiteIndexListRow = tuple[int, str, int, str, int]
SQLiteIndexInfoRow = tuple[int, int, str]
SQLiteForeignKeyRow = tuple[int, int, str, str, str, str, str, str]


def test_base_group_tables_exist_after_migration(tmp_path: Path) -> None:
    """Ensure C012 base account/channel/group tables are created."""
    db_path = tmp_path / "c012-base-groups.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
    name_rows = cast("list[SQLiteNameRow]", rows)
    table_names = {row[0] for row in name_rows}
    if not EXPECTED_BASE_TABLES.issubset(table_names):
        raise AssertionError


def test_channel_group_members_channel_id_has_unique_constraint(
    tmp_path: Path,
) -> None:
    """Ensure one-channel-per-group-membership rule is enforced by uniqueness."""
    db_path = tmp_path / "c012-group-member-unique.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        index_rows = connection.execute(
            "PRAGMA index_list('channel_group_members')",
        ).fetchall()
        typed_index_rows = cast("list[SQLiteIndexListRow]", index_rows)
        unique_index_names = [row[1] for row in typed_index_rows if row[2] == 1]

        for index_name in unique_index_names:
            column_rows = connection.execute(
                f"PRAGMA index_info('{index_name}')",
            ).fetchall()
            typed_column_rows = cast("list[SQLiteIndexInfoRow]", column_rows)
            indexed_columns = [row[2] for row in typed_column_rows]
            if indexed_columns == ["channel_id"]:
                return

    raise AssertionError


def test_group_schema_foreign_keys_resolve_correctly(tmp_path: Path) -> None:
    """Ensure foreign-key links across C012 tables point to expected parents."""
    db_path = tmp_path / "c012-group-fks.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        channels_fk = _fk_details(connection, "telegram_channels")
        members_fk = _fk_details(connection, "channel_group_members")
        state_fk = _fk_details(connection, "channel_state")

    if channels_fk != {("account_id", "telegram_accounts", "id", "CASCADE")}:
        raise AssertionError
    if members_fk != {
        ("channel_id", "telegram_channels", "id", "CASCADE"),
        ("group_id", "channel_groups", "id", "CASCADE"),
    }:
        raise AssertionError
    if state_fk != {("channel_id", "telegram_channels", "id", "CASCADE")}:
        raise AssertionError


def test_base_group_tables_are_removed_on_downgrade_to_base(
    tmp_path: Path,
) -> None:
    """Ensure C012 tables are removed when downgrading back to base revision."""
    db_path = tmp_path / "c012-downgrade.sqlite3"
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
    if EXPECTED_BASE_TABLES & table_names:
        raise AssertionError


def _fk_details(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[tuple[str, str, str, str]]:
    rows = connection.execute(
        f"PRAGMA foreign_key_list('{table_name}')",
    ).fetchall()
    typed_rows = cast("list[SQLiteForeignKeyRow]", rows)
    return {(row[3], row[2], row[4], row[6]) for row in typed_rows}


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
