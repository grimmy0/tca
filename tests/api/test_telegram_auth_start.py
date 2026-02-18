"""Tests for Telegram auth start using mocks."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from telethon.errors import ApiIdInvalidError

from tca.api.app import create_app
from tca.auth import request_login_code

if TYPE_CHECKING:
    from pathlib import Path

    from tests.mocks.mock_telegram_client import MockTelegramClient

BOOTSTRAP_TOKEN = "telegram-auth-token"  # noqa: S105


@pytest.mark.asyncio
async def test_auth_start_mock(mock_tg_client: MockTelegramClient) -> None:
    """Verify auth flow uses injected client and supports falsy scripted responses."""
    phone = "+1234567890"
    mock_tg_client.responses["send_code_request"] = False

    result = await request_login_code(mock_tg_client, phone)

    if result is not False:
        raise AssertionError
    if mock_tg_client.call_counts.get("send_code_request") != 1:
        raise AssertionError


def test_auth_start_returns_session_id(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure auth start returns a session id on valid payload."""
    _ = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-start.sqlite3",
        output_file_name="telegram-auth-start-token.txt",
    )
    api_id = 424242
    api_hash = "hash-for-auth-start"
    phone_number = "+15550001111"

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
    if mock_tg_client.call_counts.get("send_code_request") != 1:
        raise AssertionError


def test_auth_start_invalid_api_credentials_return_controlled_error(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure invalid API credentials produce deterministic error response."""
    _ = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-invalid-creds.sqlite3",
        output_file_name="telegram-auth-invalid-creds-token.txt",
    )
    api_id = 123
    api_hash = "bad-hash"
    phone_number = "+15550002222"
    mock_tg_client.responses["send_code_request"] = ApiIdInvalidError(request=None)

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
        response = client.post(
            "/auth/telegram/start",
            json={
                "api_id": api_id,
                "api_hash": api_hash,
                "phone_number": phone_number,
            },
            headers=_auth_headers(),
        )

    if response.status_code != HTTPStatus.BAD_REQUEST:
        raise AssertionError
    payload = response.json()
    if payload.get("detail") != "Invalid Telegram API credentials.":
        raise AssertionError


def test_auth_start_does_not_log_secrets(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ensure auth start does not log secrets from payload."""
    _ = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-no-secrets.sqlite3",
        output_file_name="telegram-auth-no-secrets-token.txt",
    )
    api_id = 99999
    api_hash = "super-secret-hash"
    phone_number = "+15550003333"

    app = create_app()
    app.state.telegram_auth_client_factory = _build_factory(
        mock_tg_client=mock_tg_client,
        expected_api_id=api_id,
        expected_api_hash=api_hash,
    )

    caplog.set_level(logging.INFO)
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        caplog.clear()
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
    if api_hash in caplog.text:
        raise AssertionError
    if phone_number in caplog.text:
        raise AssertionError


def _build_factory(
    *,
    mock_tg_client: MockTelegramClient,
    expected_api_id: int,
    expected_api_hash: str,
) -> object:
    """Build client factory that asserts inputs and returns the mock client."""

    def _factory(api_id: int, api_hash: str) -> MockTelegramClient:
        if api_id != expected_api_id:
            raise AssertionError
        if api_hash != expected_api_hash:
            raise AssertionError
        return mock_tg_client

    return _factory


def _configure_auth_env(
    *,
    tmp_path: Path,
    monkeypatch: object,
    db_name: str,
    output_file_name: str,
) -> Path:
    """Set DB/token-output env vars for authenticated API tests."""
    patcher = _as_monkeypatch(monkeypatch)
    db_path = tmp_path / db_name
    secret_file = tmp_path / "auth-unlock.secret"
    _ = secret_file.write_text("auth-start-secret\n", encoding="utf-8")
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / output_file_name).as_posix(),
    )
    patcher.setenv("TCA_MODE", "auto-unlock")
    patcher.setenv("TCA_SECRET_FILE", secret_file.as_posix())
    return db_path


def _auth_headers() -> dict[str, str]:
    """Build deterministic Authorization header for auth API tests."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


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
