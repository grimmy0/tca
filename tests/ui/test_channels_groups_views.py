"""Tests for channels/groups management UI views and persistence behavior."""

from __future__ import annotations

import sqlite3
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app
from tca.storage import ChannelAlreadyAssignedToGroupError, ChannelGroupsRepository

if TYPE_CHECKING:
    from pathlib import Path

BOOTSTRAP_TOKEN = "ui-channels-groups-bootstrap-token"  # noqa: S105
DEFAULT_ACCOUNT_ID = 1
UPDATED_GROUP_HORIZON_MINUTES = 180


def test_channels_view_add_edit_disable_channel_persists_changes(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure channel create/edit/disable actions work via UI endpoints."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-channels-groups-channels.sqlite3",
        output_file_name="ui-channels-groups-channels-token.txt",
    )
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account(db_path=db_path)
        created = client.post(
            "/ui/channels",
            data={
                "account_id": str(DEFAULT_ACCOUNT_ID),
                "telegram_channel_id": "20001",
                "name": "alpha",
                "username": "alpha_user",
            },
            headers=_auth_headers(),
            follow_redirects=False,
        )
        channel_id = _fetch_single_channel_id(db_path=db_path)
        edited = client.post(
            f"/ui/channels/{channel_id}/edit",
            data={"name": "alpha-updated", "username": ""},
            headers=_auth_headers(),
            follow_redirects=False,
        )
        disabled = client.post(
            f"/ui/channels/{channel_id}/disable",
            headers=_auth_headers(),
            follow_redirects=False,
        )

    if created.status_code != HTTPStatus.SEE_OTHER:
        raise AssertionError
    if edited.status_code != HTTPStatus.SEE_OTHER:
        raise AssertionError
    if disabled.status_code != HTTPStatus.SEE_OTHER:
        raise AssertionError

    channel_row = _fetch_channel_row(db_path=db_path, channel_id=channel_id)
    if channel_row["name"] != "alpha-updated":
        raise AssertionError
    if channel_row["username"] is not None:
        raise AssertionError
    if channel_row["is_enabled"] != 0:
        raise AssertionError


def test_groups_view_creates_group_and_updates_single_channel_assignment(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure group create and one-channel assignment updates persist via UI."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-channels-groups-group-assignment.sqlite3",
        output_file_name="ui-channels-groups-group-assignment-token.txt",
    )
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account(db_path=db_path)
        channel_a = _create_channel(
            client=client,
            db_path=db_path,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=20011,
            name="channel-a",
        )
        channel_b = _create_channel(
            client=client,
            db_path=db_path,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=20012,
            name="channel-b",
        )
        created_group = client.post(
            "/ui/groups",
            data={
                "name": "Priority",
                "description": "First group",
                "dedupe_horizon_minutes_override": "",
                "channel_id": str(channel_a),
            },
            headers=_auth_headers(),
            follow_redirects=False,
        )
        group_id = _fetch_single_group_id(db_path=db_path)
        reassigned = client.post(
            f"/ui/groups/{group_id}/channel",
            data={"channel_id": str(channel_b)},
            headers=_auth_headers(),
            follow_redirects=False,
        )

    if created_group.status_code != HTTPStatus.SEE_OTHER:
        raise AssertionError
    if reassigned.status_code != HTTPStatus.SEE_OTHER:
        raise AssertionError

    membership_rows = _fetch_group_memberships(db_path=db_path, group_id=group_id)
    if membership_rows != [channel_b]:
        raise AssertionError


def test_groups_view_edits_and_persists_horizon_override(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure group horizon override can be edited from UI and persisted."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-channels-groups-group-horizon.sqlite3",
        output_file_name="ui-channels-groups-group-horizon-token.txt",
    )
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account(db_path=db_path)
        created = client.post(
            "/ui/groups",
            data={
                "name": "Horizon Group",
                "description": "",
                "dedupe_horizon_minutes_override": "45",
                "channel_id": "",
            },
            headers=_auth_headers(),
            follow_redirects=False,
        )
        group_id = _fetch_single_group_id(db_path=db_path)
        updated = client.post(
            f"/ui/groups/{group_id}/edit",
            data={
                "name": "Horizon Group",
                "description": "",
                "dedupe_horizon_minutes_override": str(UPDATED_GROUP_HORIZON_MINUTES),
            },
            headers=_auth_headers(),
            follow_redirects=False,
        )

    if created.status_code != HTTPStatus.SEE_OTHER:
        raise AssertionError
    if updated.status_code != HTTPStatus.SEE_OTHER:
        raise AssertionError

    horizon = _fetch_group_horizon_override(db_path=db_path, group_id=group_id)
    if horizon != UPDATED_GROUP_HORIZON_MINUTES:
        raise AssertionError


def test_channels_view_create_requires_existing_account(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure channel create reports missing account FK violations explicitly."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-channels-groups-channel-missing-account.sqlite3",
        output_file_name="ui-channels-groups-channel-missing-account-token.txt",
    )
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            "/ui/channels",
            data={
                "account_id": "9999",
                "telegram_channel_id": "30001",
                "name": "missing-account",
                "username": "",
            },
            headers=_auth_headers(),
            follow_redirects=False,
        )

    if response.status_code != HTTPStatus.NOT_FOUND:
        raise AssertionError
    if "Account" not in response.text or "was not found" not in response.text:
        raise AssertionError
    if _count_rows(db_path=db_path, table_name="telegram_channels") != 0:
        raise AssertionError


def test_groups_view_create_rolls_back_group_when_membership_insert_fails(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure group creation compensates when channel membership insert fails."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-channels-groups-group-create-rollback.sqlite3",
        output_file_name="ui-channels-groups-group-create-rollback-token.txt",
    )
    app = create_app()

    async def _raise_assignment_conflict(
        self: object,  # noqa: ARG001
        *,
        group_id: int,  # noqa: ARG001
        channel_id: int,
    ) -> object:
        raise ChannelAlreadyAssignedToGroupError.for_channel(channel_id)

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        patch(
            "tca.ui.routes.ChannelGroupsRepository.add_channel_membership",
            new=_raise_assignment_conflict,
        ),
        TestClient(app) as client,
    ):
        _insert_account(db_path=db_path)
        channel_id = _create_channel(
            client=client,
            db_path=db_path,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=31001,
            name="rollback-target",
        )
        response = client.post(
            "/ui/groups",
            data={
                "name": "Should Roll Back",
                "description": "",
                "dedupe_horizon_minutes_override": "",
                "channel_id": str(channel_id),
            },
            headers=_auth_headers(),
            follow_redirects=False,
        )

    if response.status_code != HTTPStatus.CONFLICT:
        raise AssertionError
    if _count_rows(db_path=db_path, table_name="channel_groups") != 0:
        raise AssertionError
    if _count_rows(db_path=db_path, table_name="channel_group_members") != 0:
        raise AssertionError


def test_groups_view_reassign_restores_previous_membership_on_failure(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure reassignment restore keeps prior membership when insert fails."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-channels-groups-group-reassign-rollback.sqlite3",
        output_file_name="ui-channels-groups-group-reassign-rollback-token.txt",
    )
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account(db_path=db_path)
        channel_a = _create_channel(
            client=client,
            db_path=db_path,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=32001,
            name="channel-a",
        )
        channel_b = _create_channel(
            client=client,
            db_path=db_path,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=32002,
            name="channel-b",
        )
        created_group = client.post(
            "/ui/groups",
            data={
                "name": "Rollback Group",
                "description": "",
                "dedupe_horizon_minutes_override": "",
                "channel_id": str(channel_a),
            },
            headers=_auth_headers(),
            follow_redirects=False,
        )
        if created_group.status_code != HTTPStatus.SEE_OTHER:
            raise AssertionError
        group_id = _fetch_single_group_id(db_path=db_path)

    original_add_channel_membership = ChannelGroupsRepository.add_channel_membership

    async def _fail_reassignment(
        self: object,
        *,
        group_id: int,
        channel_id: int,
    ) -> object:
        if channel_id == channel_b:
            raise ChannelAlreadyAssignedToGroupError.for_channel(channel_id)
        return await original_add_channel_membership(
            self,
            group_id=group_id,
            channel_id=channel_id,
        )

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        patch(
            "tca.ui.routes.ChannelGroupsRepository.add_channel_membership",
            new=_fail_reassignment,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            f"/ui/groups/{group_id}/channel",
            data={"channel_id": str(channel_b)},
            headers=_auth_headers(),
            follow_redirects=False,
        )

    if response.status_code != HTTPStatus.CONFLICT:
        raise AssertionError
    membership_rows = _fetch_group_memberships(db_path=db_path, group_id=group_id)
    if membership_rows != [channel_a]:
        raise AssertionError


def _create_channel(
    *,
    client: TestClient,
    db_path: Path,
    account_id: int,
    telegram_channel_id: int,
    name: str,
) -> int:
    response = client.post(
        "/ui/channels",
        data={
            "account_id": str(account_id),
            "telegram_channel_id": str(telegram_channel_id),
            "name": name,
            "username": "",
        },
        headers=_auth_headers(),
        follow_redirects=False,
    )
    if response.status_code != HTTPStatus.SEE_OTHER:
        raise AssertionError
    location = response.headers.get("location")
    if location != "/ui/channels-groups":
        raise AssertionError
    return _fetch_channel_id_by_telegram_id(
        db_path=db_path,
        telegram_channel_id=telegram_channel_id,
    )


def _insert_account(*, db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        _ = connection.execute(
            """
            INSERT INTO telegram_accounts (
                id,
                api_id,
                api_hash_encrypted,
                phone_number,
                session_encrypted
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (DEFAULT_ACCOUNT_ID, 12345, b"encrypted-api-hash", "+15550000099", None),
        )
        connection.commit()


def _fetch_single_channel_id(*, db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute("SELECT id FROM telegram_channels ORDER BY id ASC")
        rows = cursor.fetchall()
    if len(rows) != 1:
        raise AssertionError
    value = rows[0][0]
    if type(value) is not int:
        raise AssertionError
    return value


def _fetch_channel_id_by_telegram_id(*, db_path: Path, telegram_channel_id: int) -> int:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            SELECT id
            FROM telegram_channels
            WHERE telegram_channel_id = ?
            """,
            (telegram_channel_id,),
        )
        row = cursor.fetchone()
    if row is None or type(row[0]) is not int:
        raise AssertionError
    return row[0]


def _fetch_channel_row(*, db_path: Path, channel_id: int) -> dict[str, object]:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            SELECT name, username, is_enabled
            FROM telegram_channels
            WHERE id = ?
            """,
            (channel_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise AssertionError
    return {"name": row[0], "username": row[1], "is_enabled": row[2]}


def _fetch_single_group_id(*, db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute("SELECT id FROM channel_groups ORDER BY id ASC")
        rows = cursor.fetchall()
    if len(rows) != 1:
        raise AssertionError
    value = rows[0][0]
    if type(value) is not int:
        raise AssertionError
    return value


def _fetch_group_memberships(*, db_path: Path, group_id: int) -> list[int]:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            SELECT channel_id
            FROM channel_group_members
            WHERE group_id = ?
            ORDER BY channel_id ASC
            """,
            (group_id,),
        )
        rows = cursor.fetchall()
    member_ids: list[int] = []
    for row in rows:
        value = row[0]
        if type(value) is not int:
            raise AssertionError
        member_ids.append(value)
    return member_ids


def _fetch_group_horizon_override(*, db_path: Path, group_id: int) -> int | None:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            SELECT dedupe_horizon_minutes_override
            FROM channel_groups
            WHERE id = ?
            """,
            (group_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise AssertionError
    value = row[0]
    if value is not None and type(value) is not int:
        raise AssertionError
    return value


def _count_rows(*, db_path: Path, table_name: str) -> int:
    query_by_table = {
        "telegram_channels": "SELECT COUNT(*) FROM telegram_channels",
        "channel_groups": "SELECT COUNT(*) FROM channel_groups",
        "channel_group_members": "SELECT COUNT(*) FROM channel_group_members",
    }
    query = query_by_table.get(table_name)
    if query is None:
        raise AssertionError
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(query)
        row = cursor.fetchone()
    if row is None or type(row[0]) is not int:
        raise AssertionError
    return row[0]


def _configure_auth_env(
    *,
    tmp_path: Path,
    monkeypatch: object,
    db_name: str,
    output_file_name: str,
) -> Path:
    patcher = _as_monkeypatch(monkeypatch)
    db_path = tmp_path / db_name
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / output_file_name).as_posix(),
    )
    patcher.setenv("TCA_MODE", "secure-interactive")
    patcher.setenv("TCA_SECRET_FILE", "")
    return db_path


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""
