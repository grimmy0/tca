"""Tests for dedupe decision trace read API behavior."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

BOOTSTRAP_TOKEN = "dedupe-decisions-api-token"  # noqa: S105
EXPECTED_OK_STATUS = HTTPStatus.OK
EXPECTED_NOT_FOUND_STATUS = HTTPStatus.NOT_FOUND
TARGET_ITEM_ID = 101
EXPECTED_TRACE_ENTRY_COUNT = 2
EXPECTED_TITLE_SIMILARITY_SCORE = 0.98


def test_get_dedupe_decisions_returns_full_strategy_trace(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure trace endpoint returns all strategy attempt records for item."""
    db_path = tmp_path / "dedupe-decisions-api-trace.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "dedupe-decisions-bootstrap-token.txt").as_posix(),
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
            title="Primary item",
        )
        _insert_item(
            db_path=db_path,
            item_id=102,
            channel_id=10,
            message_id=1002,
            title="Candidate item",
        )
        _insert_cluster(
            db_path=db_path,
            cluster_id=1,
            cluster_key="cluster-1",
            representative_item_id=101,
        )
        _insert_decision(
            db_path=db_path,
            decision_id=1,
            item_id=101,
            cluster_id=1,
            candidate_item_id=102,
            strategy_name="exact_url",
            outcome="ABSTAIN",
            reason_code="exact_url_missing",
            score=None,
            metadata_json='{"candidate_rank": 1}',
            created_at="2026-02-18 01:02:03",
        )
        _insert_decision(
            db_path=db_path,
            decision_id=2,
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
            "/dedupe/decisions/101",
            headers=_auth_headers(),
        )

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    payload = cast("dict[str, object]", response.json())
    if payload.get("item_id") != TARGET_ITEM_ID:
        raise AssertionError

    decisions = cast("list[dict[str, object]]", payload.get("decisions"))
    if len(decisions) != EXPECTED_TRACE_ENTRY_COUNT:
        raise AssertionError
    if decisions[0].get("strategy_name") != "exact_url":
        raise AssertionError
    if decisions[0].get("metadata_json") != '{"candidate_rank": 1}':
        raise AssertionError
    if decisions[1].get("strategy_name") != "title_similarity":
        raise AssertionError
    if decisions[1].get("outcome") != "DUPLICATE":
        raise AssertionError
    if decisions[1].get("score") != EXPECTED_TITLE_SIMILARITY_SCORE:
        raise AssertionError


def test_get_dedupe_decisions_returns_404_for_unknown_item(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure unknown item identifiers produce deterministic 404 response."""
    db_path = tmp_path / "dedupe-decisions-api-not-found.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "dedupe-decisions-not-found-token.txt").as_posix(),
    )

    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            "/dedupe/decisions/9999",
            headers=_auth_headers(),
        )

    if response.status_code != EXPECTED_NOT_FOUND_STATUS:
        raise AssertionError
    payload = cast("dict[str, object]", response.json())
    if payload.get("detail") != "Item '9999' was not found.":
        raise AssertionError


def test_get_dedupe_decisions_openapi_schema_is_explicit_and_stable(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure route uses explicit response model with stable schema fields."""
    db_path = tmp_path / "dedupe-decisions-openapi.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "dedupe-decisions-openapi-token.txt").as_posix(),
    )

    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            "/openapi.json",
            headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
        )
    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    openapi = cast("dict[str, object]", response.json())
    paths = cast("dict[str, object]", openapi["paths"])
    route_item = cast("dict[str, object]", paths["/dedupe/decisions/{item_id}"])
    get_operation = cast("dict[str, object]", route_item["get"])
    responses = cast("dict[str, object]", get_operation["responses"])
    ok_response = cast("dict[str, object]", responses["200"])
    content = cast("dict[str, object]", ok_response["content"])
    app_json = cast("dict[str, object]", content["application/json"])
    schema = cast("dict[str, object]", app_json["schema"])
    if schema.get("$ref") != "#/components/schemas/DedupeDecisionTraceResponse":
        raise AssertionError

    components = cast("dict[str, object]", openapi["components"])
    component_schemas = cast("dict[str, object]", components["schemas"])
    trace_schema = cast(
        "dict[str, object]",
        component_schemas["DedupeDecisionTraceResponse"],
    )
    trace_properties = cast("dict[str, object]", trace_schema["properties"])
    if trace_schema.get("required") != ["item_id", "decisions"]:
        raise AssertionError
    if set(trace_properties) != {"item_id", "decisions"}:
        raise AssertionError

    entry_schema = cast(
        "dict[str, object]",
        component_schemas["DedupeDecisionTraceEntryResponse"],
    )
    entry_properties = cast("dict[str, object]", entry_schema["properties"])
    expected_entry_fields = {
        "decision_id",
        "item_id",
        "cluster_id",
        "candidate_item_id",
        "strategy_name",
        "outcome",
        "reason_code",
        "score",
        "metadata_json",
        "created_at",
    }
    if set(entry_properties) != expected_entry_fields:
        raise AssertionError


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


def _insert_item(
    *,
    db_path: Path,
    item_id: int,
    channel_id: int,
    message_id: int,
    title: str,
) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute(
            """
            INSERT INTO items (
                id,
                channel_id,
                message_id,
                published_at,
                title
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (item_id, channel_id, message_id, _iso_utc(), title),
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


def _iso_utc() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat(sep=" ")


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of monkeypatch fixture used in this module."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for current pytest scope."""
