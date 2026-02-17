"""Tests for channel purge-delete API behavior."""

from __future__ import annotations

import json
import sqlite3
from http import HTTPStatus
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

BOOTSTRAP_TOKEN = "channels-purge-delete-token"  # noqa: S105
DEFAULT_ACCOUNT_ID = 1
DEFAULT_CHANNEL_ID = 1
EXPECTED_OK_STATUS = HTTPStatus.OK


def test_purge_delete_removes_channel_items_and_raw_messages_and_records_audit(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure purge delete removes channel data and records audit event."""
    db_path = _as_path(tmp_path) / "channels-purge-delete.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channels-purge-delete-token.txt").as_posix(),
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
            telegram_channel_id=11001,
            name="alpha",
            is_enabled=True,
        )
        raw_message_id = _insert_raw_message_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            message_id=501,
        )
        _insert_item_fixture(
            db_path,
            item_id=1001,
            channel_id=DEFAULT_CHANNEL_ID,
            message_id=501,
            raw_message_id=raw_message_id,
        )
        response = client.delete(
            f"/channels/{DEFAULT_CHANNEL_ID}?purge=true",
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    if _read_item_count(db_path, channel_id=DEFAULT_CHANNEL_ID) != 0:
        raise AssertionError
    if _read_raw_message_count(db_path, channel_id=DEFAULT_CHANNEL_ID) != 0:
        raise AssertionError
    if _read_channel_exists(db_path, channel_id=DEFAULT_CHANNEL_ID) is True:
        raise AssertionError

    audit_rows = _read_notification_rows(db_path)
    if len(audit_rows) != 1:
        raise AssertionError
    if audit_rows[0]["type"] != "channel_purged":
        raise AssertionError


def test_purge_delete_recomputes_clusters_and_removes_empty(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure purge delete recomputes cluster representatives and deletes empties."""
    db_path = _as_path(tmp_path) / "channels-purge-delete-clusters.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channels-purge-delete-clusters-token.txt").as_posix(),
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
            telegram_channel_id=11001,
            name="alpha",
            is_enabled=True,
        )
        _insert_channel_fixture(
            db_path,
            channel_id=2,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=11002,
            name="bravo",
            is_enabled=True,
        )

        _insert_item_fixture(
            db_path,
            item_id=101,
            channel_id=DEFAULT_CHANNEL_ID,
            message_id=101,
            raw_message_id=None,
        )
        _insert_item_fixture(
            db_path,
            item_id=102,
            channel_id=DEFAULT_CHANNEL_ID,
            message_id=102,
            raw_message_id=None,
        )
        _insert_item_fixture(
            db_path,
            item_id=103,
            channel_id=DEFAULT_CHANNEL_ID,
            message_id=103,
            raw_message_id=None,
        )
        _insert_item_fixture(
            db_path,
            item_id=201,
            channel_id=2,
            message_id=201,
            raw_message_id=None,
            canonical_url="https://example.com/201",
        )

        _insert_cluster_fixture(
            db_path,
            cluster_id=10,
            cluster_key="cluster-a",
            representative_item_id=101,
        )
        _insert_cluster_fixture(
            db_path,
            cluster_id=20,
            cluster_key="cluster-b",
            representative_item_id=103,
        )
        _insert_member_fixture(db_path, cluster_id=10, item_id=101)
        _insert_member_fixture(db_path, cluster_id=10, item_id=102)
        _insert_member_fixture(db_path, cluster_id=20, item_id=103)
        _insert_member_fixture(db_path, cluster_id=20, item_id=201)

        response = client.delete(
            f"/channels/{DEFAULT_CHANNEL_ID}?purge=true",
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    cluster_ids = _read_cluster_ids(db_path)
    if 10 in cluster_ids:
        raise AssertionError
    if 20 not in cluster_ids:
        raise AssertionError

    representative_id = _read_cluster_representative(db_path, cluster_id=20)
    if representative_id != 201:
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


def _insert_channel_fixture(
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


def _insert_raw_message_fixture(
    db_path: object,
    *,
    channel_id: int,
    message_id: int,
) -> int:
    """Insert a raw message row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            """
            INSERT INTO raw_messages (
                channel_id,
                message_id,
                payload_json
            )
            VALUES (?, ?, ?)
            """,
            (channel_id, message_id, "{}"),
        )
        connection.commit()
    return int(cursor.lastrowid)


def _insert_item_fixture(
    db_path: object,
    *,
    item_id: int,
    channel_id: int,
    message_id: int,
    raw_message_id: int | None,
    canonical_url: str | None = None,
) -> None:
    """Insert an items row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO items (
                id,
                channel_id,
                message_id,
                raw_message_id,
                title,
                body,
                canonical_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                channel_id,
                message_id,
                raw_message_id,
                "title",
                "body",
                canonical_url,
            ),
        )
        connection.commit()


def _insert_cluster_fixture(
    db_path: object,
    *,
    cluster_id: int,
    cluster_key: str,
    representative_item_id: int | None,
) -> None:
    """Insert a dedupe cluster row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO dedupe_clusters (
                id,
                cluster_key,
                representative_item_id
            )
            VALUES (?, ?, ?)
            """,
            (cluster_id, cluster_key, representative_item_id),
        )
        connection.commit()


def _insert_member_fixture(
    db_path: object,
    *,
    cluster_id: int,
    item_id: int,
) -> None:
    """Insert a dedupe member row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO dedupe_members (cluster_id, item_id)
            VALUES (?, ?)
            """,
            (cluster_id, item_id),
        )
        connection.commit()


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


def _read_raw_message_count(db_path: object, *, channel_id: int) -> int:
    """Read raw message count for a channel."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        cursor = connection.execute(
            "SELECT COUNT(*) FROM raw_messages WHERE channel_id = ?",
            (channel_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def _read_channel_exists(db_path: object, *, channel_id: int) -> bool:
    """Return True when the channel row exists."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        cursor = connection.execute(
            "SELECT 1 FROM telegram_channels WHERE id = ?",
            (channel_id,),
        )
        row = cursor.fetchone()
    return row is not None


def _read_cluster_ids(db_path: object) -> list[int]:
    """Read all cluster ids."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        cursor = connection.execute(
            "SELECT id FROM dedupe_clusters ORDER BY id ASC",
        )
        rows = cursor.fetchall()
    return [int(row[0]) for row in rows]


def _read_cluster_representative(db_path: object, *, cluster_id: int) -> int | None:
    """Read representative item id for a cluster."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        cursor = connection.execute(
            "SELECT representative_item_id FROM dedupe_clusters WHERE id = ?",
            (cluster_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return cast("int | None", row[0])


def _read_notification_rows(db_path: object) -> list[dict[str, object]]:
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


def _auth_headers() -> dict[str, str]:
    """Build auth headers with the bootstrap bearer token."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


@runtime_checkable
class _MonkeyPatch(Protocol):
    def setenv(self, name: str, value: str) -> None:
        """Proxy for monkeypatch.setenv."""


@runtime_checkable
class _PathLike(Protocol):
    def __truediv__(self, name: str) -> "_PathLike":
        """Join path fragments with /."""

    def as_posix(self) -> str:
        """Return POSIX path string."""


def _as_path(path: object) -> _PathLike:
    """Cast pytest tmp_path to protocol for type checking."""
    return cast("_PathLike", path)


def _as_monkeypatch(patcher: object) -> _MonkeyPatch:
    """Cast pytest monkeypatch to protocol for type checking."""
    return cast("_MonkeyPatch", patcher)
