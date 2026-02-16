"""Concurrency tests for SQLite BEGIN IMMEDIATE behavior."""

from __future__ import annotations

import sqlite3

import aiosqlite
import pytest


@pytest.mark.asyncio
async def test_begin_immediate_surfaces_sqlite_busy_with_second_writer(
    sqlite_writer_pair: tuple[aiosqlite.Connection, aiosqlite.Connection],
) -> None:
    """Assert second writer fails while first writer holds an immediate lock."""
    holder, contender = sqlite_writer_pair

    await holder.execute("BEGIN IMMEDIATE")
    await holder.execute("INSERT INTO lock_probe(note) VALUES ('holder')")

    with pytest.raises((sqlite3.OperationalError, aiosqlite.OperationalError)) as exc:
        await contender.execute("BEGIN IMMEDIATE")

    error_text = str(exc.value).lower()
    if "locked" not in error_text:
        raise AssertionError

    await holder.execute("ROLLBACK")
