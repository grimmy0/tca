"""Tests for crash-safe key rotation metadata resume behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.auth import KeyRotationRepository
from tca.config.settings import load_settings
from tca.storage import StorageRuntime, create_storage_runtime, dispose_storage_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

DEFAULT_TARGET_VERSION = 2


@pytest.fixture
async def rotation_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[KeyRotationRepository, StorageRuntime]]:
    """Create isolated schema fixture for key rotation resume tests."""
    db_path = tmp_path / "key-rotation.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_accounts (
                id INTEGER PRIMARY KEY,
                api_id INTEGER NOT NULL,
                api_hash_encrypted BLOB NOT NULL,
                session_encrypted BLOB NULL,
                key_version INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS auth_key_rotation (
                id INTEGER PRIMARY KEY,
                target_key_version INTEGER NOT NULL,
                last_rotated_account_id INTEGER NOT NULL DEFAULT 0,
                started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME NULL
            )
            """,
        )

    async with runtime.write_session_factory() as session:
        for account_id in (1, 2, 3):
            _ = await session.execute(
                text(
                    """
                    INSERT INTO telegram_accounts (
                        id,
                        api_id,
                        api_hash_encrypted,
                        key_version
                    )
                    VALUES (:id, :api_id, :api_hash_encrypted, :key_version)
                    """,
                ),
                {
                    "id": account_id,
                    "api_id": 1000 + account_id,
                    "api_hash_encrypted": b"ciphertext",
                    "key_version": 1,
                },
            )
        await session.commit()

    try:
        yield (
            KeyRotationRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_rotation_state_persists_progress(
    rotation_runtime: tuple[KeyRotationRepository, StorageRuntime],
) -> None:
    """Ensure rotation metadata records progress after each rotated row."""
    repository, _ = rotation_runtime
    _ = await repository.begin_rotation(target_key_version=DEFAULT_TARGET_VERSION)
    next_id = await repository.next_pending_account_id()
    if next_id != 1:
        raise AssertionError

    await repository.mark_account_rotated(account_id=next_id)
    state = await repository.get_state()

    if state is None or state.last_rotated_account_id != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_interrupted_rotation_resumes_at_next_pending_row(
    rotation_runtime: tuple[KeyRotationRepository, StorageRuntime],
) -> None:
    """Ensure resume picks up from the next pending account row."""
    repository, runtime = rotation_runtime
    _ = await repository.begin_rotation(target_key_version=DEFAULT_TARGET_VERSION)
    await repository.mark_account_rotated(account_id=1)

    resumed = KeyRotationRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    next_id = await resumed.next_pending_account_id()

    if next_id != 2:
        raise AssertionError


@pytest.mark.asyncio
async def test_completion_state_only_set_after_all_rows_rotated(
    rotation_runtime: tuple[KeyRotationRepository, StorageRuntime],
) -> None:
    """Ensure completion metadata is only set after all rows are rotated."""
    repository, _ = rotation_runtime
    _ = await repository.begin_rotation(target_key_version=DEFAULT_TARGET_VERSION)
    await repository.mark_account_rotated(account_id=1)

    completed_early = await repository.complete_if_finished()
    state = await repository.get_state()

    if completed_early or state is None or state.completed_at is not None:
        raise AssertionError

    await repository.mark_account_rotated(account_id=2)
    await repository.mark_account_rotated(account_id=3)

    completed = await repository.complete_if_finished()
    state = await repository.get_state()

    if not completed or state is None or state.completed_at is None:
        raise AssertionError
