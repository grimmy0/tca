"""Repository helpers for channel CRUD and soft-delete state transitions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import bindparam, text

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class ChannelRecord:
    """Typed `telegram_channels` row payload."""

    id: int
    account_id: int
    telegram_channel_id: int
    name: str
    username: str | None
    is_enabled: bool


class ChannelsRepositoryError(RuntimeError):
    """Base exception for channels repository operations."""


class ChannelDecodeError(ChannelsRepositoryError):
    """Raised when repository row decoding encounters unexpected payload shape."""

    @classmethod
    def from_details(cls, *, details: str) -> ChannelDecodeError:
        """Build deterministic decode failure message."""
        return cls(f"Channels repository decode error: {details}")


class ChannelsRepository:
    """CRUD helper for `telegram_channels` with soft-delete support."""

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

    async def create_channel(
        self,
        *,
        account_id: int,
        telegram_channel_id: int,
        name: str,
        username: str | None,
    ) -> ChannelRecord:
        """Create a channel row and return stored payload."""
        statement = text(
            """
            INSERT INTO telegram_channels (
                account_id,
                telegram_channel_id,
                name,
                username,
                is_enabled
            )
            VALUES (
                :account_id,
                :telegram_channel_id,
                :name,
                :username,
                1
            )
            RETURNING
                id,
                account_id,
                telegram_channel_id,
                name,
                username,
                is_enabled
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "account_id": account_id,
                    "telegram_channel_id": telegram_channel_id,
                    "name": name,
                    "username": username,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_channel_row(row)

    async def get_channel_by_id(
        self,
        *,
        channel_id: int,
    ) -> ChannelRecord | None:
        """Fetch a channel by id or return None when it does not exist."""
        statement = text(
            """
            SELECT
                id,
                account_id,
                telegram_channel_id,
                name,
                username,
                is_enabled
            FROM telegram_channels
            WHERE id = :channel_id
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"channel_id": channel_id})
            row = result.mappings().one_or_none()
        if row is None:
            return None
        return _decode_channel_row(row)

    async def update_channel(
        self,
        *,
        channel_id: int,
        name: str,
        username: str | None,
        is_enabled: bool,
    ) -> ChannelRecord | None:
        """Update mutable channel fields and return row payload when found."""
        statement = text(
            """
            UPDATE telegram_channels
            SET name = :name,
                username = :username,
                is_enabled = :is_enabled,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :channel_id
            RETURNING
                id,
                account_id,
                telegram_channel_id,
                name,
                username,
                is_enabled
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "channel_id": channel_id,
                    "name": name,
                    "username": username,
                    "is_enabled": is_enabled,
                },
            )
            row = result.mappings().one_or_none()
            await session.commit()
        if row is None:
            return None
        return _decode_channel_row(row)

    async def disable_channel(
        self,
        *,
        channel_id: int,
    ) -> ChannelRecord | None:
        """Soft-delete channel by disabling it without deleting row."""
        return await self._set_enabled_state(channel_id=channel_id, is_enabled=False)

    async def enable_channel(
        self,
        *,
        channel_id: int,
    ) -> ChannelRecord | None:
        """Re-enable a previously disabled channel row."""
        return await self._set_enabled_state(channel_id=channel_id, is_enabled=True)

    async def purge_channel(
        self,
        *,
        channel_id: int,
    ) -> ChannelRecord | None:
        """Hard-delete a channel and cleanup related dedupe state."""
        fetch_statement = text(
            """
            SELECT
                id,
                account_id,
                telegram_channel_id,
                name,
                username,
                is_enabled
            FROM telegram_channels
            WHERE id = :channel_id
            """,
        )
        cluster_statement = text(
            """
            SELECT DISTINCT members.cluster_id
            FROM dedupe_members AS members
            INNER JOIN items
                ON items.id = members.item_id
            WHERE items.channel_id = :channel_id
            """,
        )
        item_count_statement = text(
            """
            SELECT COUNT(*)
            FROM items
            WHERE channel_id = :channel_id
            """,
        )
        raw_count_statement = text(
            """
            SELECT COUNT(*)
            FROM raw_messages
            WHERE channel_id = :channel_id
            """,
        )
        delete_statement = text(
            """
            DELETE FROM telegram_channels
            WHERE id = :channel_id
            """,
        )
        recompute_statement = text(
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
        delete_empty_statement = text(
            """
            DELETE FROM dedupe_clusters
            WHERE id IN :cluster_ids
              AND NOT EXISTS (
                    SELECT 1
                    FROM dedupe_members
                    WHERE dedupe_members.cluster_id = dedupe_clusters.id
              )
            """,
        ).bindparams(bindparam("cluster_ids", expanding=True))
        audit_statement = text(
            """
            INSERT INTO notifications (type, severity, message, payload_json)
            VALUES (:type, :severity, :message, :payload_json)
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                fetch_statement,
                {"channel_id": channel_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                await session.rollback()
                return None
            channel = _decode_channel_row(row)

            cluster_result = await session.execute(
                cluster_statement,
                {"channel_id": channel_id},
            )
            cluster_rows = cast("list[object]", cluster_result.scalars().all())
            cluster_ids = [
                _coerce_count_int(value=value, field="cluster_id")
                for value in cluster_rows
            ]

            item_result = await session.execute(
                item_count_statement,
                {"channel_id": channel_id},
            )
            item_count = _coerce_count_int(
                value=cast("object", item_result.scalar_one()),
                field="item_count",
            )

            raw_result = await session.execute(
                raw_count_statement,
                {"channel_id": channel_id},
            )
            raw_count = _coerce_count_int(
                value=cast("object", raw_result.scalar_one()),
                field="raw_count",
            )

            _ = await session.execute(
                delete_statement,
                {"channel_id": channel_id},
            )

            if cluster_ids:
                _ = await session.execute(
                    recompute_statement,
                    {"cluster_ids": cluster_ids},
                )
                _ = await session.execute(
                    delete_empty_statement,
                    {"cluster_ids": cluster_ids},
                )

            payload = json.dumps(
                {
                    "channel_id": channel_id,
                    "channel_name": channel.name,
                    "deleted_items": item_count,
                    "deleted_raw_messages": raw_count,
                    "affected_cluster_ids": cluster_ids,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            _ = await session.execute(
                audit_statement,
                {
                    "type": "channel_purged",
                    "severity": "low",
                    "message": f"Channel {channel_id} purged.",
                    "payload_json": payload,
                },
            )
            await session.commit()
        return channel

    async def list_active_channels(
        self,
        *,
        account_id: int | None = None,
    ) -> list[ChannelRecord]:
        """List enabled channels, optionally scoped to one account."""
        if account_id is None:
            statement = text(
                """
                SELECT
                    id,
                    account_id,
                    telegram_channel_id,
                    name,
                    username,
                    is_enabled
                FROM telegram_channels
                WHERE is_enabled = 1
                ORDER BY id ASC
                """,
            )
            params: dict[str, object] = {}
        else:
            statement = text(
                """
                SELECT
                    id,
                    account_id,
                    telegram_channel_id,
                    name,
                    username,
                    is_enabled
                FROM telegram_channels
                WHERE is_enabled = 1
                  AND account_id = :account_id
                ORDER BY id ASC
                """,
            )
            params = {"account_id": account_id}
        async with self._read_session_factory() as session:
            result = await session.execute(statement, params)
            rows = result.mappings().all()
        return [_decode_channel_row(row) for row in rows]

    async def list_schedulable_channels(self) -> list[ChannelRecord]:
        """List enabled channels for accounts that are not paused."""
        statement = text(
            """
            SELECT
                channels.id,
                channels.account_id,
                channels.telegram_channel_id,
                channels.name,
                channels.username,
                channels.is_enabled
            FROM telegram_channels AS channels
            INNER JOIN telegram_accounts AS accounts
                ON accounts.id = channels.account_id
            WHERE channels.is_enabled = 1
              AND accounts.paused_at IS NULL
            ORDER BY channels.id ASC
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement)
            rows = result.mappings().all()
        return [_decode_channel_row(row) for row in rows]

    async def _set_enabled_state(
        self,
        *,
        channel_id: int,
        is_enabled: bool,
    ) -> ChannelRecord | None:
        """Toggle enabled state and return updated row when present."""
        statement = text(
            """
            UPDATE telegram_channels
            SET is_enabled = :is_enabled,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :channel_id
            RETURNING
                id,
                account_id,
                telegram_channel_id,
                name,
                username,
                is_enabled
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "channel_id": channel_id,
                    "is_enabled": is_enabled,
                },
            )
            row = result.mappings().one_or_none()
            await session.commit()
        if row is None:
            return None
        return _decode_channel_row(row)


def _decode_channel_row(row: object) -> ChannelRecord:
    """Decode SQL row mapping into `ChannelRecord`."""
    row_map = cast("dict[str, object]", row)
    channel_id_obj = row_map.get("id")
    account_id_obj = row_map.get("account_id")
    telegram_channel_id_obj = row_map.get("telegram_channel_id")
    name_obj = row_map.get("name")
    username_obj = row_map.get("username")
    is_enabled_obj = row_map.get("is_enabled")

    if type(channel_id_obj) is not int:
        raise ChannelDecodeError.from_details(details="missing integer `id`")
    if type(account_id_obj) is not int:
        raise ChannelDecodeError.from_details(details="missing integer `account_id`")
    if type(telegram_channel_id_obj) is not int:
        raise ChannelDecodeError.from_details(
            details="missing integer `telegram_channel_id`",
        )
    if not isinstance(name_obj, str):
        raise ChannelDecodeError.from_details(details="missing text `name`")
    if username_obj is not None and not isinstance(username_obj, str):
        raise ChannelDecodeError.from_details(details="`username` must be text or null")
    is_enabled = _decode_is_enabled(value=is_enabled_obj)
    return ChannelRecord(
        id=channel_id_obj,
        account_id=account_id_obj,
        telegram_channel_id=telegram_channel_id_obj,
        name=name_obj,
        username=username_obj,
        is_enabled=is_enabled,
    )


def _decode_is_enabled(*, value: object) -> bool:
    """Decode SQLite boolean payload from SQL row mappings."""
    if isinstance(value, bool):
        return value
    if type(value) is int and value in {0, 1}:
        return bool(value)
    raise ChannelDecodeError.from_details(details="`is_enabled` must be boolean")


def _coerce_count_int(*, value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ChannelDecodeError.from_details(details=f"invalid `{field}` value")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ChannelDecodeError.from_details(details=f"invalid `{field}` value")
