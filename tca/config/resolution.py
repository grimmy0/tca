"""Runtime configuration resolution across static and dynamic sources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tca.storage.settings_seed import DYNAMIC_SETTINGS_DEFAULTS

if TYPE_CHECKING:
    from tca.config.settings import AppSettings

DEDUPE_DEFAULT_HORIZON_MINUTES_KEY = "dedupe.default_horizon_minutes"

_DYNAMIC_DEFAULTS_BY_KEY: dict[str, object] = dict(DYNAMIC_SETTINGS_DEFAULTS)


class SettingRecordLike(Protocol):
    """Minimal setting row payload needed for runtime config resolution."""

    @property
    def key(self) -> str:
        """Return dynamic settings key."""
        ...

    @property
    def value(self) -> object:
        """Return dynamic settings JSON-compatible value."""
        ...


class ChannelGroupRecordLike(Protocol):
    """Minimal channel-group payload needed for horizon resolution."""

    @property
    def id(self) -> int:
        """Return channel-group row id."""
        ...

    @property
    def dedupe_horizon_minutes_override(self) -> int | None:
        """Return optional dedupe horizon override value."""
        ...


@runtime_checkable
class SettingsLookup(Protocol):
    """Read contract for retrieving one dynamic setting row by key."""

    async def get_by_key(self, *, key: str) -> SettingRecordLike | None:
        """Return setting row for key or None when absent."""
        ...


@runtime_checkable
class ChannelGroupsLookup(Protocol):
    """Read contract for retrieving channel-group overrides by id."""

    async def get_group_by_id(self, *, group_id: int) -> ChannelGroupRecordLike | None:
        """Return channel-group row by id or None when absent."""
        ...


class ConfigResolutionError(RuntimeError):
    """Base exception for runtime config resolution errors."""


class ConfigValueTypeError(ConfigResolutionError):
    """Raised when a dynamic setting has an unexpected runtime value type."""

    @classmethod
    def for_key(cls, *, key: str, expected: str, actual: str) -> ConfigValueTypeError:
        """Build deterministic type mismatch error with key-localized context."""
        message = (
            f"Resolved dynamic setting '{key}' has invalid type: "
            f"expected {expected}, got {actual}."
        )
        return cls(message)


class MissingSeedDefaultError(ConfigResolutionError):
    """Raised when seeded defaults do not include a required resolution key."""

    @classmethod
    def for_key(cls, *, key: str) -> MissingSeedDefaultError:
        """Build deterministic missing-default error for resolution fallback."""
        message = f"Missing seeded dynamic default for setting key '{key}'."
        return cls(message)


class ConfigResolutionService:
    """Resolve effective runtime config from static env and dynamic settings."""

    _app_settings: AppSettings
    _settings_lookup: SettingsLookup
    _channel_groups_lookup: ChannelGroupsLookup

    def __init__(
        self,
        *,
        app_settings: AppSettings,
        settings_lookup: SettingsLookup,
        channel_groups_lookup: ChannelGroupsLookup,
    ) -> None:
        """Create service with explicit static and dynamic config dependencies."""
        self._app_settings = app_settings
        self._settings_lookup = settings_lookup
        self._channel_groups_lookup = channel_groups_lookup

    @property
    def static_settings(self) -> AppSettings:
        """Expose immutable static env-derived process settings."""
        return self._app_settings

    async def resolve_global_dedupe_horizon_minutes(self) -> int:
        """Resolve global dedupe horizon from dynamic settings or seeded default."""
        record = await self._settings_lookup.get_by_key(
            key=DEDUPE_DEFAULT_HORIZON_MINUTES_KEY,
        )
        if record is None:
            return _resolve_seeded_default_horizon_minutes()

        value = record.value
        if type(value) is not int:
            raise ConfigValueTypeError.for_key(
                key=DEDUPE_DEFAULT_HORIZON_MINUTES_KEY,
                expected="int",
                actual=type(value).__name__,
            )
        return value

    async def resolve_effective_dedupe_horizon_minutes(
        self,
        *,
        group_id: int | None = None,
    ) -> int:
        """Resolve effective dedupe horizon with group override precedence."""
        if group_id is not None:
            group = await self._channel_groups_lookup.get_group_by_id(group_id=group_id)
            if group is not None and group.dedupe_horizon_minutes_override is not None:
                return group.dedupe_horizon_minutes_override
        return await self.resolve_global_dedupe_horizon_minutes()


def _resolve_seeded_default_horizon_minutes() -> int:
    """Return seeded default horizon for missing dynamic settings row."""
    default_value = _DYNAMIC_DEFAULTS_BY_KEY.get(DEDUPE_DEFAULT_HORIZON_MINUTES_KEY)
    if type(default_value) is not int:
        raise MissingSeedDefaultError.for_key(key=DEDUPE_DEFAULT_HORIZON_MINUTES_KEY)
    return default_value
