"""Tests for temporary auth session state storage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.auth import (
    AuthSessionExpiredError,
    AuthSessionStateRepository,
)
from tca.config.settings import load_settings
from tca.storage import StorageRuntime, create_storage_runtime, dispose_storage_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def auth_session_state_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[AuthSessionStateRepository, StorageRuntime]]:
    """Create isolated schema fixture for auth session state storage."""
    db_path = tmp_path / "auth-session-state.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS auth_session_state (
                session_id TEXT PRIMARY KEY,
                phone_number TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                telegram_session TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )

    try:
        yield (
            AuthSessionStateRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_auth_session_state_has_expiry(
    auth_session_state_runtime: tuple[AuthSessionStateRepository, StorageRuntime],
) -> None:
    """Ensure auth session state persists expiry values."""
    repository, _ = auth_session_state_runtime
    now = int(datetime.now(tz=UTC).timestamp())
    expires_at = now + 300

    created = await repository.create_session(
        session_id="session-expiry-1",
        phone_number="+15555550001",
        status="phone_submitted",
        expires_at=expires_at,
    )
    loaded = await repository.get_session(session_id=created.session_id)

    if loaded.expires_at != expires_at:
        raise AssertionError


@pytest.mark.asyncio
async def test_expired_session_is_rejected(
    auth_session_state_runtime: tuple[AuthSessionStateRepository, StorageRuntime],
) -> None:
    """Ensure expired auth sessions raise deterministic errors."""
    repository, runtime = auth_session_state_runtime
    now = int(datetime.now(tz=UTC).timestamp())

    _ = await repository.create_session(
        session_id="expired-session-1",
        phone_number="+15555550002",
        status="phone_submitted",
        expires_at=now - 5,
    )

    with pytest.raises(
        AuthSessionExpiredError,
        match=r"Auth session state expired for session_id='expired-session-1'\.",
    ):
        _ = await repository.get_session(session_id="expired-session-1")

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT session_id
                FROM auth_session_state
                WHERE session_id = :session_id
                """,
            ),
            {"session_id": "expired-session-1"},
        )
        row = result.mappings().one_or_none()
    if row is not None:
        raise AssertionError


@pytest.mark.asyncio
async def test_parallel_auth_sessions_are_isolated(
    auth_session_state_runtime: tuple[AuthSessionStateRepository, StorageRuntime],
) -> None:
    """Ensure parallel auth sessions do not overlap."""
    repository, _ = auth_session_state_runtime
    now = int(datetime.now(tz=UTC).timestamp())
    expires_at = now + 600

    _ = await repository.create_session(
        session_id="session-user-1",
        phone_number="+15555550003",
        status="phone_submitted",
        expires_at=expires_at,
    )
    _ = await repository.create_session(
        session_id="session-user-2",
        phone_number="+15555550004",
        status="phone_submitted",
        expires_at=expires_at,
    )

    session_one = await repository.get_session(session_id="session-user-1")
    session_two = await repository.get_session(session_id="session-user-2")

    if session_one.phone_number != "+15555550003":
        raise AssertionError
    if session_two.phone_number != "+15555550004":
        raise AssertionError
