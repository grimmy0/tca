"""Tests for merged thread UI and dedupe explainability drill-down behavior."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

BOOTSTRAP_TOKEN = "ui-thread-bootstrap-token"  # noqa: S105


def test_thread_view_shows_representative_duplicate_count_and_sources(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Thread page should show representative item, duplicate count, and sources."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-thread-list.sqlite3",
        output_file_name="ui-thread-list-token.txt",
    )
    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account(db_path=db_path, account_id=1)
        _insert_channel(
            db_path=db_path,
            channel_id=10,
            account_id=1,
            telegram_channel_id=5010,
            name="alpha",
        )
        _insert_channel(
            db_path=db_path,
            channel_id=11,
            account_id=1,
            telegram_channel_id=5011,
            name="beta",
        )
        _insert_item(
            db_path=db_path,
            item_id=101,
            channel_id=10,
            message_id=1001,
            published_at=_iso_utc(hours_ago=0),
            title="Representative alpha",
            body="Primary body",
            canonical_url="https://example.com/alpha",
        )
        _insert_item(
            db_path=db_path,
            item_id=102,
            channel_id=11,
            message_id=1002,
            published_at=_iso_utc(hours_ago=1),
            title="Duplicate beta",
            body=None,
            canonical_url=None,
        )
        _insert_cluster(
            db_path=db_path,
            cluster_id=1,
            cluster_key="cluster-1",
            representative_item_id=101,
        )
        _insert_member(db_path=db_path, cluster_id=1, item_id=101)
        _insert_member(db_path=db_path, cluster_id=1, item_id=102)

        response = client.get("/ui/thread", headers=_auth_headers())

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    body = response.text
    for expected in (
        "Representative alpha",
        "Duplicate count: 2.",
        "Sources: alpha, beta.",
    ):
        if expected not in body:
            raise AssertionError


def test_thread_view_selection_shows_dedupe_decision_details(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Selecting one entry should render dedupe decision details panel."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-thread-decisions.sqlite3",
        output_file_name="ui-thread-decisions-token.txt",
    )
    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account(db_path=db_path, account_id=1)
        _insert_channel(
            db_path=db_path,
            channel_id=10,
            account_id=1,
            telegram_channel_id=5010,
            name="alpha",
        )
        _insert_item(
            db_path=db_path,
            item_id=101,
            channel_id=10,
            message_id=1001,
            published_at=_iso_utc(hours_ago=0),
            title="Representative alpha",
            body=None,
            canonical_url=None,
        )
        _insert_cluster(
            db_path=db_path,
            cluster_id=1,
            cluster_key="cluster-1",
            representative_item_id=101,
        )
        _insert_member(db_path=db_path, cluster_id=1, item_id=101)
        _insert_decision(
            db_path=db_path,
            decision_id=1,
            item_id=101,
            cluster_id=1,
            candidate_item_id=102,
            strategy_name="title_similarity",
            outcome="DUPLICATE",
            reason_code="title_similarity_match",
            score=0.98,
            metadata_json='{"threshold": 0.92}',
            created_at="2026-02-18 01:02:04",
        )

        response = client.get(
            "/ui/thread?selected_item_id=101",
            headers=_auth_headers(),
        )

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    body = response.text
    for expected in (
        "title_similarity",
        "DUPLICATE",
        "title_similarity_match",
        "Metadata: {&#34;threshold&#34;: 0.92}",
    ):
        if expected not in body:
            raise AssertionError


def test_thread_view_pagination_and_filter_controls_work_end_to_end(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Thread controls should page deterministically and filter by source channel."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-thread-pagination-filter.sqlite3",
        output_file_name="ui-thread-pagination-filter-token.txt",
    )
    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account(db_path=db_path, account_id=1)
        _insert_channel(
            db_path=db_path,
            channel_id=10,
            account_id=1,
            telegram_channel_id=5010,
            name="alpha",
        )
        _insert_channel(
            db_path=db_path,
            channel_id=11,
            account_id=1,
            telegram_channel_id=5011,
            name="beta",
        )
        _insert_item(
            db_path=db_path,
            item_id=101,
            channel_id=10,
            message_id=1001,
            published_at=_iso_utc(hours_ago=0),
            title="Newest alpha",
            body=None,
            canonical_url=None,
        )
        _insert_item(
            db_path=db_path,
            item_id=201,
            channel_id=11,
            message_id=2001,
            published_at=_iso_utc(hours_ago=1),
            title="Middle beta",
            body=None,
            canonical_url=None,
        )
        _insert_item(
            db_path=db_path,
            item_id=301,
            channel_id=10,
            message_id=3001,
            published_at=_iso_utc(hours_ago=2),
            title="Oldest alpha",
            body=None,
            canonical_url=None,
        )
        _insert_cluster(
            db_path=db_path,
            cluster_id=1,
            cluster_key="cluster-1",
            representative_item_id=101,
        )
        _insert_cluster(
            db_path=db_path,
            cluster_id=2,
            cluster_key="cluster-2",
            representative_item_id=201,
        )
        _insert_cluster(
            db_path=db_path,
            cluster_id=3,
            cluster_key="cluster-3",
            representative_item_id=301,
        )
        _insert_member(db_path=db_path, cluster_id=1, item_id=101)
        _insert_member(db_path=db_path, cluster_id=2, item_id=201)
        _insert_member(db_path=db_path, cluster_id=3, item_id=301)

        page_one = client.get("/ui/thread?page=1&size=1", headers=_auth_headers())
        page_two = client.get("/ui/thread?page=2&size=1", headers=_auth_headers())
        filtered = client.get(
            "/ui/thread?page=1&size=10&channel_id=10",
            headers=_auth_headers(),
        )

    if page_one.status_code != HTTPStatus.OK:
        raise AssertionError
    if page_two.status_code != HTTPStatus.OK:
        raise AssertionError
    if filtered.status_code != HTTPStatus.OK:
        raise AssertionError

    if (
        "Newest alpha" not in page_one.text
        or 'id="thread-next-page"' not in page_one.text
    ):
        raise AssertionError
    if (
        "Middle beta" not in page_two.text
        or 'id="thread-prev-page"' not in page_two.text
    ):
        raise AssertionError

    filtered_body = filtered.text
    if "Newest alpha" not in filtered_body:
        raise AssertionError
    if "Oldest alpha" not in filtered_body:
        raise AssertionError
    if "Middle beta" in filtered_body:
        raise AssertionError


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
    return db_path


def _insert_account(*, db_path: Path, account_id: int) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
            VALUES (?, ?, ?)
            """,
            (account_id, 12345, b"encrypted-api-hash"),
        )
        connection.commit()


def _insert_channel(
    *,
    db_path: Path,
    channel_id: int,
    account_id: int,
    telegram_channel_id: int,
    name: str,
) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO telegram_channels (
                id,
                account_id,
                telegram_channel_id,
                name,
                is_enabled
            )
            VALUES (?, ?, ?, ?, 1)
            """,
            (channel_id, account_id, telegram_channel_id, name),
        )
        connection.commit()


def _insert_item(  # noqa: PLR0913
    *,
    db_path: Path,
    item_id: int,
    channel_id: int,
    message_id: int,
    published_at: str,
    title: str | None,
    body: str | None,
    canonical_url: str | None,
) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
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
    *,
    db_path: Path,
    cluster_id: int,
    cluster_key: str,
    representative_item_id: int,
) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO dedupe_clusters (id, cluster_key, representative_item_id)
            VALUES (?, ?, ?)
            """,
            (cluster_id, cluster_key, representative_item_id),
        )
        connection.commit()


def _insert_member(*, db_path: Path, cluster_id: int, item_id: int) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO dedupe_members (cluster_id, item_id)
            VALUES (?, ?)
            """,
            (cluster_id, item_id),
        )
        connection.commit()


def _insert_decision(  # noqa: PLR0913
    *,
    db_path: Path,
    decision_id: int,
    item_id: int,
    cluster_id: int,
    candidate_item_id: int,
    strategy_name: str,
    outcome: str,
    reason_code: str,
    score: float | None,
    metadata_json: str,
    created_at: str,
) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO dedupe_decisions (
                id,
                item_id,
                cluster_id,
                candidate_item_id,
                strategy_name,
                outcome,
                reason_code,
                score,
                metadata_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                item_id,
                cluster_id,
                candidate_item_id,
                strategy_name,
                outcome,
                reason_code,
                score,
                metadata_json,
                created_at,
            ),
        )
        connection.commit()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _iso_utc(*, hours_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""
