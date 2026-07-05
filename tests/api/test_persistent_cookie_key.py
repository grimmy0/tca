"""Tests for persistent cookie signing key initialization."""

from __future__ import annotations

import base64
import os
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI

from tca.api.app import _resolve_persistent_cookie_signing_key
from tca.config.settings import load_settings
from tca.storage import (
    SettingsRepository,
    create_storage_runtime,
    dispose_storage_runtime,
)
from tca.storage.migrations import run_startup_migrations

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_env_var_cookie_key_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """If TCA_COOKIE_SIGNING_KEY is set, it should override the key on startup."""
    key_bytes = 32
    test_key = os.urandom(key_bytes)
    b64_key = base64.b64encode(test_key).decode("utf-8")
    monkeypatch.setenv("TCA_COOKIE_SIGNING_KEY", b64_key)

    settings = load_settings()
    runtime = create_storage_runtime(settings)
    try:
        app = FastAPI()
        app.state.cookie_signing_key = None
        await _resolve_persistent_cookie_signing_key(app, runtime)
        assert app.state.cookie_signing_key == test_key  # noqa: S101
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_database_cookie_key_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without env var, the key is saved to settings table and reused."""
    key_bytes = 32
    monkeypatch.delenv("TCA_COOKIE_SIGNING_KEY", raising=False)

    db_path = tmp_path / "persistent-cookie.sqlite3"
    monkeypatch.setenv("TCA_DB_PATH", db_path.as_posix())

    # Run migrations to ensure settings table exists
    run_startup_migrations()

    settings = load_settings()
    runtime = create_storage_runtime(settings)
    try:
        # Resolve key for first app instance
        app1 = FastAPI()
        app1.state.cookie_signing_key = None
        await _resolve_persistent_cookie_signing_key(app1, runtime)
        key1 = app1.state.cookie_signing_key
        assert key1 is not None  # noqa: S101
        assert len(key1) == key_bytes  # noqa: S101

        # Verify it was written to database settings
        repo = SettingsRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        )
        record = await repo.get_by_key(key="auth.cookie_signing_key")
        assert record is not None  # noqa: S101
        assert bytes.fromhex(str(record.value)) == key1  # noqa: S101

        # Resolve key for second app instance (should load the same key)
        app2 = FastAPI()
        app2.state.cookie_signing_key = None
        await _resolve_persistent_cookie_signing_key(app2, runtime)
        key2 = app2.state.cookie_signing_key
        assert key2 == key1  # noqa: S101
    finally:
        await dispose_storage_runtime(runtime)
