"""Repository helpers for channel group CRUD and membership operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class ChannelGroupRecord:
    """Typed channel-group row payload."""

    id: int
    name: str
    description: str | None
    dedupe_horizon_minutes_override: int | None


@dataclass(slots=True, frozen=True)
class ChannelGroupMembershipRecord:
    """Typed channel-group membership payload."""

    group_id: int
    channel_id: int


class ChannelGroupsRepositoryError(RuntimeError):
    """Base exception for channel-groups repository operations."""


class ChannelAlreadyAssignedToGroupError(ChannelGroupsRepositoryError):
    """Raised when assigning a channel already assigned to a different group."""

    @classmethod
    def for_channel(
        cls,
        channel_id: int,
    ) -> ChannelAlreadyAssignedToGroupError:
        """Build deterministic duplicate-membership error for a channel."""
        return cls(f"Channel '{channel_id}' is already assigned to a group.")


class ChannelGroupDecodeError(ChannelGroupsRepositoryError):
    """Raised when repository row decoding encounters unexpected payload shape."""

    @classmethod
    def from_details(cls, *, details: str) -> ChannelGroupDecodeError:
        """Build deterministic decode failure message."""
        return cls(f"Channel group repository decode error: {details}")


class ChannelGroupsRepository:
    """CRUD helper for `channel_groups` and `channel_group_members`."""

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

    async def create_group(
        self,
        *,
        name: str,
        description: str | None,
        dedupe_horizon_minutes_override: int | None,
    ) -> ChannelGroupRecord:
        """Create a channel group and return created row payload."""
        statement = text(
            """
            INSERT INTO channel_groups (
                name,
                description,
                dedupe_horizon_minutes_override
            )
            VALUES (
                :name,
                :description,
                :dedupe_horizon_minutes_override
            )
            RETURNING id, name, description, dedupe_horizon_minutes_override
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "name": name,
                    "description": description,
                    "dedupe_horizon_minutes_override": dedupe_horizon_minutes_override,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_group_row(row)

    async def get_group_by_id(
        self,
        *,
        group_id: int,
    ) -> ChannelGroupRecord | None:
        """Fetch a group by id or return None when it does not exist."""
        statement = text(
            """
            SELECT id, name, description, dedupe_horizon_minutes_override
            FROM channel_groups
            WHERE id = :group_id
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"group_id": group_id})
            row = result.mappings().one_or_none()
        if row is None:
            return None
        return _decode_group_row(row)

    async def update_group(
        self,
        *,
        group_id: int,
        name: str,
        description: str | None,
        dedupe_horizon_minutes_override: int | None,
    ) -> ChannelGroupRecord | None:
        """Update an existing group and return payload, or None when missing."""
        statement = text(
            """
            UPDATE channel_groups
            SET name = :name,
                description = :description,
                dedupe_horizon_minutes_override = :dedupe_horizon_minutes_override,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :group_id
            RETURNING id, name, description, dedupe_horizon_minutes_override
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "group_id": group_id,
                    "name": name,
                    "description": description,
                    "dedupe_horizon_minutes_override": dedupe_horizon_minutes_override,
                },
            )
            row = result.mappings().one_or_none()
            await session.commit()
        if row is None:
            return None
        return _decode_group_row(row)

    async def delete_group(
        self,
        *,
        group_id: int,
    ) -> bool:
        """Delete group by id; return True when row existed and was removed."""
        statement = text(
            """
            DELETE FROM channel_groups
            WHERE id = :group_id
            RETURNING id
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(statement, {"group_id": group_id})
            deleted_id = result.scalar_one_or_none()
            await session.commit()
        return deleted_id is not None

    async def add_channel_membership(
        self,
        *,
        group_id: int,
        channel_id: int,
    ) -> ChannelGroupMembershipRecord:
        """Assign channel to group; one-group-per-channel rule is enforced."""
        statement = text(
            """
            INSERT INTO channel_group_members (group_id, channel_id)
            VALUES (:group_id, :channel_id)
            RETURNING group_id, channel_id
            """,
        )
        async with self._write_session_factory() as session:
            try:
                result = await session.execute(
                    statement,
                    {"group_id": group_id, "channel_id": channel_id},
                )
                row = result.mappings().one()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                if _is_duplicate_channel_assignment_integrity_error(exc=exc):
                    raise ChannelAlreadyAssignedToGroupError.for_channel(
                        channel_id,
                    ) from exc
                raise
        return _decode_membership_row(row)

    async def remove_channel_membership(
        self,
        *,
        group_id: int,
        channel_id: int,
    ) -> bool:
        """Remove channel membership from group; return True when removed."""
        statement = text(
            """
            DELETE FROM channel_group_members
            WHERE group_id = :group_id
              AND channel_id = :channel_id
            RETURNING group_id
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {"group_id": group_id, "channel_id": channel_id},
            )
            deleted_group_id = result.scalar_one_or_none()
            await session.commit()
        return deleted_group_id is not None

    async def get_membership_by_channel_id(
        self,
        *,
        channel_id: int,
    ) -> ChannelGroupMembershipRecord | None:
        """Fetch current membership for channel or None when unassigned."""
        statement = text(
            """
            SELECT group_id, channel_id
            FROM channel_group_members
            WHERE channel_id = :channel_id
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"channel_id": channel_id})
            row = result.mappings().one_or_none()
        if row is None:
            return None
        return _decode_membership_row(row)


def _decode_group_row(row: object) -> ChannelGroupRecord:
    """Decode SQL row mapping into `ChannelGroupRecord`."""
    row_map = cast("dict[str, object]", row)
    group_id_obj = row_map.get("id")
    name_obj = row_map.get("name")
    description_obj = row_map.get("description")
    horizon_obj = row_map.get("dedupe_horizon_minutes_override")

    if type(group_id_obj) is not int:
        raise ChannelGroupDecodeError.from_details(details="missing integer `id`")
    if not isinstance(name_obj, str):
        raise ChannelGroupDecodeError.from_details(details="missing text `name`")
    if description_obj is not None and not isinstance(description_obj, str):
        raise ChannelGroupDecodeError.from_details(
            details="`description` must be text or null",
        )
    if horizon_obj is not None and type(horizon_obj) is not int:
        raise ChannelGroupDecodeError.from_details(
            details="`dedupe_horizon_minutes_override` must be integer or null",
        )

    return ChannelGroupRecord(
        id=group_id_obj,
        name=name_obj,
        description=description_obj,
        dedupe_horizon_minutes_override=horizon_obj,
    )


def _decode_membership_row(row: object) -> ChannelGroupMembershipRecord:
    """Decode SQL row mapping into `ChannelGroupMembershipRecord`."""
    row_map = cast("dict[str, object]", row)
    group_id_obj = row_map.get("group_id")
    channel_id_obj = row_map.get("channel_id")

    if type(group_id_obj) is not int:
        raise ChannelGroupDecodeError.from_details(
            details="missing integer `group_id`",
        )
    if type(channel_id_obj) is not int:
        raise ChannelGroupDecodeError.from_details(
            details="missing integer `channel_id`",
        )

    return ChannelGroupMembershipRecord(
        group_id=group_id_obj,
        channel_id=channel_id_obj,
    )


def _is_duplicate_channel_assignment_integrity_error(*, exc: IntegrityError) -> bool:
    """Return True only for unique violation on `channel_group_members.channel_id`."""
    message = _normalized_integrity_message(exc=exc)
    if "uq_channel_group_members_channel_id" in message:
        return True
    return (
        "unique constraint failed" in message
        and "channel_group_members.channel_id" in message
    )


def _normalized_integrity_message(*, exc: IntegrityError) -> str:
    """Normalize SQLAlchemy/driver integrity error text for matching."""
    driver_error = cast("object | None", getattr(exc, "orig", None))
    message_parts = [str(exc)]
    if driver_error is not None:
        message_parts.append(str(driver_error))
    return " ".join(message_parts).lower()
