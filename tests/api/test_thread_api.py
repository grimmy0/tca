"""Tests for GET /thread API behavior."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tca.api.app import create_app
from tca.api.routes.thread import router as thread_router

BOOTSTRAP_TOKEN = "thread-api-token"  # noqa: S105
EXPECTED_OK_STATUS = HTTPStatus.OK
EXPECTED_UNAUTHORIZED_STATUS = HTTPStatus.UNAUTHORIZED
EXPECTED_UNPROCESSABLE_ENTITY = HTTPStatus.UNPROCESSABLE_ENTITY
EXPECTED_ENTRY_COUNT = 2
PRIMARY_CLUSTER_ID = 1
SECONDARY_CLUSTER_ID = 2
PRIMARY_CLUSTER_DUPLICATE_COUNT = 2
SECONDARY_CLUSTER_DUPLICATE_COUNT = 1


def test_get_thread_returns_cluster_entries_with_duplicate_counts(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure endpoint returns cluster-level rows with duplicate counts."""
    db_path = _as_path(tmp_path) / "thread-api-list.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "thread-bootstrap-token.txt").as_posix(),
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
        _insert_account(db_path, account_id=1)
        _insert_channel(
            db_path,
            channel_id=10,
            account_id=1,
            telegram_channel_id=5010,
            name="alpha",
            username="alpha_user",
        )
        _insert_channel(
            db_path,
            channel_id=11,
            account_id=1,
            telegram_channel_id=5011,
            name="beta",
            username=None,
        )

        _insert_item(
            db_path,
            item_id=101,
            channel_id=10,
            message_id=1001,
            published_at=_iso_utc(hours_ago=0),
            title="Primary",
            body="Body",
            canonical_url="https://example.com/a",
        )
        _insert_item(
            db_path,
            item_id=102,
            channel_id=11,
            message_id=1002,
            published_at=_iso_utc(hours_ago=1),
            title="Duplicate",
            body=None,
            canonical_url=None,
        )
        _insert_item(
            db_path,
            item_id=201,
            channel_id=11,
            message_id=2001,
            published_at=_iso_utc(hours_ago=24),
            title="Secondary",
            body=None,
            canonical_url=None,
        )

        _insert_cluster(
            db_path,
            cluster_id=PRIMARY_CLUSTER_ID,
            cluster_key="cluster-1",
            representative_item_id=101,
        )
        _insert_cluster(
            db_path,
            cluster_id=SECONDARY_CLUSTER_ID,
            cluster_key="cluster-2",
            representative_item_id=201,
        )

        _insert_member(db_path, cluster_id=PRIMARY_CLUSTER_ID, item_id=101)
        _insert_member(db_path, cluster_id=PRIMARY_CLUSTER_ID, item_id=102)
        _insert_member(db_path, cluster_id=SECONDARY_CLUSTER_ID, item_id=201)

        response = client.get("/thread?page=1&size=10", headers=auth_headers)

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    payload = cast("list[dict[str, object]]", response.json())
    if len(payload) != EXPECTED_ENTRY_COUNT:
        raise AssertionError

    first = cast("dict[str, object]", payload[0])
    first_representative = cast("dict[str, object]", first["representative"])
    if first.get("cluster_id") != PRIMARY_CLUSTER_ID:
        raise AssertionError
    if first.get("duplicate_count") != PRIMARY_CLUSTER_DUPLICATE_COUNT:
        raise AssertionError
    if first_representative.get("channel_name") != "alpha":
        raise AssertionError

    second = cast("dict[str, object]", payload[1])
    if second.get("cluster_id") != SECONDARY_CLUSTER_ID:
        raise AssertionError
    if second.get("duplicate_count") != SECONDARY_CLUSTER_DUPLICATE_COUNT:
        raise AssertionError


def test_get_thread_validates_page_query_bounds(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure page validation rejects values lower than one."""
    db_path = _as_path(tmp_path) / "thread-api-page-bounds.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "thread-page-bootstrap-token.txt").as_posix(),
    )

    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get("/thread?page=0", headers=_auth_headers())

    if response.status_code != EXPECTED_UNPROCESSABLE_ENTITY:
        raise AssertionError


def test_get_thread_validates_size_query_bounds(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure size validation enforces min and max constraints."""
    db_path = _as_path(tmp_path) / "thread-api-size-bounds.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "thread-size-bootstrap-token.txt").as_posix(),
    )

    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        too_small = client.get("/thread?size=0", headers=_auth_headers())
        too_large = client.get("/thread?size=101", headers=_auth_headers())

    if too_small.status_code != EXPECTED_UNPROCESSABLE_ENTITY:
        raise AssertionError
    if too_large.status_code != EXPECTED_UNPROCESSABLE_ENTITY:
        raise AssertionError


def test_get_thread_requires_bearer_auth(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure GET /thread rejects requests without bearer auth."""
    db_path = _as_path(tmp_path) / "thread-api-auth.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "thread-auth-bootstrap-token.txt").as_posix(),
    )

    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get("/thread")

    if response.status_code != EXPECTED_UNAUTHORIZED_STATUS:
        raise AssertionError


def test_get_thread_raises_when_storage_runtime_missing() -> None:
    """Ensure newly added runtime resolution error path is exercised."""
    app = FastAPI()
    app.include_router(thread_router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/thread")
    if response.status_code != HTTPStatus.INTERNAL_SERVER_ERROR:
        raise AssertionError


def _insert_account(db_path: object, *, account_id: int) -> None:
    """Insert one telegram account fixture row."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
            VALUES (?, ?, ?)
            """,
            (account_id, 12345, b"encrypted-api-hash"),
        )
        connection.commit()


def _insert_channel(  # noqa: PLR0913
    db_path: object,
    *,
    channel_id: int,
    account_id: int,
    telegram_channel_id: int,
    name: str,
    username: str | None,
) -> None:
    """Insert one telegram channel fixture row."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO telegram_channels (
                id,
                account_id,
                telegram_channel_id,
                name,
                username,
                is_enabled
            )
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (channel_id, account_id, telegram_channel_id, name, username),
        )
        connection.commit()


def _insert_item(  # noqa: PLR0913
    db_path: object,
    *,
    item_id: int,
    channel_id: int,
    message_id: int,
    published_at: str,
    title: str | None,
    body: str | None,
    canonical_url: str | None,
) -> None:
    """Insert one normalized item fixture row."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO items (
                id,
                channel_id,
                message_id,
                published_at,
                title,
                body,
                canonical_url,
                dedupe_state
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'clustered')
            """,
            (item_id, channel_id, message_id, published_at, title, body, canonical_url),
        )
        connection.commit()


def _insert_cluster(
    db_path: object,
    *,
    cluster_id: int,
    cluster_key: str,
    representative_item_id: int,
) -> None:
    """Insert one dedupe cluster fixture row."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO dedupe_clusters (id, cluster_key, representative_item_id)
            VALUES (?, ?, ?)
            """,
            (cluster_id, cluster_key, representative_item_id),
        )
        connection.commit()


def _insert_member(
    db_path: object,
    *,
    cluster_id: int,
    item_id: int,
) -> None:
    """Insert one dedupe membership fixture row."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO dedupe_members (cluster_id, item_id)
            VALUES (?, ?)
            """,
            (cluster_id, item_id),
        )
        connection.commit()


def _auth_headers() -> dict[str, str]:
    """Build Authorization header for tests."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _iso_utc(*, hours_ago: int) -> str:
    """Build ISO8601 UTC timestamp offset from current time."""
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def _as_path(value: object) -> Path:
    """Narrow input to pathlib.Path."""
    if not isinstance(value, Path):
        raise TypeError
    return value


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
