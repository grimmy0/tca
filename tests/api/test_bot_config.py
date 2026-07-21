"""Tests for Telegram Bot Config API endpoints (C097)."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tca.api.app import create_app
from tca.bot import BotInfo, BotTokenInvalidError, SentMessage

if TYPE_CHECKING:
    from pathlib import Path

BOOTSTRAP_TOKEN = "bot-config-api-token"  # noqa: S105


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    """Narrow monkeypatch fixture object to setenv-capable helper."""
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


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
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / output_file_name).as_posix(),
    )
    return db_path


def _auth_headers() -> dict[str, str]:
    """Build deterministic Authorization header for API tests."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


@pytest.mark.asyncio
async def test_bot_config_lifecycle_endpoints(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure POST/GET/DELETE bot configuration operations work end-to-end."""
    _ = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="bot-config-lifecycle.sqlite3",
        output_file_name="bot-config-lifecycle-bootstrap-token.txt",
    )

    app = create_app()
    auth_headers = _auth_headers()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        # 1. GET config before setup -> should return null fields
        get_res = client.get("/bot/config", headers=auth_headers)
        assert get_res.status_code == HTTPStatus.OK
        get_data = get_res.json()
        assert get_data["token_masked"] is None
        assert get_data["chat_id"] is None
        assert get_data["enabled"] is False

        # 2. POST /bot/test when not configured -> should return 422
        test_res = client.post("/bot/test", headers=auth_headers)
        assert test_res.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

        # 3. POST /bot/config with invalid token -> should return 422
        with patch("tca.bot.client.BotApiClient.validate_token") as mock_validate:
            mock_validate.side_effect = BotTokenInvalidError("Invalid token")
            post_res = client.post(
                "/bot/config",
                json={"token": "123:invalid", "chat_id": "@mychat"},
                headers=auth_headers,
            )
            assert post_res.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

        # 4. POST /bot/config with valid token -> should succeed and set enabled=True
        with patch("tca.bot.client.BotApiClient.validate_token") as mock_validate:
            mock_validate.return_value = BotInfo(bot_id=123, username="my_tca_bot")
            post_res = client.post(
                "/bot/config",
                json={"token": "123:ABCDEFGH", "chat_id": "@mychat"},
                headers=auth_headers,
            )
            assert post_res.status_code == HTTPStatus.OK
            post_data = post_res.json()
            assert post_data["bot_username"] == "my_tca_bot"
            assert post_data["chat_id"] == "@mychat"

        # 5. GET config after setup -> should return masked token (last 4 visible)
        get_res = client.get("/bot/config", headers=auth_headers)
        assert get_res.status_code == HTTPStatus.OK
        get_data = get_res.json()
        assert get_data["token_masked"] == "********EFGH"
        assert get_data["chat_id"] == "@mychat"
        assert get_data["enabled"] is True

        # 6. POST /bot/test when configured -> should deliver successfully
        with patch("tca.bot.client.BotApiClient.send_message") as mock_send:
            mock_send.return_value = SentMessage(message_id=999)
            test_res = client.post("/bot/test", headers=auth_headers)
            assert test_res.status_code == HTTPStatus.OK
            test_data = test_res.json()
            assert test_data["message_id"] == 999
            mock_send.assert_called_once_with(
                token="123:ABCDEFGH",
                chat_id="@mychat",
                text="TCA bot delivery test — connection verified.",
            )

        # 7. DELETE /bot/config -> should clear config
        del_res = client.delete("/bot/config", headers=auth_headers)
        assert del_res.status_code == HTTPStatus.NO_CONTENT

        # 8. GET config after deletion -> should be null/disabled
        get_res = client.get("/bot/config", headers=auth_headers)
        assert get_res.status_code == HTTPStatus.OK
        get_data = get_res.json()
        assert get_data["token_masked"] is None
        assert get_data["chat_id"] is None
        assert get_data["enabled"] is False
