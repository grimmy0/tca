"""Ordered daily retention prune job implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from sqlalchemy import bindparam, text

from tca.storage import SettingsRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from tca.storage.db import SessionFactory

DELETE_BATCH_SIZE = 500

RAW_MESSAGES_RETENTION_DAYS_KEY = "retention.raw_messages_days"
ITEMS_RETENTION_DAYS_KEY = "retention.items_days"
INGEST_ERRORS_RETENTION_DAYS_KEY = "retention.ingest_errors_days"

DEFAULT_RAW_MESSAGES_RETENTION_DAYS = 30
DEFAULT_ITEMS_RETENTION_DAYS = 365
DEFAULT_INGEST_ERRORS_RETENTION_DAYS = 90

STEP_DELETE_EXPIRED_RAW_MESSAGES = "delete_expired_raw_messages"
STEP_DELETE_EXPIRED_ITEMS = "delete_expired_items"
STEP_RECOMPUTE_CLUSTER_REPRESENTATIVES = "recompute_cluster_representatives"
STEP_DELETE_EMPTY_CLUSTERS = "delete_empty_clusters"
STEP_DELETE_ORPHANED_ROWS = "delete_orphaned_rows"
STEP_DELETE_EXPIRED_INGEST_ERRORS = "delete_expired_ingest_errors"


@dataclass(slots=True, frozen=True)
class RetentionPruneSettings:
    """Resolved runtime retention settings for prune job execution."""

    raw_messages_days: int
    items_days: int
    ingest_errors_days: int


@dataclass(slots=True, frozen=True)
class PruneJobRunSummary:
    """Result details for one prune job execution."""

    executed_steps: tuple[str, ...]
    raw_messages_deleted: int
    items_deleted: int
    raw_message_batch_sizes: tuple[int, ...]
    item_batch_sizes: tuple[int, ...]
    recomputed_cluster_count: int
    deleted_empty_cluster_count: int
    orphaned_members_deleted: int
    orphaned_decisions_deleted: int
    ingest_errors_deleted: int
    affected_cluster_ids: tuple[int, ...]


class OrderedRetentionPruneJob:
    """Run the retention prune routine in the design-mandated order."""

    _read_session_factory: SessionFactory
    _write_session_factory: SessionFactory
    _now_provider: Callable[[], datetime]

    def __init__(
        self,
        *,
        read_session_factory: SessionFactory,
        write_session_factory: SessionFactory,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """Create prune job with storage dependencies and optional clock."""
        self._read_session_factory = read_session_factory
        self._write_session_factory = write_session_factory
        self._now_provider = now_provider or _utc_now

    async def run_once(self) -> PruneJobRunSummary:
        """Execute all six prune steps in required deterministic order."""
        settings = await self._resolve_settings()
        now = _normalize_datetime(self._now_provider())
        executed_steps: list[str] = []

        raw_messages_cutoff = now - timedelta(days=settings.raw_messages_days)
        items_cutoff = now - timedelta(days=settings.items_days)
        ingest_errors_cutoff = now - timedelta(days=settings.ingest_errors_days)

        async with self._write_session_factory() as session:
            (
                raw_messages_deleted,
                raw_message_batch_sizes,
            ) = await _delete_expired_raw_messages_in_batches(
                session=session,
                cutoff=raw_messages_cutoff,
            )
            executed_steps.append(STEP_DELETE_EXPIRED_RAW_MESSAGES)

            (
                items_deleted,
                item_batch_sizes,
                affected_cluster_ids,
            ) = await _delete_expired_items_in_batches(
                session=session,
                cutoff=items_cutoff,
            )
            executed_steps.append(STEP_DELETE_EXPIRED_ITEMS)

            recomputed_cluster_count = await _recompute_cluster_representatives(
                session=session,
                cluster_ids=affected_cluster_ids,
            )
            executed_steps.append(STEP_RECOMPUTE_CLUSTER_REPRESENTATIVES)

            deleted_empty_cluster_count = await _delete_empty_clusters(
                session=session,
                cluster_ids=affected_cluster_ids,
            )
            executed_steps.append(STEP_DELETE_EMPTY_CLUSTERS)

            (
                orphaned_members_deleted,
                orphaned_decisions_deleted,
            ) = await _delete_orphaned_rows(session=session)
            executed_steps.append(STEP_DELETE_ORPHANED_ROWS)

            ingest_errors_deleted = await _delete_expired_ingest_errors(
                session=session,
                cutoff=ingest_errors_cutoff,
            )
            executed_steps.append(STEP_DELETE_EXPIRED_INGEST_ERRORS)
            await session.commit()

        return PruneJobRunSummary(
            executed_steps=tuple(executed_steps),
            raw_messages_deleted=raw_messages_deleted,
            items_deleted=items_deleted,
            raw_message_batch_sizes=tuple(raw_message_batch_sizes),
            item_batch_sizes=tuple(item_batch_sizes),
            recomputed_cluster_count=recomputed_cluster_count,
            deleted_empty_cluster_count=deleted_empty_cluster_count,
            orphaned_members_deleted=orphaned_members_deleted,
            orphaned_decisions_deleted=orphaned_decisions_deleted,
            ingest_errors_deleted=ingest_errors_deleted,
            affected_cluster_ids=tuple(sorted(affected_cluster_ids)),
        )

    async def _resolve_settings(self) -> RetentionPruneSettings:
        repository = SettingsRepository(
            read_session_factory=self._read_session_factory,
            write_session_factory=self._write_session_factory,
        )
        raw_messages_days = await _resolve_positive_days_setting(
            repository=repository,
            key=RAW_MESSAGES_RETENTION_DAYS_KEY,
            default_value=DEFAULT_RAW_MESSAGES_RETENTION_DAYS,
            allow_zero=False,
        )
        items_days = await _resolve_positive_days_setting(
            repository=repository,
            key=ITEMS_RETENTION_DAYS_KEY,
            default_value=DEFAULT_ITEMS_RETENTION_DAYS,
            allow_zero=True,
        )
        ingest_errors_days = await _resolve_positive_days_setting(
            repository=repository,
            key=INGEST_ERRORS_RETENTION_DAYS_KEY,
            default_value=DEFAULT_INGEST_ERRORS_RETENTION_DAYS,
            allow_zero=False,
        )
        return RetentionPruneSettings(
            raw_messages_days=raw_messages_days,
            items_days=items_days,
            ingest_errors_days=ingest_errors_days,
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def _resolve_positive_days_setting(
    *,
    repository: SettingsRepository,
    key: str,
    default_value: int,
    allow_zero: bool,
) -> int:
    record = await repository.get_by_key(key=key)
    if record is None:
        return default_value
    value = record.value
    if isinstance(value, bool):
        return default_value
    if isinstance(value, int):
        return _resolve_days_numeric(
            value=value,
            allow_zero=allow_zero,
            default_value=default_value,
        )
    if isinstance(value, float):
        if not value.is_integer():
            return default_value
        return _resolve_days_numeric(
            value=int(value),
            allow_zero=allow_zero,
            default_value=default_value,
        )
    return default_value


def _resolve_days_numeric(
    *,
    value: int,
    allow_zero: bool,
    default_value: int,
) -> int:
    if value > 0:
        return value
    if allow_zero and value == 0:
        return 0
    return default_value


async def _delete_expired_raw_messages_in_batches(
    *,
    session: AsyncSession,
    cutoff: datetime,
) -> tuple[int, list[int]]:
    select_ids_statement = text(
        """
        SELECT id
        FROM raw_messages
        WHERE created_at < :cutoff
        ORDER BY id ASC
        LIMIT :batch_size
        """,
    )
    delete_statement = text(
        """
        DELETE FROM raw_messages
        WHERE id IN :raw_message_ids
        RETURNING id
        """,
    ).bindparams(bindparam("raw_message_ids", expanding=True))
    deleted_total = 0
    batch_sizes: list[int] = []
    while True:
        batch_result = await session.execute(
            select_ids_statement,
            {"cutoff": cutoff, "batch_size": DELETE_BATCH_SIZE},
        )
        raw_message_rows = cast("list[object]", batch_result.scalars().all())
        raw_message_ids = [
            _coerce_row_int(value=row, field="raw_message_id")
            for row in raw_message_rows
        ]
        if not raw_message_ids:
            break
        delete_result = await session.execute(
            delete_statement,
            {"raw_message_ids": raw_message_ids},
        )
        deleted_count = len(delete_result.scalars().all())
        deleted_total += deleted_count
        batch_sizes.append(deleted_count)
    return deleted_total, batch_sizes


async def _delete_expired_items_in_batches(
    *,
    session: AsyncSession,
    cutoff: datetime,
) -> tuple[int, list[int], set[int]]:
    select_ids_statement = text(
        """
        SELECT id
        FROM items
        WHERE created_at < :cutoff
        ORDER BY id ASC
        LIMIT :batch_size
        """,
    )
    select_clusters_statement = text(
        """
        SELECT DISTINCT cluster_id
        FROM dedupe_members
        WHERE item_id IN :item_ids
        """,
    ).bindparams(bindparam("item_ids", expanding=True))
    delete_statement = text(
        """
        DELETE FROM items
        WHERE id IN :item_ids
        RETURNING id
        """,
    ).bindparams(bindparam("item_ids", expanding=True))
    deleted_total = 0
    batch_sizes: list[int] = []
    affected_cluster_ids: set[int] = set()
    while True:
        batch_result = await session.execute(
            select_ids_statement,
            {"cutoff": cutoff, "batch_size": DELETE_BATCH_SIZE},
        )
        item_rows = cast("list[object]", batch_result.scalars().all())
        item_ids = [_coerce_row_int(value=row, field="item_id") for row in item_rows]
        if not item_ids:
            break
        cluster_result = await session.execute(
            select_clusters_statement,
            {"item_ids": item_ids},
        )
        cluster_rows = cast("list[object]", cluster_result.scalars().all())
        for cluster_id in cluster_rows:
            affected_cluster_ids.add(
                _coerce_row_int(value=cluster_id, field="cluster_id"),
            )
        delete_result = await session.execute(
            delete_statement,
            {"item_ids": item_ids},
        )
        deleted_count = len(delete_result.scalars().all())
        deleted_total += deleted_count
        batch_sizes.append(deleted_count)
    return deleted_total, batch_sizes, affected_cluster_ids


async def _recompute_cluster_representatives(
    *,
    session: AsyncSession,
    cluster_ids: set[int],
) -> int:
    if not cluster_ids:
        return 0
    statement = text(
        """
        WITH ranked AS (
            SELECT
                members.cluster_id AS cluster_id,
                items.id AS item_id,
                ROW_NUMBER() OVER (
                    PARTITION BY members.cluster_id
                    ORDER BY
                        CASE
                            WHEN COALESCE(items.canonical_url, '') != '' THEN 0
                            ELSE 1
                        END,
                        (
                            COALESCE(LENGTH(items.title), 0)
                            + COALESCE(LENGTH(items.body), 0)
                        ) DESC,
                        CASE
                            WHEN items.published_at IS NULL THEN 1
                            ELSE 0
                        END,
                        items.published_at ASC,
                        items.id ASC
                ) AS row_rank
            FROM dedupe_members AS members
            INNER JOIN items
                ON items.id = members.item_id
            WHERE members.cluster_id IN :cluster_ids
        )
        UPDATE dedupe_clusters
        SET representative_item_id = (
            SELECT ranked.item_id
            FROM ranked
            WHERE ranked.cluster_id = dedupe_clusters.id
              AND ranked.row_rank = 1
        )
        WHERE id IN :cluster_ids
        """,
    ).bindparams(bindparam("cluster_ids", expanding=True))
    _ = await session.execute(statement, {"cluster_ids": sorted(cluster_ids)})
    return len(cluster_ids)


async def _delete_empty_clusters(
    *,
    session: AsyncSession,
    cluster_ids: set[int],
) -> int:
    if not cluster_ids:
        return 0
    statement = text(
        """
        DELETE FROM dedupe_clusters
        WHERE id IN :cluster_ids
          AND NOT EXISTS (
                SELECT 1
                FROM dedupe_members
                WHERE dedupe_members.cluster_id = dedupe_clusters.id
          )
        RETURNING id
        """,
    ).bindparams(bindparam("cluster_ids", expanding=True))
    result = await session.execute(
        statement,
        {"cluster_ids": sorted(cluster_ids)},
    )
    return len(result.scalars().all())


async def _delete_orphaned_rows(*, session: AsyncSession) -> tuple[int, int]:
    delete_members_statement = text(
        """
        DELETE FROM dedupe_members
        WHERE NOT EXISTS (
                SELECT 1
                FROM dedupe_clusters
                WHERE dedupe_clusters.id = dedupe_members.cluster_id
        )
           OR NOT EXISTS (
                SELECT 1
                FROM items
                WHERE items.id = dedupe_members.item_id
        )
        RETURNING cluster_id
        """,
    )
    delete_decisions_statement = text(
        """
        DELETE FROM dedupe_decisions
        WHERE NOT EXISTS (
                SELECT 1
                FROM items
                WHERE items.id = dedupe_decisions.item_id
        )
           OR (
                dedupe_decisions.cluster_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM dedupe_clusters
                    WHERE dedupe_clusters.id = dedupe_decisions.cluster_id
                )
           )
           OR (
                dedupe_decisions.candidate_item_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM items
                    WHERE items.id = dedupe_decisions.candidate_item_id
                )
           )
        RETURNING id
        """,
    )
    orphaned_members_result = await session.execute(delete_members_statement)
    orphaned_members_deleted = len(orphaned_members_result.scalars().all())
    orphaned_decisions_result = await session.execute(delete_decisions_statement)
    orphaned_decisions_deleted = len(orphaned_decisions_result.scalars().all())
    return orphaned_members_deleted, orphaned_decisions_deleted


async def _delete_expired_ingest_errors(
    *,
    session: AsyncSession,
    cutoff: datetime,
) -> int:
    statement = text(
        """
        DELETE FROM ingest_errors
        WHERE created_at < :cutoff
        RETURNING id
        """,
    )
    result = await session.execute(statement, {"cutoff": cutoff})
    return len(result.scalars().all())


def _coerce_row_int(*, value: object, field: str) -> int:
    if isinstance(value, bool):
        message = f"expected integer `{field}`"
        raise TypeError(message)
    if isinstance(value, int):
        return value
    message = f"expected integer `{field}`"
    raise TypeError(message)
