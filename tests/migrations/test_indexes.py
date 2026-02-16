"""Migration checks for required secondary indexes (C015)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQLiteIndexListRow = tuple[int, str, int, str, int]
SQLiteIndexInfoRow = tuple[int, int, str]
SQLiteExplainPlanRow = tuple[int, int, int, str]
IndexSignature = tuple[tuple[str, ...], bool]


@dataclass(frozen=True)
class IndexExpectation:
    """Expected index signature from the design document."""

    table_name: str
    columns: tuple[str, ...]
    unique: bool | None


@dataclass(frozen=True)
class QueryPlanExpectation:
    """Expected index usage for representative read queries."""

    label: str
    sql: str
    params: tuple[object, ...]
    expected_index_name: str


PHASE1_INDEX_EXPECTATIONS = (
    IndexExpectation(
        table_name="raw_messages",
        columns=("channel_id", "message_id"),
        unique=True,
    ),
    IndexExpectation(
        table_name="items",
        columns=("channel_id", "message_id"),
        unique=True,
    ),
    IndexExpectation(
        table_name="items",
        columns=("raw_message_id",),
        unique=True,
    ),
    IndexExpectation(
        table_name="items",
        columns=("published_at",),
        unique=False,
    ),
    IndexExpectation(
        table_name="items",
        columns=("canonical_url_hash",),
        unique=False,
    ),
    IndexExpectation(
        table_name="items",
        columns=("content_hash",),
        unique=False,
    ),
    IndexExpectation(
        table_name="channel_group_members",
        columns=("channel_id",),
        unique=True,
    ),
    IndexExpectation(
        table_name="channel_group_members",
        columns=("group_id", "channel_id"),
        unique=None,
    ),
    IndexExpectation(
        table_name="dedupe_members",
        columns=("item_id",),
        unique=False,
    ),
    IndexExpectation(
        table_name="dedupe_clusters",
        columns=("representative_item_id",),
        unique=False,
    ),
    IndexExpectation(
        table_name="ingest_errors",
        columns=("created_at",),
        unique=False,
    ),
)

QUERY_PLAN_EXPECTATIONS = (
    QueryPlanExpectation(
        label="items_recent_feed",
        sql="""
SELECT id
FROM items
WHERE published_at >= ?
ORDER BY published_at DESC
LIMIT 20
""".strip(),
        params=("2026-02-01T00:00:00Z",),
        expected_index_name="ix_items_published_at",
    ),
    QueryPlanExpectation(
        label="items_by_canonical_hash",
        sql="SELECT id FROM items WHERE canonical_url_hash = ?",
        params=("a" * 64,),
        expected_index_name="ix_items_canonical_url_hash",
    ),
    QueryPlanExpectation(
        label="items_by_content_hash",
        sql="SELECT id FROM items WHERE content_hash = ?",
        params=("b" * 64,),
        expected_index_name="ix_items_content_hash",
    ),
    QueryPlanExpectation(
        label="dedupe_members_by_item",
        sql="SELECT cluster_id FROM dedupe_members WHERE item_id = ?",
        params=(1,),
        expected_index_name="ix_dedupe_members_item_id",
    ),
    QueryPlanExpectation(
        label="dedupe_clusters_by_representative",
        sql="SELECT id FROM dedupe_clusters WHERE representative_item_id = ?",
        params=(1,),
        expected_index_name="ix_dedupe_clusters_representative_item_id",
    ),
    QueryPlanExpectation(
        label="ingest_errors_recent",
        sql="""
SELECT id
FROM ingest_errors
WHERE created_at >= ?
ORDER BY created_at DESC
LIMIT 20
""".strip(),
        params=("2026-02-01T00:00:00Z",),
        expected_index_name="ix_ingest_errors_created_at",
    ),
)

C015_CREATED_INDEX_NAMES = {
    "items": frozenset(
        {
            "ix_items_published_at",
            "ix_items_canonical_url_hash",
            "ix_items_content_hash",
        },
    ),
    "dedupe_members": frozenset({"ix_dedupe_members_item_id"}),
    "dedupe_clusters": frozenset({"ix_dedupe_clusters_representative_item_id"}),
    "ingest_errors": frozenset({"ix_ingest_errors_created_at"}),
}


def test_phase1_indexes_from_design_exist_in_metadata(tmp_path: Path) -> None:
    """Ensure all mandatory Phase 1 index signatures are present."""
    db_path = tmp_path / "c015-indexes.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        _assert_expected_phase1_indexes(connection)


def test_phase1_index_assertion_fails_when_required_index_is_missing(
    tmp_path: Path,
) -> None:
    """Ensure the index assertion fails when a required index is removed."""
    db_path = tmp_path / "c015-missing-index.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute("DROP INDEX ix_items_published_at")
        with pytest.raises(AssertionError):
            _assert_expected_phase1_indexes(connection)


def test_representative_read_path_query_plans_use_expected_indexes(
    tmp_path: Path,
) -> None:
    """Ensure representative `EXPLAIN QUERY PLAN` snapshots include index usage."""
    db_path = tmp_path / "c015-explain.sqlite3"
    _upgrade_to_head(db_path)

    with sqlite3.connect(db_path.as_posix()) as connection:
        snapshots: dict[str, tuple[str, ...]] = {}
        for expectation in QUERY_PLAN_EXPECTATIONS:
            details = _explain_query_plan_details(
                connection,
                expectation.sql,
                expectation.params,
            )
            snapshots[expectation.label] = details
            if any(expectation.expected_index_name in detail for detail in details):
                continue
            raise AssertionError

    if len(snapshots) != len(QUERY_PLAN_EXPECTATIONS):
        raise AssertionError


def test_c015_indexes_are_removed_when_downgrading_to_c014(tmp_path: Path) -> None:
    """Ensure indexes introduced by C015 are removed by the C015 downgrade."""
    db_path = tmp_path / "c015-downgrade.sqlite3"
    _upgrade_to_head(db_path)

    result = _run_alembic_command(db_path, ("downgrade", "9c2a8f6d0f7b"))
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)

    with sqlite3.connect(db_path.as_posix()) as connection:
        for table_name, expected_absent in C015_CREATED_INDEX_NAMES.items():
            existing = _index_names_for_table(connection, table_name)
            remaining = expected_absent & existing
            if remaining:
                raise AssertionError


def _assert_expected_phase1_indexes(connection: sqlite3.Connection) -> None:
    missing = [
        expectation
        for expectation in PHASE1_INDEX_EXPECTATIONS
        if not _has_index_signature(connection, expectation)
    ]
    if not missing:
        return

    missing_labels = ", ".join(
        f"{item.table_name}{item.columns}[unique={item.unique}]" for item in missing
    )
    raise AssertionError(missing_labels)


def _has_index_signature(
    connection: sqlite3.Connection,
    expectation: IndexExpectation,
) -> bool:
    signatures = _index_signatures_for_table(connection, expectation.table_name)
    for columns, is_unique in signatures:
        if columns != expectation.columns:
            continue
        if expectation.unique is not None and is_unique != expectation.unique:
            continue
        return True
    return False


def _index_signatures_for_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[IndexSignature]:
    typed_index_rows = _index_list_rows_for_table(connection, table_name)
    signatures: set[IndexSignature] = set()

    for index_row in typed_index_rows:
        index_name = index_row[1]
        is_unique = index_row[2] == 1
        column_rows = connection.execute(
            f"PRAGMA index_info('{index_name}')",
        ).fetchall()
        typed_column_rows = cast("list[SQLiteIndexInfoRow]", column_rows)
        ordered_column_rows = sorted(typed_column_rows, key=lambda row: row[0])
        indexed_columns = tuple(row[2] for row in ordered_column_rows)
        signatures.add((indexed_columns, is_unique))

    return signatures


def _explain_query_plan_details(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[object, ...],
) -> tuple[str, ...]:
    rows = connection.execute(
        f"EXPLAIN QUERY PLAN {sql}",
        params,
    ).fetchall()
    typed_rows = cast("list[SQLiteExplainPlanRow]", rows)
    return tuple(row[3] for row in typed_rows)


def _index_names_for_table(connection: sqlite3.Connection, table_name: str) -> set[str]:
    typed_index_rows = _index_list_rows_for_table(connection, table_name)
    return {row[1] for row in typed_index_rows}


def _index_list_rows_for_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[SQLiteIndexListRow]:
    index_rows = connection.execute(
        f"PRAGMA index_list('{table_name}')",
    ).fetchall()
    return cast("list[SQLiteIndexListRow]", index_rows)


def _upgrade_to_head(db_path: Path) -> None:
    result = _run_alembic_command(db_path, ("upgrade", "head"))
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


def _run_alembic_command(
    db_path: Path,
    command_parts: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    alembic_executable = Path(sys.executable).with_name("alembic")
    if not alembic_executable.exists():
        raise AssertionError

    env = os.environ.copy()
    env["TCA_DB_PATH"] = db_path.as_posix()
    return subprocess.run(  # noqa: S603
        [alembic_executable.as_posix(), "-c", "alembic.ini", *command_parts],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
