"""Tests for static environment settings loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from tca.config import SettingsValidationError, load_settings


def test_load_settings_uses_design_defaults_when_env_absent() -> None:
    """Ensure static settings resolve to default values when env vars are missing."""
    settings = load_settings({})

    if settings.db_path != Path("/data/tca.db"):
        raise AssertionError
    if settings.bind != "127.0.0.1":
        raise AssertionError
    if settings.mode != "secure-interactive":
        raise AssertionError
    if settings.log_level != "INFO":
        raise AssertionError
    if settings.secret_file is not None:
        raise AssertionError


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"TCA_MODE": "invalid"},
            (
                "Invalid TCA_MODE: 'invalid'. "
                "Allowed values: auto-unlock, secure-interactive."
            ),
        ),
        (
            {"TCA_LOG_LEVEL": "verbose"},
            (
                "Invalid TCA_LOG_LEVEL: 'verbose'. "
                "Allowed values: CRITICAL, DEBUG, ERROR, INFO, WARNING."
            ),
        ),
    ],
)
def test_load_settings_rejects_invalid_mode_and_log_level(
    env: dict[str, str],
    message: str,
) -> None:
    """Ensure invalid mode/log level fail with deterministic validation text."""
    with pytest.raises(SettingsValidationError, match=message):
        _ = load_settings(env)
