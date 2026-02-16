"""Shared pytest fixtures for local storage and concurrency tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


async def _configure_sqlite_connection(conn: aiosqlite.Connection) -> None:
    # Keep lock behavior deterministic for concurrency tests.
    _ = await conn.execute("PRAGMA journal_mode=WAL;")
    _ = await conn.execute("PRAGMA synchronous=NORMAL;")
    _ = await conn.execute("PRAGMA foreign_keys=ON;")
    _ = await conn.execute("PRAGMA busy_timeout=0;")
    await conn.commit()


@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> Path:
    """Provide a per-test SQLite file path for storage tests."""
    return tmp_path / "storage-concurrency.sqlite3"


@pytest.fixture
async def sqlite_writer_pair(
    sqlite_db_path: Path,
) -> AsyncIterator[tuple[aiosqlite.Connection, aiosqlite.Connection]]:
    """Two local SQLite connections for deterministic write-lock tests."""
    first = await aiosqlite.connect(sqlite_db_path.as_posix(), timeout=0)
    second = await aiosqlite.connect(sqlite_db_path.as_posix(), timeout=0)

    await _configure_sqlite_connection(first)
    await _configure_sqlite_connection(second)

    _ = await first.execute(
        """
        CREATE TABLE IF NOT EXISTS lock_probe (
            id INTEGER PRIMARY KEY,
            note TEXT NOT NULL
        )
        """,
    )
    await first.commit()

    try:
        yield first, second
    finally:
        await first.close()
        await second.close()
