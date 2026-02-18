"""Repository helpers for dedupe cluster membership assignment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import text

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class ClusterAssignmentResult:
    """Result payload for assigning one item to a dedupe cluster."""

    cluster_id: int
    created_cluster: bool
    created_membership: bool


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


def _coerce_int(*, value: object, field: str) -> int:
    if isinstance(value, bool):
        msg = f"missing integer `{field}`"
        raise TypeError(msg)
    if isinstance(value, int):
        return value
    msg = f"missing integer `{field}`"
    raise TypeError(msg)
