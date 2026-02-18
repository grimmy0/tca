"""Tests for channels API create/list/update behavior."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

BOOTSTRAP_TOKEN = "channels-crud-token"  # noqa: S105
DEFAULT_ACCOUNT_ID = 1
DEFAULT_CHANNEL_ID = 1
DEFAULT_TELEGRAM_CHANNEL_ID = 11001
EXPECTED_CREATED_STATUS = HTTPStatus.CREATED
EXPECTED_OK_STATUS = HTTPStatus.OK
EXPECTED_UNPROCESSABLE_ENTITY = HTTPStatus.UNPROCESSABLE_ENTITY


def test_channel_create_validates_required_telegram_identifiers(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure create rejects invalid Telegram identifier payloads."""
    db_path = _as_path(tmp_path) / "channels-api-create.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channels-bootstrap-token.txt").as_posix(),
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
        response = client.post(
            "/channels",
            json={
                "account_id": DEFAULT_ACCOUNT_ID,
                "telegram_channel_id": 0,
                "name": "alpha",
            },
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_UNPROCESSABLE_ENTITY:
        raise AssertionError


def test_list_channels_returns_only_enabled_rows(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure list endpoint filters disabled channels."""
    db_path = _as_path(tmp_path) / "channels-api-list.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channels-list-bootstrap-token.txt").as_posix(),
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
        _insert_account_fixture(db_path, account_id=DEFAULT_ACCOUNT_ID)
        _insert_channel_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=DEFAULT_TELEGRAM_CHANNEL_ID,
            name="alpha",
            is_enabled=True,
        )
        _insert_channel_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID + 1,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=DEFAULT_TELEGRAM_CHANNEL_ID + 1,
            name="beta",
            is_enabled=False,
        )
        response = client.get("/channels", headers=auth_headers)

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    payload = cast("list[dict[str, object]]", response.json())
    returned_ids = [item.get("id") for item in payload]
    if returned_ids != [DEFAULT_CHANNEL_ID]:
        raise AssertionError


def test_patch_channel_rejects_empty_username(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure PATCH rejects empty username payloads."""
    db_path = _as_path(tmp_path) / "channels-api-patch-username.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channels-username-bootstrap-token.txt").as_posix(),
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
        _insert_account_fixture(db_path, account_id=DEFAULT_ACCOUNT_ID)
        _insert_channel_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=DEFAULT_TELEGRAM_CHANNEL_ID,
            name="alpha",
            is_enabled=True,
        )
        response = client.patch(
            f"/channels/{DEFAULT_CHANNEL_ID}",
            json={"username": ""},
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_UNPROCESSABLE_ENTITY:
        raise AssertionError


def test_patch_channel_persists_polling_state_updates(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure polling state fields persist through PATCH."""
    db_path = _as_path(tmp_path) / "channels-api-patch.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channels-patch-bootstrap-token.txt").as_posix(),
    )

    paused_until = datetime(2026, 2, 16, 10, 30, tzinfo=UTC)
    last_success_at = datetime(2026, 2, 16, 9, 15, tzinfo=UTC)

    app = create_app()
    auth_headers = _auth_headers()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account_fixture(db_path, account_id=DEFAULT_ACCOUNT_ID)
        _insert_channel_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=DEFAULT_TELEGRAM_CHANNEL_ID,
            name="alpha",
            is_enabled=True,
        )
        response = client.patch(
            f"/channels/{DEFAULT_CHANNEL_ID}",
            json={
                "is_enabled": False,
                "paused_until": paused_until.isoformat(),
                "last_success_at": last_success_at.isoformat(),
            },
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError
    if _read_channel_enabled(db_path, channel_id=DEFAULT_CHANNEL_ID) is not False:
        raise AssertionError

    state = _read_channel_state(db_path, channel_id=DEFAULT_CHANNEL_ID)
    if state is None:
        raise AssertionError
    paused_until_value, last_success_value = state
    if paused_until_value is None or last_success_value is None:
        raise AssertionError


def _insert_account_fixture(db_path: object, *, account_id: int) -> None:
    """Insert a Telegram account row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
            VALUES (?, ?, ?)
            """,
            (account_id, 12345, b"encrypted-api-hash"),
        )
        connection.commit()


def _insert_channel_fixture(  # noqa: PLR0913
    db_path: object,
    *,
    channel_id: int,
    account_id: int,
    telegram_channel_id: int,
    name: str,
    is_enabled: bool,
) -> None:
    """Insert a Telegram channel row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO telegram_channels (
                id,
                account_id,
                telegram_channel_id,
                name,
                is_enabled
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (channel_id, account_id, telegram_channel_id, name, int(is_enabled)),
        )
        connection.commit()


def _read_channel_enabled(db_path: object, *, channel_id: int) -> bool | None:
    """Read enabled state for a channel."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        cursor = connection.execute(
            "SELECT is_enabled FROM telegram_channels WHERE id = ?",
            (channel_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return bool(row[0])


def _read_channel_state(
    db_path: object,
    *,
    channel_id: int,
) -> tuple[str | None, str | None] | None:
    """Read paused/last-success values for a channel."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        cursor = connection.execute(
            """
            SELECT paused_until, last_success_at
            FROM channel_state
            WHERE channel_id = ?
            """,
            (channel_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return cast("tuple[str | None, str | None]", row)


def _auth_headers() -> dict[str, str]:
    """Build auth headers with the bootstrap bearer token."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


@runtime_checkable
class _MonkeyPatch(Protocol):
    def setenv(self, name: str, value: str) -> None:
        """Proxy for monkeypatch.setenv."""


@runtime_checkable
class _PathLike(Protocol):
    def __truediv__(self, name: str) -> _PathLike:
        """Join path fragments with /."""

    def as_posix(self) -> str:
        """Return POSIX path string."""


def _as_path(path: object) -> _PathLike:
    """Cast pytest tmp_path to protocol for type checking."""
    return cast("_PathLike", path)


def _as_monkeypatch(patcher: object) -> _MonkeyPatch:
    """Cast pytest monkeypatch to protocol for type checking."""
    return cast("_MonkeyPatch", patcher)
