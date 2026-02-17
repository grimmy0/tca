"""Tests for auth failure notifications."""

from __future__ import annotations

import json
import sqlite3
from http import HTTPStatus
from typing import Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient
from telethon.errors import PhoneNumberBannedError, PhoneNumberInvalidError

from tca.api.app import create_app

BOOTSTRAP_TOKEN = "auth-notifications-token"  # noqa: S105


def test_auth_start_blocked_registration_creates_notification(
    tmp_path: object,
    monkeypatch: object,
    mock_tg_client: object,
) -> None:
    """Ensure registration block creates a high-severity notification."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="auth-notification-blocked.sqlite3",
        output_file_name="auth-notification-blocked-token.txt",
    )
    api_id = 4040
    api_hash = "auth-blocked"
    phone_number = "+15550004444"

    app = create_app()
    app.state.telegram_auth_client_factory = _build_factory(
        mock_tg_client=mock_tg_client,
        expected_api_id=api_id,
        expected_api_hash=api_hash,
    )
    mock_tg_client.responses["send_code_request"] = PhoneNumberBannedError(request=None)

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            "/auth/telegram/start",
            json={
                "api_id": api_id,
                "api_hash": api_hash,
                "phone_number": phone_number,
            },
            headers=_auth_headers(),
        )

    if response.status_code != HTTPStatus.FORBIDDEN:
        raise AssertionError
    payload = response.json()
    if payload.get("detail") != "Telegram registration is blocked. Retry later.":
        raise AssertionError

    notifications = _fetch_notifications(db_path=db_path)
    if len(notifications) != 1:
        raise AssertionError
    row = notifications[0]
    if row["type"] != "auth_registration_blocked":
        raise AssertionError
    if row["severity"] != "high":
        raise AssertionError
    if "retry_after_seconds" not in row["payload"]:
        raise AssertionError
    if "retry_hint" not in row["payload"]:
        raise AssertionError


def test_auth_verify_code_failed_login_creates_notification(
    tmp_path: object,
    monkeypatch: object,
    mock_tg_client: object,
) -> None:
    """Ensure login failures create a retryable notification."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="auth-notification-login-failed.sqlite3",
        output_file_name="auth-notification-login-failed-token.txt",
    )
    api_id = 5050
    api_hash = "auth-login-failed"
    phone_number = "+15550005555"
    mock_tg_client.responses["sign_in"] = PhoneNumberInvalidError(request=None)

    app = create_app()
    app.state.telegram_auth_client_factory = _build_factory(
        mock_tg_client=mock_tg_client,
        expected_api_id=api_id,
        expected_api_hash=api_hash,
    )

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        session_id = _start_auth_session(
            client=client,
            api_id=api_id,
            api_hash=api_hash,
            phone_number=phone_number,
        )
        response = client.post(
            "/auth/telegram/verify-code",
            json={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
                "code": "00000",
            },
            headers=_auth_headers(),
        )

    if response.status_code != HTTPStatus.BAD_REQUEST:
        raise AssertionError
    payload = response.json()
    if payload.get("detail") != "Telegram login failed. Retry after verifying credentials.":
        raise AssertionError

    notifications = _fetch_notifications(db_path=db_path)
    if len(notifications) != 1:
        raise AssertionError
    row = notifications[0]
    if row["type"] != "auth_login_failed":
        raise AssertionError
    if row["severity"] != "medium":
        raise AssertionError
    if "retry_after_seconds" not in row["payload"]:
        raise AssertionError
    if "retry_hint" not in row["payload"]:
        raise AssertionError


def _fetch_notifications(*, db_path: object) -> list[dict[str, object]]:
    """Fetch notification rows from sqlite storage."""
    connection = sqlite3.connect(_as_path(db_path).as_posix())
    connection.row_factory = sqlite3.Row
    with connection:
        cursor = connection.execute(
            "SELECT type, severity, message, payload_json FROM notifications",
        )
        rows = cursor.fetchall()
    notifications: list[dict[str, object]] = []
    for row in rows:
        payload_json = row["payload_json"]
        payload = json.loads(payload_json) if payload_json else {}
        notifications.append(
            {
                "type": row["type"],
                "severity": row["severity"],
                "message": row["message"],
                "payload": payload,
            },
        )
    return notifications


def _start_auth_session(
    *,
    client: TestClient,
    api_id: int,
    api_hash: str,
    phone_number: str,
) -> str:
    """Start Telegram auth to obtain a session id."""
    response = client.post(
        "/auth/telegram/start",
        json={
            "api_id": api_id,
            "api_hash": api_hash,
            "phone_number": phone_number,
        },
        headers=_auth_headers(),
    )
    if response.status_code != HTTPStatus.CREATED:
        raise AssertionError
    payload = response.json()
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise AssertionError
    return session_id


def _build_factory(
    *,
    mock_tg_client: object,
    expected_api_id: int,
    expected_api_hash: str,
) -> object:
    """Build client factory that asserts inputs and returns the mock client."""

    def _factory(api_id: int, api_hash: str) -> object:
        if api_id != expected_api_id:
            raise AssertionError
        if api_hash != expected_api_hash:
            raise AssertionError
        return mock_tg_client

    return _factory


def _configure_auth_env(
    *,
    tmp_path: object,
    monkeypatch: object,
    db_name: str,
    output_file_name: str,
) -> object:
    """Set DB/token-output env vars for authenticated API tests."""
    patcher = _as_monkeypatch(monkeypatch)
    db_path = _as_path(tmp_path) / db_name
    secret_file = _as_path(tmp_path) / "auth-notifications.secret"
    _ = secret_file.write_text("auth-notifications-secret\n", encoding="utf-8")
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / output_file_name).as_posix(),
    )
    patcher.setenv("TCA_MODE", "auto-unlock")
    patcher.setenv("TCA_SECRET_FILE", secret_file.as_posix())
    return db_path


def _auth_headers() -> dict[str, str]:
    """Build deterministic Authorization header for auth API tests."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _as_path(value: object) -> _PathLike:
    """Narrow path-like values to Path for local filesystem operations."""
    if not isinstance(value, _PathLike):
        raise TypeError
    return value


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    """Narrow monkeypatch fixture object to setenv-capable helper."""
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class _PathLike(Protocol):
    """Minimal path-like protocol for temp path fixtures."""

    def __truediv__(self, other: str) -> object:
        """Append a child path segment."""

    def as_posix(self) -> str:
        """Return POSIX string representation."""

    def write_text(self, data: str, encoding: str) -> int:
        """Write text to the filesystem."""


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""
