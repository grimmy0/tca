"""Tests for channel-group API CRUD, membership, and horizon override behavior."""

from __future__ import annotations

import sqlite3
from http import HTTPStatus
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

DEFAULT_ACCOUNT_ID = 1
DEFAULT_CHANNEL_ID = 1
DEFAULT_TELEGRAM_CHANNEL_ID = 10001
SET_OVERRIDE_MINUTES = 180
EXPECTED_CREATED_STATUS = HTTPStatus.CREATED
EXPECTED_NOT_FOUND_STATUS = HTTPStatus.NOT_FOUND
EXPECTED_OK_STATUS = HTTPStatus.OK
BOOTSTRAP_TOKEN = "channel-groups-api-token"  # noqa: S105

SQLiteMembershipRow = tuple[int, int]
SQLiteHorizonRow = tuple[int | None]


def test_channel_group_crud_endpoints_return_expected_status_codes(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure create/list/patch/delete group endpoints return expected statuses."""
    db_path = _as_path(tmp_path) / "channel-groups-api-crud.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channel-groups-bootstrap-token.txt").as_posix(),
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
        initial_list = client.get("/channel-groups", headers=auth_headers)
        created = client.post(
            "/channel-groups",
            json={"name": "Priority Sources", "description": "High-signal feeds"},
            headers=auth_headers,
        )

        _assert_response_status(initial_list, expected=EXPECTED_OK_STATUS)
        _assert_empty_group_list(initial_list)
        _assert_response_status(created, expected=EXPECTED_CREATED_STATUS)
        group_id = _extract_group_id(created)

        listed = client.get("/channel-groups", headers=auth_headers)
        _assert_response_status(listed, expected=EXPECTED_OK_STATUS)
        _assert_group_list_ids(listed, expected_ids=[group_id])

        patched = client.patch(
            f"/channel-groups/{group_id}",
            json={"name": "Priority Sources Updated"},
            headers=auth_headers,
        )
        _assert_response_status(patched, expected=EXPECTED_OK_STATUS)
        _assert_group_name(patched, expected_name="Priority Sources Updated")

        deleted = client.delete(
            f"/channel-groups/{group_id}",
            headers=auth_headers,
        )
        _assert_response_status(deleted, expected=EXPECTED_OK_STATUS)
        deleted_payload = cast("dict[str, object]", deleted.json())
        if deleted_payload.get("deleted_group_id") != group_id:
            raise AssertionError

        patch_missing = client.patch(
            f"/channel-groups/{group_id}",
            json={"name": "does-not-exist"},
            headers=auth_headers,
        )
        delete_missing = client.delete(
            f"/channel-groups/{group_id}",
            headers=auth_headers,
        )
        _assert_response_status(patch_missing, expected=EXPECTED_NOT_FOUND_STATUS)
        _assert_response_status(delete_missing, expected=EXPECTED_NOT_FOUND_STATUS)


def test_channel_group_membership_put_and_delete_update_join_table(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure membership add/remove API endpoints update join-table rows."""
    db_path = _as_path(tmp_path) / "channel-groups-api-memberships.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channel-groups-bootstrap-token.txt").as_posix(),
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
        created = client.post(
            "/channel-groups",
            json={"name": "Membership Group", "description": None},
            headers=auth_headers,
        )
        if created.status_code != EXPECTED_CREATED_STATUS:
            raise AssertionError
        created_payload = cast("dict[str, object]", created.json())
        group_id_obj = created_payload.get("id")
        if type(group_id_obj) is not int:
            raise AssertionError
        group_id = group_id_obj

        _insert_account_and_channel_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            telegram_channel_id=DEFAULT_TELEGRAM_CHANNEL_ID,
            name="alpha",
        )

        added = client.put(
            f"/channel-groups/{group_id}/channels/{DEFAULT_CHANNEL_ID}",
            headers=auth_headers,
        )
        if added.status_code != EXPECTED_OK_STATUS:
            raise AssertionError
        added_payload = cast("dict[str, object]", added.json())
        if added_payload.get("is_member") is not True:
            raise AssertionError
        if _read_memberships(db_path) != [(group_id, DEFAULT_CHANNEL_ID)]:
            raise AssertionError

        removed = client.delete(
            f"/channel-groups/{group_id}/channels/{DEFAULT_CHANNEL_ID}",
            headers=auth_headers,
        )
        if removed.status_code != EXPECTED_OK_STATUS:
            raise AssertionError
        removed_payload = cast("dict[str, object]", removed.json())
        if removed_payload.get("is_member") is not False:
            raise AssertionError
        if _read_memberships(db_path):
            raise AssertionError


def test_channel_group_horizon_override_can_be_set_and_cleared(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure PATCH can set and clear per-group horizon override values."""
    db_path = _as_path(tmp_path) / "channel-groups-api-horizon.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "channel-groups-bootstrap-token.txt").as_posix(),
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
        created = client.post(
            "/channel-groups",
            json={"name": "Horizon Group", "description": "override test"},
            headers=auth_headers,
        )
        if created.status_code != EXPECTED_CREATED_STATUS:
            raise AssertionError

        created_payload = cast("dict[str, object]", created.json())
        group_id_obj = created_payload.get("id")
        if type(group_id_obj) is not int:
            raise AssertionError
        group_id = group_id_obj

        set_override = client.patch(
            f"/channel-groups/{group_id}",
            json={"dedupe_horizon_minutes_override": SET_OVERRIDE_MINUTES},
            headers=auth_headers,
        )
        if set_override.status_code != EXPECTED_OK_STATUS:
            raise AssertionError
        set_payload = cast("dict[str, object]", set_override.json())
        if set_payload.get("dedupe_horizon_minutes_override") != SET_OVERRIDE_MINUTES:
            raise AssertionError
        if _read_group_horizon_override_minutes(db_path, group_id=group_id) != (
            SET_OVERRIDE_MINUTES
        ):
            raise AssertionError

        clear_override = client.patch(
            f"/channel-groups/{group_id}",
            json={"dedupe_horizon_minutes_override": None},
            headers=auth_headers,
        )
        if clear_override.status_code != EXPECTED_OK_STATUS:
            raise AssertionError
        clear_payload = cast("dict[str, object]", clear_override.json())
        if clear_payload.get("dedupe_horizon_minutes_override") is not None:
            raise AssertionError
        if _read_group_horizon_override_minutes(db_path, group_id=group_id) is not None:
            raise AssertionError


def _insert_account_and_channel_fixture(
    db_path: object,
    *,
    channel_id: int,
    telegram_channel_id: int,
    name: str,
) -> None:
    """Insert account and channel rows used by membership endpoint tests."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
            VALUES (?, ?, ?)
            """,
            (DEFAULT_ACCOUNT_ID, 12345, b"encrypted-api-hash"),
        )
        _ = connection.execute(
            """
            INSERT INTO telegram_channels (
                id,
                account_id,
                telegram_channel_id,
                name
            )
            VALUES (?, ?, ?, ?)
            """,
            (channel_id, DEFAULT_ACCOUNT_ID, telegram_channel_id, name),
        )
        connection.commit()


def _read_memberships(db_path: object) -> list[SQLiteMembershipRow]:
    """Read all group memberships from SQLite for endpoint verification."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        rows = connection.execute(
            """
            SELECT group_id, channel_id
            FROM channel_group_members
            ORDER BY group_id, channel_id
            """,
        ).fetchall()
    return cast("list[SQLiteMembershipRow]", rows)


def _read_group_horizon_override_minutes(
    db_path: object,
    *,
    group_id: int,
) -> int | None:
    """Read persisted horizon override value for one group row."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        typed_row = cast(
            "SQLiteHorizonRow | None",
            connection.execute(
                """
                SELECT dedupe_horizon_minutes_override
                FROM channel_groups
                WHERE id = ?
                """,
                (group_id,),
            ).fetchone(),
        )
    if typed_row is None:
        raise AssertionError
    return typed_row[0]


def _assert_response_status(response: ResponseLike, *, expected: HTTPStatus) -> None:
    """Assert one API response status code exactly matches expected value."""
    if response.status_code != expected:
        raise AssertionError


def _assert_empty_group_list(response: ResponseLike) -> None:
    """Assert channel-group list response payload is empty."""
    if response.json() != []:
        raise AssertionError


def _assert_group_list_ids(response: ResponseLike, *, expected_ids: list[int]) -> None:
    """Assert channel-group list payload contains ids in order."""
    payload = cast("list[dict[str, object]]", response.json())
    ids = [row.get("id") for row in payload]
    if ids != expected_ids:
        raise AssertionError


def _assert_group_name(response: ResponseLike, *, expected_name: str) -> None:
    """Assert one channel-group response payload has expected name."""
    payload = cast("dict[str, object]", response.json())
    if payload.get("name") != expected_name:
        raise AssertionError


def _extract_group_id(response: ResponseLike) -> int:
    """Extract and validate integer group id from API response payload."""
    payload = cast("dict[str, object]", response.json())
    group_id_obj = payload.get("id")
    if type(group_id_obj) is not int:
        raise AssertionError
    return group_id_obj


def _auth_headers() -> dict[str, str]:
    """Build deterministic Authorization header for protected endpoint tests."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _as_path(value: object) -> PathLike:
    """Narrow object into a Path-like protocol used by this test module."""
    if not isinstance(value, PathLike):
        raise TypeError
    return value


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    """Narrow monkeypatch fixture object to setenv-capable helper."""
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class PathLike(Protocol):
    """Runtime-checkable subset of pathlib.Path used in this module."""

    def __truediv__(self, key: str) -> PathLike:
        """Join one path segment."""
        ...

    def as_posix(self) -> str:
        """Return POSIX path representation."""
        ...


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""
        ...


@runtime_checkable
class ResponseLike(Protocol):
    """Runtime-checkable subset of FastAPI test client response behavior."""

    @property
    def status_code(self) -> int:
        """Return HTTP status code from response."""
        ...

    def json(self) -> object:
        """Decode response payload JSON value."""
        ...
