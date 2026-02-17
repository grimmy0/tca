"""Repository helpers for channel CRUD and soft-delete state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

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
    ) -> ChannelRecord | None:
        """Update mutable channel fields and return row payload when found."""
        statement = text(
            """
            UPDATE telegram_channels
            SET name = :name,
                username = :username,
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
