"""Tests for bootstrap bearer token generation and persistence."""

from __future__ import annotations

import json
import sqlite3
import stat
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tca.api.app import create_app
from tca.auth import BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY, compute_token_sha256_digest

if TYPE_CHECKING:
    from pathlib import Path

OWNER_ONLY_FILE_MODE = 0o600


def test_bootstrap_token_plain_value_is_never_persisted_to_db(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure settings persistence stores only token digest and never plaintext."""
    db_path, _ = _configure_bootstrap_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    bootstrap_value = "plain-bootstrap-value"

    with patch(
        "tca.auth.bootstrap_token.secrets.token_urlsafe",
        return_value=bootstrap_value,
    ):
        _start_app_once()

    stored_digest = _read_stored_digest(db_path=db_path)
    if stored_digest != compute_token_sha256_digest(token=bootstrap_value):
        raise AssertionError

    with sqlite3.connect(db_path.as_posix()) as connection:
        rows = cast(
            "list[tuple[object]]",
            connection.execute("SELECT value_json FROM settings").fetchall(),
        )

    for row in rows:
        if len(row) != 1:
            raise AssertionError
        value_json_obj = row[0]
        if not isinstance(value_json_obj, str):
            raise TypeError
        if bootstrap_value in value_json_obj:
            raise AssertionError


def test_bootstrap_token_is_written_once_to_configured_output_path(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure first-run bootstrap writes one token line to output path."""
    _, output_path = _configure_bootstrap_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    bootstrap_value = "plain-bootstrap-value"

    with patch(
        "tca.auth.bootstrap_token.secrets.token_urlsafe",
        return_value=bootstrap_value,
    ):
        _start_app_once()

    if not output_path.exists():
        raise AssertionError
    if output_path.read_text(encoding="utf-8") != f"{bootstrap_value}\n":
        raise AssertionError


def test_restart_does_not_rotate_bootstrap_token_automatically(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure startup keeps existing digest and does not regenerate token."""
    db_path, output_path = _configure_bootstrap_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    first_value = "first-bootstrap-value"
    second_value = "second-bootstrap-value"

    with patch(
        "tca.auth.bootstrap_token.secrets.token_urlsafe",
        return_value=first_value,
    ):
        _start_app_once()

    with patch(
        "tca.auth.bootstrap_token.secrets.token_urlsafe",
        return_value=second_value,
    ) as token_generator:
        _start_app_once()

    if token_generator.call_count != 0:
        raise AssertionError

    stored_digest = _read_stored_digest(db_path=db_path)
    if stored_digest != compute_token_sha256_digest(token=first_value):
        raise AssertionError
    if output_path.read_text(encoding="utf-8") != f"{first_value}\n":
        raise AssertionError


def test_bootstrap_token_output_file_is_owner_only(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure bootstrap token output file permissions are set to 0600."""
    _, output_path = _configure_bootstrap_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    with patch(
        "tca.auth.bootstrap_token.secrets.token_urlsafe",
        return_value="plain-bootstrap-value",
    ):
        _start_app_once()

    file_mode = stat.S_IMODE(output_path.stat().st_mode)
    if file_mode != OWNER_ONLY_FILE_MODE:
        raise AssertionError


def test_bootstrap_digest_is_rolled_back_when_output_write_fails(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure write failures do not leave unrecoverable digest-only state."""
    db_path, output_path = _configure_bootstrap_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    first_value = "first-bootstrap-value"
    second_value = "second-bootstrap-value"

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=first_value,
        ),
        patch(
            "tca.auth.bootstrap_token._write_bootstrap_token",
            side_effect=OSError("forced-output-write-failure"),
        ),
        pytest.raises(OSError, match="forced-output-write-failure"),
    ):
        _start_app_once()

    if _bootstrap_digest_exists(db_path=db_path):
        raise AssertionError
    if output_path.exists():
        raise AssertionError

    with patch(
        "tca.auth.bootstrap_token.secrets.token_urlsafe",
        return_value=second_value,
    ):
        _start_app_once()

    stored_digest = _read_stored_digest(db_path=db_path)
    if stored_digest != compute_token_sha256_digest(token=second_value):
        raise AssertionError
    if output_path.read_text(encoding="utf-8") != f"{second_value}\n":
        raise AssertionError


def _configure_bootstrap_env(
    *,
    tmp_path: Path,
    monkeypatch: object,
) -> tuple[Path, Path]:
    """Configure DB and bootstrap token output path for startup lifecycle tests."""
    db_path = tmp_path / "bootstrap-token.sqlite3"
    output_path = tmp_path / "bootstrap-token.txt"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv("TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH", output_path.as_posix())
    return db_path, output_path


def _start_app_once() -> None:
    """Run one app lifecycle and assert health endpoint remains reachable."""
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        if response.status_code != HTTPStatus.OK:
            raise AssertionError


def _read_stored_digest(*, db_path: Path) -> str:
    """Read and decode persisted bootstrap digest from settings row."""
    with sqlite3.connect(db_path.as_posix()) as connection:
        row = cast(
            "tuple[object] | None",
            connection.execute(
                """
                SELECT value_json
                FROM settings
                WHERE key = ?
                """,
                (BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY,),
            ).fetchone(),
        )

    if row is None:
        raise AssertionError
    if len(row) != 1:
        raise AssertionError
    value_json_obj = row[0]
    if not isinstance(value_json_obj, str):
        raise TypeError
    decoded_obj = cast("object", json.loads(value_json_obj))
    if not isinstance(decoded_obj, str):
        raise TypeError
    return decoded_obj


def _bootstrap_digest_exists(*, db_path: Path) -> bool:
    """Return True when bootstrap token digest row exists in settings table."""
    with sqlite3.connect(db_path.as_posix()) as connection:
        row = cast(
            "tuple[object] | None",
            connection.execute(
                """
                SELECT value_json
                FROM settings
                WHERE key = ?
                """,
                (BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY,),
            ).fetchone(),
        )
    return row is not None


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    """Narrow monkeypatch fixture object to setenv-capable helper."""
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""
