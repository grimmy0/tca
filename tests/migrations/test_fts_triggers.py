"""Migration checks for FTS5 synchronization triggers (C017)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQLiteRowIdRow = tuple[int]


def test_insert_into_items_appears_in_fts_results(tmp_path: Path) -> None:
    """Ensure INSERT trigger adds new `items` rows to `items_fts`."""
    db_path = tmp_path / "c017-insert-trigger.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        channel_id = _insert_account_and_channel(connection)
        item_rowid = _insert_item(
            connection,
            channel_id=channel_id,
            message_id=5001,
            title="Insert trigger headline",
            body="c017insertterm now searchable",
        )
        match_rows = connection.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?",
            ("c017insertterm",),
        ).fetchall()

    typed_rows = cast("list[SQLiteRowIdRow]", match_rows)
    if {row[0] for row in typed_rows} != {item_rowid}:
        raise AssertionError


def test_update_modifies_fts_searchable_text(tmp_path: Path) -> None:
    """Ensure UPDATE trigger removes stale terms and adds fresh searchable text."""
    db_path = tmp_path / "c017-update-trigger.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        channel_id = _insert_account_and_channel(connection)
        item_rowid = _insert_item(
            connection,
            channel_id=channel_id,
            message_id=5002,
            title="Before update",
            body="c017oldterm is present",
        )

        _ = connection.execute(
            "UPDATE items SET title = ?, body = ? WHERE id = ?",
            ("After update", "c017newterm replaced old text", item_rowid),
        )

        old_match_rows = connection.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?",
            ("c017oldterm",),
        ).fetchall()
        new_match_rows = connection.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?",
            ("c017newterm",),
        ).fetchall()

    typed_old_rows = cast("list[SQLiteRowIdRow]", old_match_rows)
    typed_new_rows = cast("list[SQLiteRowIdRow]", new_match_rows)
    if typed_old_rows:
        raise AssertionError
    if {row[0] for row in typed_new_rows} != {item_rowid}:
        raise AssertionError


def test_delete_removes_fts_hit(tmp_path: Path) -> None:
    """Ensure DELETE trigger removes rows from `items_fts` search hits."""
    db_path = tmp_path / "c017-delete-trigger.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        channel_id = _insert_account_and_channel(connection)
        item_rowid = _insert_item(
            connection,
            channel_id=channel_id,
            message_id=5003,
            title="Delete trigger headline",
            body="c017deleteterm should disappear",
        )

        before_delete = connection.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?",
            ("c017deleteterm",),
        ).fetchall()
        _ = connection.execute("DELETE FROM items WHERE id = ?", (item_rowid,))
        after_delete = connection.execute(
            "SELECT rowid FROM items_fts WHERE items_fts MATCH ?",
            ("c017deleteterm",),
        ).fetchall()

    typed_before_delete = cast("list[SQLiteRowIdRow]", before_delete)
    typed_after_delete = cast("list[SQLiteRowIdRow]", after_delete)
    if {row[0] for row in typed_before_delete} != {item_rowid}:
        raise AssertionError
    if typed_after_delete:
        raise AssertionError


def _insert_account_and_channel(connection: sqlite3.Connection) -> int:
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

    return channel_rowid


def _insert_item(
    connection: sqlite3.Connection,
    *,
    channel_id: int,
    message_id: int,
    title: str,
    body: str,
) -> int:
    item_rowid = connection.execute(
        """
INSERT INTO items (channel_id, message_id, title, body)
VALUES (?, ?, ?, ?)
""".strip(),
        (channel_id, message_id, title, body),
    ).lastrowid
    if item_rowid is None:
        raise AssertionError
    return item_rowid


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
