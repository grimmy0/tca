"""Bootstrap dynamic settings defaults into `settings` on startup."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tca.config.settings import load_settings

from .db import StorageRuntime, create_storage_runtime, dispose_storage_runtime
from .settings_repo import JSONValue, SettingAlreadyExistsError, SettingsRepository

logger = logging.getLogger(__name__)

DYNAMIC_SETTINGS_DEFAULTS: tuple[tuple[str, JSONValue], ...] = (
    ("dedupe.default_horizon_minutes", 1440),
    ("dedupe.threshold.title_similarity", 0.92),
    ("scheduler.default_poll_interval_seconds", 300),
    ("scheduler.max_pages_per_poll", 5),
    ("scheduler.max_messages_per_poll", 500),
    ("retention.raw_messages_days", 30),
    ("retention.items_days", 365),
    ("retention.ingest_errors_days", 90),
    ("retention.dedupe_decisions_days", 180),
    ("backup.retain_count", 14),
)


async def seed_default_dynamic_settings(*, repository: SettingsRepository) -> None:
    """Insert design defaults for settings keys that are currently absent."""
    inserted_count = 0
    for key, default_value in DYNAMIC_SETTINGS_DEFAULTS:
        try:
            _ = await repository.create(key=key, value=default_value)
        except SettingAlreadyExistsError:
            continue
        inserted_count += 1

    logger.info(
        "Seeded dynamic setting defaults (inserted=%d, total_keys=%d)",
        inserted_count,
        len(DYNAMIC_SETTINGS_DEFAULTS),
    )


@dataclass(slots=True)
class SettingsSeedDependency:
    """Lifecycle dependency that ensures dynamic settings defaults at startup."""

    _runtime: StorageRuntime | None = None

    async def startup(self) -> None:
        """Seed missing dynamic settings before app starts serving traffic."""
        settings = load_settings()
        runtime = create_storage_runtime(settings)
        repository = SettingsRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        )
        try:
            await seed_default_dynamic_settings(repository=repository)
        except Exception:
            await dispose_storage_runtime(runtime)
            raise
        self._runtime = runtime

    async def shutdown(self) -> None:
        """Dispose startup seeding runtime resources."""
        runtime = self._runtime
        if runtime is None:
            return
        self._runtime = None
        await dispose_storage_runtime(runtime)
