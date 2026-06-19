"""Migration checks for items embedding column."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQLiteTableInfoRow = tuple[int, str, str, int, object, int]


def test_items_embedding_column_exists_after_upgrade(tmp_path: Path) -> None:
    """Ensure embedding column exists on items table after migration."""
    db_path = tmp_path / "embedding-upgrade.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        rows = connection.execute("PRAGMA table_info('items')").fetchall()
    typed_rows = cast("list[SQLiteTableInfoRow]", rows)
    column_names = {row[1] for row in typed_rows}

    if "embedding" not in column_names:
        raise AssertionError


def test_items_embedding_column_removed_on_downgrade(tmp_path: Path) -> None:
    """Ensure embedding column is removed when downgrading by one revision."""
    db_path = tmp_path / "embedding-downgrade.sqlite3"
    _upgrade_to_head(db_path)

    result = _run_alembic_command(db_path, ("downgrade", "-1"))
    if result.returncode != 0:
        raise AssertionError

    with sqlite3.connect(db_path.as_posix()) as connection:
        rows = connection.execute("PRAGMA table_info('items')").fetchall()
    typed_rows = cast("list[SQLiteTableInfoRow]", rows)
    column_names = {row[1] for row in typed_rows}

    if "embedding" in column_names:
        raise AssertionError


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
