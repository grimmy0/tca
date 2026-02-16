from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest


async def _configure_sqlite_connection(conn: aiosqlite.Connection) -> None:
    # Keep lock behavior deterministic for concurrency tests.
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    await conn.execute("PRAGMA busy_timeout=0;")
    await conn.commit()


@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> Path:
    return tmp_path / "storage-concurrency.sqlite3"


@pytest.fixture
async def sqlite_writer_pair(
    sqlite_db_path: Path,
) -> tuple[aiosqlite.Connection, aiosqlite.Connection]:
    """Two local SQLite connections for deterministic write-lock tests."""
    first = await aiosqlite.connect(sqlite_db_path.as_posix(), timeout=0)
    second = await aiosqlite.connect(sqlite_db_path.as_posix(), timeout=0)

    await _configure_sqlite_connection(first)
    await _configure_sqlite_connection(second)

    await first.execute(
        """
        CREATE TABLE IF NOT EXISTS lock_probe (
            id INTEGER PRIMARY KEY,
            note TEXT NOT NULL
        )
        """
    )
    await first.commit()

    try:
        yield first, second
    finally:
        await first.close()
        await second.close()

