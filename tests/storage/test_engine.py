"""Tests for SQLAlchemy async engine/session wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.storage import StorageRuntime, create_storage_runtime, dispose_storage_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def storage_runtime(tmp_path: Path) -> AsyncIterator[tuple[StorageRuntime, Path]]:
    """Create and teardown isolated read/write engine runtime per test."""
    db_path = tmp_path / "engine-wiring.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    try:
        yield runtime, db_path
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_engine_initializes_against_configured_db_path(
    storage_runtime: tuple[StorageRuntime, Path],
) -> None:
    """Ensure read/write engines bind to the configured SQLite file path."""
    runtime, db_path = storage_runtime
    expected = db_path.as_posix()
    if runtime.read_engine.url.database != expected:
        raise AssertionError
    if runtime.write_engine.url.database != expected:
        raise AssertionError


@pytest.mark.asyncio
async def test_session_factory_can_execute_select_one(
    storage_runtime: tuple[StorageRuntime, Path],
) -> None:
    """Ensure session factory executes baseline SQL query successfully."""
    runtime, _ = storage_runtime
    async with runtime.read_session_factory() as session:
        result = await session.execute(text("SELECT 1"))
    if result.scalar_one() != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_fixture_supports_clean_read_and_write_session_lifecycle(
    storage_runtime: tuple[StorageRuntime, Path],
) -> None:
    """Ensure fixture can open and close read/write sessions without leaks."""
    runtime, _ = storage_runtime
    async with runtime.read_session_factory() as read_session:
        _ = await read_session.execute(text("SELECT 1"))
    async with runtime.write_session_factory() as write_session:
        _ = await write_session.execute(text("SELECT 1"))
