"""Repository helpers for dedupe cluster membership assignment."""

from __future__ import annotations

from dataclasses import dataclass
from json import dumps
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from sqlalchemy import bindparam, text

MIN_MERGE_CLUSTER_COUNT = 2

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class ClusterAssignmentResult:
    """Result payload for assigning one item to a dedupe cluster."""

    cluster_id: int
    created_cluster: bool
    created_membership: bool


@dataclass(slots=True, frozen=True)
class ClusterMergeResult:
    """Result payload for merging multiple dedupe clusters."""

    target_cluster_id: int
    source_cluster_ids: tuple[int, ...]
    moved_member_count: int
    removed_cluster_count: int
    recorded_event: bool


class DedupeClustersRepository:
    """Persistence flow for creating clusters and assigning cluster members."""

    _read_session_factory: SessionFactory
    _write_session_factory: SessionFactory

    def __init__(
        self,
        *,
        read_session_factory: SessionFactory,
        write_session_factory: SessionFactory,
    ) -> None:
        """Create repository with explicit read/write session dependencies."""
        self._read_session_factory = read_session_factory
        self._write_session_factory = write_session_factory

    async def assign_item_to_cluster(
        self,
        *,
        item_id: int,
        matched_cluster_id: int | None,
    ) -> ClusterAssignmentResult:
        """Assign item to an existing cluster or create a new one when unmatched."""
        cluster_id = matched_cluster_id
        created_cluster = False

        create_cluster_statement = text(
            """
            INSERT INTO dedupe_clusters (cluster_key, representative_item_id)
            VALUES (:cluster_key, :representative_item_id)
            RETURNING id
            """,
        )
        add_member_statement = text(
            """
            INSERT INTO dedupe_members (cluster_id, item_id)
            VALUES (:cluster_id, :item_id)
            ON CONFLICT(cluster_id, item_id) DO NOTHING
            RETURNING cluster_id
            """,
        )

        async with self._write_session_factory() as session:
            if cluster_id is None:
                cluster_row = (
                    (
                        await session.execute(
                            create_cluster_statement,
                            {
                                "cluster_key": str(uuid4()),
                                "representative_item_id": item_id,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                cluster_id = _coerce_int(value=cluster_row.get("id"), field="id")
                created_cluster = True

            membership_row = (
                (
                    await session.execute(
                        add_member_statement,
                        {"cluster_id": cluster_id, "item_id": item_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            await session.commit()

        return ClusterAssignmentResult(
            cluster_id=cluster_id,
            created_cluster=created_cluster,
            created_membership=membership_row is not None,
        )

    async def merge_clusters(
        self,
        *,
        matched_cluster_ids: list[int],
    ) -> ClusterMergeResult:
        """Merge matched clusters into the smallest cluster id target."""
        unique_cluster_ids = _normalize_cluster_ids(
            matched_cluster_ids=matched_cluster_ids,
        )
        if len(unique_cluster_ids) < MIN_MERGE_CLUSTER_COUNT:
            message = "need at least two clusters to merge"
            raise ValueError(message)

        target_cluster_id = unique_cluster_ids[0]
        source_cluster_ids = tuple(unique_cluster_ids[1:])

        count_source_members_statement = text(
            """
            SELECT COUNT(*) AS count
            FROM dedupe_members
            WHERE cluster_id IN :source_cluster_ids
            """,
        ).bindparams(
            bindparam("source_cluster_ids", expanding=True),
        )
        count_source_clusters_statement = text(
            """
            SELECT COUNT(*) AS count
            FROM dedupe_clusters
            WHERE id IN :source_cluster_ids
            """,
        ).bindparams(
            bindparam("source_cluster_ids", expanding=True),
        )
        move_members_statement = text(
            """
            INSERT INTO dedupe_members (cluster_id, item_id)
            SELECT :target_cluster_id, item_id
            FROM dedupe_members
            WHERE cluster_id IN :source_cluster_ids
            ON CONFLICT(cluster_id, item_id) DO NOTHING
            """,
        ).bindparams(
            bindparam("source_cluster_ids", expanding=True),
        )
        delete_source_clusters_statement = text(
            """
            DELETE FROM dedupe_clusters
            WHERE id IN :source_cluster_ids
            """,
        ).bindparams(
            bindparam("source_cluster_ids", expanding=True),
        )
        resolve_event_item_statement = text(
            """
            SELECT representative_item_id AS item_id
            FROM dedupe_clusters
            WHERE id = :target_cluster_id
            """,
        )
        resolve_fallback_item_statement = text(
            """
            SELECT item_id
            FROM dedupe_members
            WHERE cluster_id = :target_cluster_id
            ORDER BY item_id ASC
            LIMIT 1
            """,
        )
        insert_merge_event_statement = text(
            """
            INSERT INTO dedupe_decisions (
                item_id,
                cluster_id,
                candidate_item_id,
                strategy_name,
                outcome,
                reason_code,
                score,
                metadata_json
            )
            VALUES (
                :item_id,
                :cluster_id,
                NULL,
                'cluster_merge',
                'DUPLICATE',
                'cluster_merge',
                NULL,
                :metadata_json
            )
            RETURNING id
            """,
        )

        async with self._write_session_factory() as session:
            target_cluster_row = (
                (
                    await session.execute(
                        resolve_event_item_statement,
                        {"target_cluster_id": target_cluster_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if target_cluster_row is None:
                message = f"target cluster `{target_cluster_id}` does not exist"
                raise ValueError(message)

            source_member_row = (
                (
                    await session.execute(
                        count_source_members_statement,
                        {"source_cluster_ids": source_cluster_ids},
                    )
                )
                .mappings()
                .one()
            )
            moved_member_count = _coerce_int(
                value=source_member_row.get("count"),
                field="count",
            )

            source_cluster_row = (
                (
                    await session.execute(
                        count_source_clusters_statement,
                        {"source_cluster_ids": source_cluster_ids},
                    )
                )
                .mappings()
                .one()
            )
            removed_cluster_count = _coerce_int(
                value=source_cluster_row.get("count"),
                field="count",
            )

            if moved_member_count == 0 and removed_cluster_count == 0:
                return ClusterMergeResult(
                    target_cluster_id=target_cluster_id,
                    source_cluster_ids=source_cluster_ids,
                    moved_member_count=0,
                    removed_cluster_count=0,
                    recorded_event=False,
                )

            _ = await session.execute(
                move_members_statement,
                {
                    "target_cluster_id": target_cluster_id,
                    "source_cluster_ids": source_cluster_ids,
                },
            )

            _ = await session.execute(
                delete_source_clusters_statement,
                {"source_cluster_ids": source_cluster_ids},
            )

            event_item_id: object | None = None
            event_item_id = cast("object | None", target_cluster_row["item_id"])

            if event_item_id is None:
                fallback_item_row = (
                    (
                        await session.execute(
                            resolve_fallback_item_statement,
                            {"target_cluster_id": target_cluster_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if fallback_item_row is not None:
                    event_item_id = cast("object", fallback_item_row["item_id"])

            if event_item_id is None:
                message = "cannot record merge event without target cluster item"
                raise ValueError(message)

            merge_event_row = (
                (
                    await session.execute(
                        insert_merge_event_statement,
                        {
                            "item_id": _coerce_int(
                                value=event_item_id,
                                field="item_id",
                            ),
                            "cluster_id": target_cluster_id,
                            "metadata_json": dumps(
                                {
                                    "target_cluster_id": target_cluster_id,
                                    "source_cluster_ids": list(source_cluster_ids),
                                },
                                sort_keys=True,
                            ),
                        },
                    )
                )
                .mappings()
                .one()
            )
            await session.commit()

        return ClusterMergeResult(
            target_cluster_id=target_cluster_id,
            source_cluster_ids=source_cluster_ids,
            moved_member_count=moved_member_count,
            removed_cluster_count=removed_cluster_count,
            recorded_event=merge_event_row.get("id") is not None,
        )


def _coerce_int(*, value: object, field: str) -> int:
    if isinstance(value, bool):
        msg = f"missing integer `{field}`"
        raise TypeError(msg)
    if isinstance(value, int):
        return value
    msg = f"missing integer `{field}`"
    raise TypeError(msg)


def _normalize_cluster_ids(*, matched_cluster_ids: list[int]) -> list[int]:
    normalized: set[int] = set()
    for index, cluster_id in enumerate(matched_cluster_ids):
        normalized.add(
            _coerce_int(
                value=cluster_id,
                field=f"matched_cluster_ids[{index}]",
            ),
        )
    return sorted(normalized)
