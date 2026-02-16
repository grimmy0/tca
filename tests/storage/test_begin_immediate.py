"""Concurrency tests for SQLite BEGIN IMMEDIATE behavior."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, cast

import aiosqlite
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from tca.config.settings import load_settings
from tca.storage import StorageRuntime, create_storage_runtime, dispose_storage_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.mark.asyncio
async def test_begin_immediate_surfaces_sqlite_busy_with_second_writer(
    sqlite_writer_pair: tuple[aiosqlite.Connection, aiosqlite.Connection],
) -> None:
    """Assert second writer fails while first writer holds an immediate lock."""
    holder, contender = sqlite_writer_pair

    _ = await holder.execute("BEGIN IMMEDIATE")
    _ = await holder.execute("INSERT INTO lock_probe(note) VALUES ('holder')")

    with pytest.raises((sqlite3.OperationalError, aiosqlite.OperationalError)) as exc:
        _ = await contender.execute("BEGIN IMMEDIATE")

    error_text = str(exc.value).lower()
    if "locked" not in error_text:
        raise AssertionError

    _ = await holder.execute("ROLLBACK")


@pytest.fixture
async def storage_runtime(tmp_path: Path) -> AsyncIterator[StorageRuntime]:
    """Create and teardown storage runtime for BEGIN IMMEDIATE tests."""
    db_path = tmp_path / "begin-immediate.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS lock_probe (
                id INTEGER PRIMARY KEY,
                note TEXT NOT NULL
            )
            """,
        )
    try:
        yield runtime
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_writer_transactions_emit_begin_immediate(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure write transactions are opened using BEGIN IMMEDIATE."""
    statements: list[str] = []

    def _capture_statement(  # noqa: PLR0913
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: object,
    ) -> None:
        _ = (conn, cursor, parameters, context, executemany)
        statements.append(statement.upper())

    event.listen(
        storage_runtime.write_engine.sync_engine,
        "before_cursor_execute",
        _capture_statement,
    )
    try:
        async with storage_runtime.write_session_factory() as session, session.begin():
            _ = await session.execute(text("SELECT 1"))
    finally:
        event.remove(
            storage_runtime.write_engine.sync_engine,
            "before_cursor_execute",
            _capture_statement,
        )

    if not any(statement.startswith("BEGIN IMMEDIATE") for statement in statements):
        raise AssertionError


@pytest.mark.asyncio
async def test_read_transactions_remain_unaffected_by_begin_immediate(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure read sessions can query while writer holds an IMMEDIATE transaction."""
    read_statements: list[str] = []

    def _capture_read_statement(  # noqa: PLR0913
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: object,
    ) -> None:
        _ = (conn, cursor, parameters, context, executemany)
        read_statements.append(statement.upper())

    event.listen(
        storage_runtime.read_engine.sync_engine,
        "before_cursor_execute",
        _capture_read_statement,
    )
    count: object = 0
    try:
        async with storage_runtime.write_session_factory() as writer, writer.begin():
            _ = await writer.execute(
                text("INSERT INTO lock_probe(note) VALUES ('w1')"),
            )
            async with storage_runtime.read_session_factory() as reader:
                result = await reader.execute(
                    text("SELECT COUNT(*) FROM lock_probe"),
                )
                count = cast("object", result.scalar_one())
    finally:
        event.remove(
            storage_runtime.read_engine.sync_engine,
            "before_cursor_execute",
            _capture_read_statement,
        )

    if cast("int", count) < 0:
        raise AssertionError
    if any(statement.startswith("BEGIN IMMEDIATE") for statement in read_statements):
        raise AssertionError


@pytest.mark.asyncio
async def test_writer_lock_acquisition_is_deterministic_with_begin_immediate(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure second writer fails deterministically while first writer holds lock."""
    async with storage_runtime.write_session_factory() as holder, holder.begin():
        _ = await holder.execute(
            text("INSERT INTO lock_probe(note) VALUES ('holder')"),
        )
        async with storage_runtime.write_session_factory() as contender:
            with pytest.raises(OperationalError):
                async with contender.begin():
                    _ = await contender.execute(
                        text("INSERT INTO lock_probe(note) VALUES ('contender')"),
                    )
