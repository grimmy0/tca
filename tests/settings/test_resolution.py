"""Tests for runtime config resolution precedence and fallback behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tca.config import (
    DEDUPE_DEFAULT_HORIZON_MINUTES_KEY,
    ConfigResolutionService,
    ConfigValueTypeError,
)
from tca.config.settings import load_settings
from tca.storage import (
    DYNAMIC_SETTINGS_DEFAULTS,
    ChannelGroupsRepository,
    SettingsRepository,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

GLOBAL_HORIZON_MINUTES = 180
GROUP_OVERRIDE_HORIZON_MINUTES = 45
GLOBAL_HORIZON_WITH_OVERRIDE_PRESENT = 240
MISSING_GROUP_ID = 99999
DEFAULTS_BY_KEY: dict[str, object] = dict(DYNAMIC_SETTINGS_DEFAULTS)


@pytest.fixture
async def resolution_dependencies(
    tmp_path: Path,
) -> AsyncIterator[
    tuple[ConfigResolutionService, SettingsRepository, ChannelGroupsRepository]
]:
    """Provide config-resolution service and backing repositories."""
    db_path = tmp_path / "config-resolution.sqlite3"

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
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS channel_groups (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT NULL,
                dedupe_horizon_minutes_override INTEGER NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )

    settings_repository = SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    groups_repository = ChannelGroupsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    service = ConfigResolutionService(
        app_settings=settings,
        settings_lookup=settings_repository,
        channel_groups_lookup=groups_repository,
    )
    try:
        yield service, settings_repository, groups_repository
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_global_horizon_comes_from_dynamic_settings_key(
    resolution_dependencies: tuple[
        ConfigResolutionService,
        SettingsRepository,
        ChannelGroupsRepository,
    ],
) -> None:
    """Ensure global horizon resolves from `settings` key value."""
    service, settings_repository, _ = resolution_dependencies
    _ = await settings_repository.create(
        key=DEDUPE_DEFAULT_HORIZON_MINUTES_KEY,
        value=GLOBAL_HORIZON_MINUTES,
    )

    resolved = await service.resolve_global_dedupe_horizon_minutes()

    if resolved != GLOBAL_HORIZON_MINUTES:
        raise AssertionError


@pytest.mark.asyncio
async def test_static_settings_are_exposed_for_runtime_resolution_contract(
    resolution_dependencies: tuple[
        ConfigResolutionService,
        SettingsRepository,
        ChannelGroupsRepository,
    ],
) -> None:
    """Ensure service exposes static environment settings consistently."""
    service, _, _ = resolution_dependencies
    static_settings = service.static_settings

    if static_settings.db_path.name != "config-resolution.sqlite3":
        raise AssertionError
    if service.static_settings is not static_settings:
        raise AssertionError


@pytest.mark.asyncio
async def test_group_override_wins_over_global_horizon(
    resolution_dependencies: tuple[
        ConfigResolutionService,
        SettingsRepository,
        ChannelGroupsRepository,
    ],
) -> None:
    """Ensure per-group horizon override takes precedence over global setting."""
    service, settings_repository, groups_repository = resolution_dependencies
    _ = await settings_repository.create(
        key=DEDUPE_DEFAULT_HORIZON_MINUTES_KEY,
        value=GLOBAL_HORIZON_WITH_OVERRIDE_PRESENT,
    )
    group = await groups_repository.create_group(
        name="Priority",
        description=None,
        dedupe_horizon_minutes_override=GROUP_OVERRIDE_HORIZON_MINUTES,
    )

    resolved = await service.resolve_effective_dedupe_horizon_minutes(group_id=group.id)

    if resolved != GROUP_OVERRIDE_HORIZON_MINUTES:
        raise AssertionError


@pytest.mark.asyncio
async def test_missing_group_id_falls_back_to_global_horizon(
    resolution_dependencies: tuple[
        ConfigResolutionService,
        SettingsRepository,
        ChannelGroupsRepository,
    ],
) -> None:
    """Ensure unknown group id resolves through global horizon value."""
    service, settings_repository, _ = resolution_dependencies
    _ = await settings_repository.create(
        key=DEDUPE_DEFAULT_HORIZON_MINUTES_KEY,
        value=GLOBAL_HORIZON_MINUTES,
    )

    resolved = await service.resolve_effective_dedupe_horizon_minutes(
        group_id=MISSING_GROUP_ID,
    )

    if resolved != GLOBAL_HORIZON_MINUTES:
        raise AssertionError


@pytest.mark.asyncio
async def test_missing_horizon_setting_falls_back_to_seeded_default(
    resolution_dependencies: tuple[
        ConfigResolutionService,
        SettingsRepository,
        ChannelGroupsRepository,
    ],
) -> None:
    """Ensure missing global setting key resolves to seeded default horizon."""
    service, _, _ = resolution_dependencies
    expected_default = DEFAULTS_BY_KEY[DEDUPE_DEFAULT_HORIZON_MINUTES_KEY]
    if type(expected_default) is not int:
        raise TypeError

    resolved = await service.resolve_global_dedupe_horizon_minutes()

    if resolved != expected_default:
        raise AssertionError


@pytest.mark.asyncio
async def test_global_horizon_rejects_non_integer_dynamic_setting_values(
    resolution_dependencies: tuple[
        ConfigResolutionService,
        SettingsRepository,
        ChannelGroupsRepository,
    ],
) -> None:
    """Ensure non-integer global horizon values fail deterministically."""
    service, settings_repository, _ = resolution_dependencies
    _ = await settings_repository.create(
        key=DEDUPE_DEFAULT_HORIZON_MINUTES_KEY,
        value=True,
    )

    with pytest.raises(ConfigValueTypeError) as exc_info:
        _ = await service.resolve_global_dedupe_horizon_minutes()

    if "expected int, got bool." not in str(exc_info.value):
        raise AssertionError
