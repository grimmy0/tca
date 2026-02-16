"""Tests for settings repository CRUD and JSON value fidelity."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.exc import IntegrityError

from tca.config.settings import load_settings
from tca.storage import (
    JSONValue,
    SettingAlreadyExistsError,
    SettingsRepository,
    create_storage_runtime,
    dispose_storage_runtime,
)
from tca.storage.settings_repo import SettingValueDecodeError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

INITIAL_MAX_PAGES = 5
UPDATED_MAX_PAGES = 8
DUPLICATE_INITIAL_HORIZON = 180
DUPLICATE_UPDATED_HORIZON = 240


@pytest.fixture
async def settings_repository(tmp_path: Path) -> AsyncIterator[SettingsRepository]:
    """Create settings repository against isolated SQLite schema fixture."""
    db_path = tmp_path / "settings-repository.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                key VARCHAR(255) NOT NULL,
                value_json TEXT NOT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_settings_key UNIQUE (key)
            )
            """,
        )
    try:
        yield SettingsRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_create_read_and_update_by_key(
    settings_repository: SettingsRepository,
) -> None:
    """Ensure repository supports create/read/update flows with `settings.key`."""
    key = "scheduler.max_pages_per_poll"
    created = await settings_repository.create(key=key, value=INITIAL_MAX_PAGES)
    if created.key != key:
        raise AssertionError
    if created.value != INITIAL_MAX_PAGES:
        raise AssertionError

    loaded = await settings_repository.get_by_key(key=key)
    if loaded is None:
        raise AssertionError
    if loaded.value != INITIAL_MAX_PAGES:
        raise AssertionError

    updated = await settings_repository.update(key=key, value=UPDATED_MAX_PAGES)
    if updated is None:
        raise AssertionError
    if updated.value != UPDATED_MAX_PAGES:
        raise AssertionError

    reread = await settings_repository.get_by_key(key=key)
    if reread is None:
        raise AssertionError
    if reread.value != UPDATED_MAX_PAGES:
        raise AssertionError


@pytest.mark.asyncio
async def test_delete_if_value_matches_is_value_sensitive(
    settings_repository: SettingsRepository,
) -> None:
    """Ensure conditional delete removes row only when expected value matches."""
    key = "scheduler.max_pages_per_poll"
    _ = await settings_repository.create(key=key, value=INITIAL_MAX_PAGES)

    wrong_value_deleted = await settings_repository.delete_if_value_matches(
        key=key,
        value=UPDATED_MAX_PAGES,
    )
    if wrong_value_deleted:
        raise AssertionError
    if await settings_repository.get_by_key(key=key) is None:
        raise AssertionError

    matched_deleted = await settings_repository.delete_if_value_matches(
        key=key,
        value=INITIAL_MAX_PAGES,
    )
    if not matched_deleted:
        raise AssertionError
    if await settings_repository.get_by_key(key=key) is not None:
        raise AssertionError


@pytest.mark.asyncio
async def test_duplicate_key_insert_fails_deterministically(
    settings_repository: SettingsRepository,
) -> None:
    """Ensure duplicate insert emits deterministic repository exception."""
    key = "dedupe.default_horizon_minutes"
    _ = await settings_repository.create(key=key, value=DUPLICATE_INITIAL_HORIZON)

    with pytest.raises(SettingAlreadyExistsError) as exc_info:
        _ = await settings_repository.create(key=key, value=DUPLICATE_UPDATED_HORIZON)

    if str(exc_info.value) != f"Setting already exists for key '{key}'.":
        raise AssertionError


@pytest.mark.asyncio
async def test_create_does_not_mask_non_duplicate_integrity_errors(
    settings_repository: SettingsRepository,
) -> None:
    """Ensure non-duplicate integrity failures remain surfaced to callers."""
    invalid_key = cast("str", cast("object", None))
    with pytest.raises(IntegrityError):
        _ = await settings_repository.create(key=invalid_key, value=1)


@pytest.mark.asyncio
async def test_json_values_preserve_type_fidelity(
    settings_repository: SettingsRepository,
) -> None:
    """Ensure value_json round-trips preserve JSON value types exactly."""
    payload: JSONValue = {
        "enabled": True,
        "max_pages": 5,
        "title_similarity": 0.92,
        "retention_days": None,
        "sources": ["telegram", "rss"],
        "weights": {"title": 1, "body": 2.5},
    }
    key = "scheduler.runtime_config"
    _ = await settings_repository.create(key=key, value=payload)
    stored = await settings_repository.get_by_key(key=key)
    if stored is None:
        raise AssertionError
    if stored.value != payload:
        raise AssertionError

    decoded = cast("dict[str, object]", stored.value)
    if not isinstance(decoded.get("enabled"), bool):
        raise TypeError
    if type(decoded.get("max_pages")) is not int:
        raise AssertionError
    if type(decoded.get("title_similarity")) is not float:
        raise AssertionError
    if decoded.get("retention_days") is not None:
        raise AssertionError
    sources = decoded.get("sources")
    if not isinstance(sources, list):
        raise TypeError
    weights = decoded.get("weights")
    if not isinstance(weights, dict):
        raise TypeError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value_json", "expected_detail"),
    [
        (
            "scheduler.invalid_json_nan",
            "NaN",
            "invalid numeric constant 'NaN'",
        ),
        (
            "scheduler.invalid_json_overflow",
            "1e309",
            "decoded payload contains non-JSON type or non-finite number",
        ),
    ],
)
async def test_get_by_key_rejects_non_finite_json_values(
    settings_repository: SettingsRepository,
    tmp_path: Path,
    key: str,
    value_json: str,
    expected_detail: str,
) -> None:
    """Ensure repository rejects non-standard or non-finite JSON numbers."""
    db_path = tmp_path / "settings-repository.sqlite3"
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO settings (key, value_json)
            VALUES (?, ?)
            """,
            (key, value_json),
        )
        connection.commit()

    with pytest.raises(SettingValueDecodeError) as exc_info:
        _ = await settings_repository.get_by_key(key=key)

    if expected_detail not in str(exc_info.value):
        raise AssertionError
