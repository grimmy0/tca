"""Tests for channel soft-delete API behavior."""

from __future__ import annotations

import sqlite3
from http import HTTPStatus
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

BOOTSTRAP_TOKEN = "channels-soft-delete-token"  # noqa: S105
DEFAULT_ACCOUNT_ID = 1
DEFAULT_CHANNEL_ID = 1
DEFAULT_TELEGRAM_CHANNEL_ID = 11001
EXPECTED_OK_STATUS = HTTPStatus.OK


def test_delete_channel_marks_disabled(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure DELETE marks channel disabled."""
    db_path = _as_path(tmp_path) / "channels-soft-delete.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channels-soft-delete-token.txt").as_posix(),
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
        response = client.delete(
            f"/channels/{DEFAULT_CHANNEL_ID}",
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    payload = cast("dict[str, object]", response.json())
    if payload.get("is_enabled") is not False:
        raise AssertionError

    if _read_channel_enabled(db_path, channel_id=DEFAULT_CHANNEL_ID) is not False:
        raise AssertionError


def test_delete_channel_preserves_historical_items(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure historical items remain queryable after delete."""
    db_path = _as_path(tmp_path) / "channels-soft-delete-items.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channels-soft-delete-items-token.txt").as_posix(),
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
        _insert_item_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            message_id=501,
        )
        response = client.delete(
            f"/channels/{DEFAULT_CHANNEL_ID}",
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    if _read_item_count(db_path, channel_id=DEFAULT_CHANNEL_ID) != 1:
        raise AssertionError


def test_delete_channel_excludes_scheduler_selection(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure disabled channels are excluded from scheduler selection."""
    db_path = _as_path(tmp_path) / "channels-soft-delete-schedule.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channels-soft-delete-schedule-token.txt").as_posix(),
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
        if _read_schedulable_channel_ids(db_path) != [DEFAULT_CHANNEL_ID]:
            raise AssertionError

        response = client.delete(
            f"/channels/{DEFAULT_CHANNEL_ID}",
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    if _read_schedulable_channel_ids(db_path) != []:
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


def _insert_item_fixture(
    db_path: object,
    *,
    channel_id: int,
    message_id: int,
) -> None:
    """Insert an items row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO items (
                channel_id,
                message_id,
                title,
                body
            )
            VALUES (?, ?, ?, ?)
            """,
            (channel_id, message_id, "title", "body"),
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


def _read_item_count(db_path: object, *, channel_id: int) -> int:
    """Read item count for a channel."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        cursor = connection.execute(
            "SELECT COUNT(*) FROM items WHERE channel_id = ?",
            (channel_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _read_schedulable_channel_ids(db_path: object) -> list[int]:
    """Read schedulable channel ids using scheduler selection logic."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        cursor = connection.execute(
            """
            SELECT channels.id
            FROM telegram_channels AS channels
            INNER JOIN telegram_accounts AS accounts
                ON accounts.id = channels.account_id
            WHERE channels.is_enabled = 1
              AND accounts.paused_at IS NULL
            ORDER BY channels.id ASC
            """,
        )
        rows = cursor.fetchall()
    return [row[0] for row in rows]


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
