"""Tests for mandatory SQLite PRAGMA runtime settings."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tca.config.settings import load_settings
from tca.storage import StorageRuntime, create_storage_runtime, dispose_storage_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

EXPECTED_PRAGMAS: dict[str, str | int] = {
    "journal_mode": "wal",
    "synchronous": 1,
    "foreign_keys": 1,
    "busy_timeout": 5000,
}


@pytest.fixture
async def storage_runtime(tmp_path: Path) -> AsyncIterator[StorageRuntime]:
    """Create and teardown isolated storage runtime for PRAGMA assertions."""
    db_path = tmp_path / "sqlite-pragmas.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    try:
        yield runtime
    finally:
        await dispose_storage_runtime(runtime)


async def _read_pragmas(engine: AsyncEngine) -> dict[str, str | int]:
    values: dict[str, str | int] = {}
    async with engine.connect() as connection:
        for pragma_name in EXPECTED_PRAGMAS:
            result = await connection.exec_driver_sql(f"PRAGMA {pragma_name};")
            value_obj = cast("object", result.scalar_one())
            if isinstance(value_obj, str):
                values[pragma_name] = value_obj.lower()
                continue
            if isinstance(value_obj, int):
                values[pragma_name] = value_obj
                continue
            raise AssertionError
    return values


@pytest.mark.asyncio
async def test_runtime_pragmas_match_design_on_fresh_connection(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure mandatory PRAGMA values are applied to fresh read connection."""
    values = await _read_pragmas(storage_runtime.read_engine)
    if values != EXPECTED_PRAGMAS:
        raise AssertionError


@pytest.mark.asyncio
async def test_pragmas_match_exact_values_for_writer_connection(
    storage_runtime: StorageRuntime,
) -> None:
    """Ensure writer connections receive the same exact PRAGMA settings."""
    values = await _read_pragmas(storage_runtime.write_engine)
    if values != EXPECTED_PRAGMAS:
        raise AssertionError


@pytest.mark.asyncio
async def test_pragmas_are_reapplied_on_each_new_connection(
    storage_runtime: StorageRuntime,
) -> None:
    """Regression guard: verify expected PRAGMA keys/values on repeated connects."""
    first = await _read_pragmas(storage_runtime.read_engine)
    second = await _read_pragmas(storage_runtime.read_engine)
    if first != EXPECTED_PRAGMAS:
        raise AssertionError
    if second != EXPECTED_PRAGMAS:
        raise AssertionError
