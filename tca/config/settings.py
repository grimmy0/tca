"""Typed application settings loaded from static environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

Mode = str
LogLevel = str

ENV_DB_PATH = "TCA_DB_PATH"
ENV_BIND = "TCA_BIND"
ENV_MODE = "TCA_MODE"
ENV_LOG_LEVEL = "TCA_LOG_LEVEL"
ENV_SECRET_FILE = "TCA_SECRET_FILE"  # noqa: S105

DEFAULT_DB_PATH = Path("/data/tca.db")
DEFAULT_BIND = "127.0.0.1"
DEFAULT_MODE: Mode = "secure-interactive"
DEFAULT_LOG_LEVEL: LogLevel = "INFO"
DEFAULT_SECRET_FILE: Path | None = None

VALID_MODES: frozenset[Mode] = frozenset({"secure-interactive", "auto-unlock"})
VALID_LOG_LEVELS: frozenset[LogLevel] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
)


class SettingsValidationError(ValueError):
    """Raised when static settings env vars contain invalid values."""

    @classmethod
    def for_empty_value(cls, env_var: str) -> SettingsValidationError:
        """Build error for empty non-optional env var values."""
        message = f"Invalid {env_var}: value cannot be empty."
        return cls(message)

    @classmethod
    def for_invalid_choice(
        cls,
        env_var: str,
        value: str,
        allowed_values: str,
    ) -> SettingsValidationError:
        """Build error for enum-like env vars with fixed allowlists."""
        message = f"Invalid {env_var}: {value!r}. Allowed values: {allowed_values}."
        return cls(message)


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Resolved static configuration values for process startup."""

    db_path: Path
    bind: str
    mode: Mode
    log_level: LogLevel
    secret_file: Path | None


def load_settings(environ: Mapping[str, str] | None = None) -> AppSettings:
    """Load and validate static settings from process environment."""
    env = os.environ if environ is None else environ

    db_path = _read_db_path(env)
    bind = _read_bind(env)
    mode = _read_mode(env)
    log_level = _read_log_level(env)
    secret_file = _read_secret_file(env)

    return AppSettings(
        db_path=db_path,
        bind=bind,
        mode=mode,
        log_level=log_level,
        secret_file=secret_file,
    )


def _read_db_path(environ: Mapping[str, str]) -> Path:
    raw = environ.get(ENV_DB_PATH)
    if raw is None:
        return DEFAULT_DB_PATH
    value = raw.strip()
    if not value:
        raise SettingsValidationError.for_empty_value(ENV_DB_PATH)
    return Path(value).expanduser()


def _read_bind(environ: Mapping[str, str]) -> str:
    raw = environ.get(ENV_BIND)
    if raw is None:
        return DEFAULT_BIND
    value = raw.strip()
    if not value:
        raise SettingsValidationError.for_empty_value(ENV_BIND)
    return value


def _read_mode(environ: Mapping[str, str]) -> Mode:
    raw = environ.get(ENV_MODE)
    if raw is None:
        return DEFAULT_MODE
    value = raw.strip()
    if value in VALID_MODES:
        return value
    allowed = ", ".join(sorted(VALID_MODES))
    raise SettingsValidationError.for_invalid_choice(ENV_MODE, value, allowed)


def _read_log_level(environ: Mapping[str, str]) -> LogLevel:
    raw = environ.get(ENV_LOG_LEVEL)
    if raw is None:
        return DEFAULT_LOG_LEVEL
    value = raw.strip().upper()
    if value in VALID_LOG_LEVELS:
        return value
    allowed = ", ".join(sorted(VALID_LOG_LEVELS))
    raise SettingsValidationError.for_invalid_choice(ENV_LOG_LEVEL, raw, allowed)


def _read_secret_file(environ: Mapping[str, str]) -> Path | None:
    raw = environ.get(ENV_SECRET_FILE)
    if raw is None:
        return DEFAULT_SECRET_FILE
    value = raw.strip()
    if not value:
        return DEFAULT_SECRET_FILE
    return Path(value).expanduser()
