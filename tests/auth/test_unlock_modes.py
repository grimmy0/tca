"""Tests for startup unlock mode behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tca.auth import (
    SensitiveOperationLockedError,
    StartupUnlockModeError,
    UnlockState,
    get_sensitive_operation_secret,
    initialize_startup_unlock_mode,
    unlock_with_passphrase,
)
from tca.config import AppSettings

if TYPE_CHECKING:
    from pathlib import Path


def test_secure_interactive_mode_requires_explicit_unlock_action_before_sensitive_operations(  # noqa: E501
    tmp_path: Path,
) -> None:
    """Ensure secure-interactive startup keeps sensitive operations locked."""
    unlock_state = UnlockState()
    settings = _build_settings(
        tmp_path=tmp_path,
        mode="secure-interactive",
        secret_file=None,
    )

    initialize_startup_unlock_mode(
        mode=settings.mode,
        secret_file=settings.secret_file,
        unlock_state=unlock_state,
    )

    with pytest.raises(
        SensitiveOperationLockedError,
        match="Sensitive operations are locked",
    ):
        _ = get_sensitive_operation_secret(unlock_state=unlock_state)

    interactive_passphrase = "interactive-passphrase"  # noqa: S105
    unlock_with_passphrase(
        passphrase=interactive_passphrase,
        unlock_state=unlock_state,
    )
    if (
        get_sensitive_operation_secret(unlock_state=unlock_state)
        != interactive_passphrase
    ):
        raise AssertionError


def test_auto_unlock_mode_reads_secret_from_mounted_file(tmp_path: Path) -> None:
    """Ensure auto-unlock mode reads key material from configured secret file."""
    mounted_secret_file = tmp_path / "mounted-unlock.secret"
    _ = mounted_secret_file.write_text("mounted-secret-value\n", encoding="utf-8")
    unlock_state = UnlockState()
    settings = _build_settings(
        tmp_path=tmp_path,
        mode="auto-unlock",
        secret_file=mounted_secret_file,
    )

    initialize_startup_unlock_mode(
        mode=settings.mode,
        secret_file=settings.secret_file,
        unlock_state=unlock_state,
    )

    if not unlock_state.is_unlocked:
        raise AssertionError
    if (
        get_sensitive_operation_secret(unlock_state=unlock_state)
        != "mounted-secret-value"
    ):
        raise AssertionError


def test_auto_unlock_mode_missing_secret_fails_startup_with_actionable_error(
    tmp_path: Path,
) -> None:
    """Ensure missing auto-unlock secret aborts startup with actionable guidance."""
    missing_secret_file = tmp_path / "missing-unlock.secret"
    unlock_state = UnlockState()
    settings = _build_settings(
        tmp_path=tmp_path,
        mode="auto-unlock",
        secret_file=missing_secret_file,
    )

    with pytest.raises(
        StartupUnlockModeError,
        match="TCA_SECRET_FILE",
    ):
        initialize_startup_unlock_mode(
            mode=settings.mode,
            secret_file=settings.secret_file,
            unlock_state=unlock_state,
        )


def _build_settings(
    *,
    tmp_path: Path,
    mode: str,
    secret_file: Path | None,
) -> AppSettings:
    """Build deterministic app settings instance for unlock mode tests."""
    return AppSettings(
        db_path=tmp_path / "unlock-modes.sqlite3",
        bind="127.0.0.1",
        mode=mode,
        log_level="INFO",
        secret_file=secret_file,
        cors_allow_origins=(),
    )
