"""Configuration module for TCA."""

from .resolution import (
    DEDUPE_DEFAULT_HORIZON_MINUTES_KEY,
    ConfigResolutionService,
    ConfigValueTypeError,
)
from .settings import AppSettings, SettingsValidationError, load_settings

__all__ = [
    "DEDUPE_DEFAULT_HORIZON_MINUTES_KEY",
    "AppSettings",
    "ConfigResolutionService",
    "ConfigValueTypeError",
    "SettingsValidationError",
    "load_settings",
]
