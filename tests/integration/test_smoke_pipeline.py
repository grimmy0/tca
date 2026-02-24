"""End-to-end smoke test for create-channel -> poll -> dedupe -> thread flow."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from tca.api.app import create_app

BOOTSTRAP_TOKEN = "integration-smoke-token"  # noqa: S105
FIXED_CORRELATION_ID = "00000000-0000-0000-0000-000000000001"
ACCOUNT_ID = 1
TELEGRAM_CHANNEL_ID = 88001
MESSAGE_ONE_ID = 9001
MESSAGE_TWO_ID = 9002
THREAD_CLUSTER_ID = 7001
THREAD_CLUSTER_KEY = "smoke-cluster-7001"
DUPLICATE_COUNT = 2
PUBLISHED_AT = datetime.now(UTC).replace(microsecond=0).isoformat()


def test_smoke_pipeline_auth_mocked_create_poll_dedupe_thread(  # noqa: C901, PLR0912
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Smoke test should produce stable dedupe cluster output in thread API."""
    db_path = _as_path(tmp_path) / "integration-smoke-pipeline.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "integration-smoke-bootstrap-token.txt").as_posix(),
    )

    app = create_app()
    auth_headers = _auth_headers()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        patch(
            "tca.api.routes.jobs.uuid4",
            return_value=UUID(FIXED_CORRELATION_ID),
        ),
        TestClient(app) as client,
    ):
        _insert_account_fixture(db_path, account_id=ACCOUNT_ID)

        create_response = client.post(
            "/channels",
            json={
                "account_id": ACCOUNT_ID,
                "telegram_channel_id": TELEGRAM_CHANNEL_ID,
                "name": "smoke-alpha",
                "username": "smoke_alpha",
            },
            headers=auth_headers,
        )
        if create_response.status_code != HTTPStatus.CREATED:
            raise AssertionError

        channel_payload = cast("dict[str, object]", create_response.json())
        created_channel_id = channel_payload.get("id")
        if not isinstance(created_channel_id, int):
            raise TypeError

        poll_response = client.post(
            f"/jobs/poll-now/{created_channel_id}",
            headers=auth_headers,
        )
        if poll_response.status_code != HTTPStatus.ACCEPTED:
            raise AssertionError

        poll_payload = cast("dict[str, object]", poll_response.json())
        if poll_payload.get("correlation_id") != FIXED_CORRELATION_ID:
            raise AssertionError

        _insert_mock_dedupe_output(
            db_path=db_path,
            channel_id=created_channel_id,
        )

        thread_response = client.get(
            "/thread?page=1&size=10",
            headers=auth_headers,
        )

    if thread_response.status_code != HTTPStatus.OK:
        raise AssertionError

    thread_payload = cast("list[dict[str, object]]", thread_response.json())
    if len(thread_payload) != 1:
        raise AssertionError

    thread_entry = thread_payload[0]
    if thread_entry.get("cluster_id") != THREAD_CLUSTER_ID:
        raise AssertionError
    if thread_entry.get("cluster_key") != THREAD_CLUSTER_KEY:
        raise AssertionError
    if thread_entry.get("duplicate_count") != DUPLICATE_COUNT:
        raise AssertionError

    representative = cast("dict[str, object]", thread_entry.get("representative"))
    if representative.get("item_id") != MESSAGE_ONE_ID:
        raise AssertionError
    if representative.get("title") != "Smoke headline":
        raise AssertionError
    if representative.get("canonical_url") != "https://example.com/smoke":
        raise AssertionError
    if representative.get("channel_name") != "smoke-alpha":
        raise AssertionError


def _insert_account_fixture(db_path: Path, *, account_id: int) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
            VALUES (?, ?, ?)
            """,
            (account_id, 12345, b"encrypted-api-hash"),
        )
        connection.commit()


def _insert_mock_dedupe_output(*, db_path: Path, channel_id: int) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
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
            (
                MESSAGE_ONE_ID,
                channel_id,
                500001,
                PUBLISHED_AT,
                "Smoke headline",
                "Smoke body one",
                "https://example.com/smoke",
            ),
        )
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
            (
                MESSAGE_TWO_ID,
                channel_id,
                500002,
                PUBLISHED_AT,
                "Smoke headline",
                "Smoke body two",
                "https://example.com/smoke",
            ),
        )
        _ = connection.execute(
            """
            INSERT INTO dedupe_clusters (id, cluster_key, representative_item_id)
            VALUES (?, ?, ?)
            """,
            (THREAD_CLUSTER_ID, THREAD_CLUSTER_KEY, MESSAGE_ONE_ID),
        )
        _ = connection.execute(
            """
            INSERT INTO dedupe_members (cluster_id, item_id)
            VALUES (?, ?), (?, ?)
            """,
            (
                THREAD_CLUSTER_ID,
                MESSAGE_ONE_ID,
                THREAD_CLUSTER_ID,
                MESSAGE_TWO_ID,
            ),
        )
        connection.commit()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _as_path(value: object) -> Path:
    if not isinstance(value, Path):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Typed protocol for pytest monkeypatch used in this integration test."""

    def setenv(self, name: str, value: str) -> None:
        """Set an environment variable for the current test process."""
        ...


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value
