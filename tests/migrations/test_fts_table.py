"""Migration checks for FTS5 external-content table setup (C016)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "c2f9c1e5a7b3"
FTS_TABLE_NAME = "items_fts"
SQLiteNameRow = tuple[str]
SQLiteRowIdRow = tuple[int]


def test_fts_table_exists_and_supports_match_queries(tmp_path: Path) -> None:
    """Ensure C016 creates an FTS5 table and supports MATCH queries."""
    db_path = tmp_path / "c016-fts-match.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        if FTS_TABLE_NAME not in _table_names(connection):
            raise AssertionError

        account_rowid = connection.execute(
            "INSERT INTO telegram_accounts (api_id, api_hash_encrypted) VALUES (?, ?)",
            (1, b"api-hash"),
        ).lastrowid
        if account_rowid is None:
            raise AssertionError

        channel_rowid = connection.execute(
            """
INSERT INTO telegram_channels (account_id, telegram_channel_id, name)
VALUES (?, ?, ?)
""".strip(),
            (account_rowid, 101, "channel"),
        ).lastrowid
        if channel_rowid is None:
            raise AssertionError

        item_rowid = connection.execute(
            """
INSERT INTO items (channel_id, message_id, title, body)
VALUES (?, ?, ?, ?)
""".strip(),
            (
                channel_rowid,
                5001,
                "Breaking comet signal",
                "rareterm42 observed in station report",
            ),
        ).lastrowid
        if item_rowid is None:
            raise AssertionError

        _ = connection.execute("INSERT INTO items_fts(items_fts) VALUES ('rebuild')")
        match_rows = connection.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?",
            ("rareterm42",),
        ).fetchall()

    typed_match_rows = cast("list[SQLiteRowIdRow]", match_rows)
    if {row[0] for row in typed_match_rows} != {item_rowid}:
        raise AssertionError


def test_fts_table_is_removed_when_downgrading_to_c015(tmp_path: Path) -> None:
    """Ensure C016 FTS table is removed when downgrading to C015 revision."""
    db_path = tmp_path / "c016-downgrade.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        if FTS_TABLE_NAME not in _table_names(connection):
            raise AssertionError

    result = _run_alembic_command(db_path, ("downgrade", PREVIOUS_REVISION))
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)

    with sqlite3.connect(db_path.as_posix()) as connection:
        if FTS_TABLE_NAME in _table_names(connection):
            raise AssertionError


def test_fts_migration_runs_on_sqlite_without_manual_intervention(
    tmp_path: Path,
) -> None:
    """Ensure `alembic upgrade head` applies C016 on SQLite as-is."""
    db_path = tmp_path / "c016-sqlite-upgrade.sqlite3"
    result = _run_alembic_command(db_path, ("upgrade", "head"))
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)

    with sqlite3.connect(db_path.as_posix()) as connection:
        if FTS_TABLE_NAME not in _table_names(connection):
            raise AssertionError


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'",
    ).fetchall()
    typed_rows = cast("list[SQLiteNameRow]", rows)
    return {row[0] for row in typed_rows}


def _upgrade_to_head(db_path: Path) -> None:
    result = _run_alembic_command(db_path, ("upgrade", "head"))
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


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
