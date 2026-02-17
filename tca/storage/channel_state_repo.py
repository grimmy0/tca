"""Repository helpers for channel polling state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import TYPE_CHECKING, cast

from sqlalchemy import bindparam, text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class ChannelStateRecord:
    """Typed polling state payload for one channel."""

    channel_id: int
    cursor: ChannelCursor | None
    paused_until: datetime | None
    last_success_at: datetime | None


@dataclass(slots=True, frozen=True)
class ChannelCursor:
    """Typed cursor payload for channel ingest progress."""

    last_message_id: int | None
    next_offset_id: int | None
    last_polled_at: datetime


class ChannelStateRepositoryError(RuntimeError):
    """Base exception for channel polling state operations."""


class ChannelStateDecodeError(ChannelStateRepositoryError):
    """Raised when state rows cannot be decoded."""

    @classmethod
    def from_details(cls, *, details: str) -> ChannelStateDecodeError:
        """Build deterministic decode error message."""
        return cls(f"Channel state payload invalid: {details}")


class ChannelStateRepository:
    """Repository for reading and writing channel polling state."""

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

    async def get_state(
        self,
        *,
        channel_id: int,
    ) -> ChannelStateRecord | None:
        """Return polling state for a channel or None if missing."""
        statement = text(
            """
            SELECT channel_id, cursor_json, paused_until, last_success_at
            FROM channel_state
            WHERE channel_id = :channel_id
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"channel_id": channel_id})
            row = result.mappings().one_or_none()
        if row is None:
            return None
        return _decode_state_row(row)

    async def list_states_by_channel_ids(
        self,
        *,
        channel_ids: Sequence[int],
    ) -> dict[int, ChannelStateRecord]:
        """Return polling states keyed by channel id."""
        if not channel_ids:
            return {}
        statement = text(
            """
            SELECT channel_id, cursor_json, paused_until, last_success_at
            FROM channel_state
            WHERE channel_id IN :channel_ids
            """,
        ).bindparams(bindparam("channel_ids", expanding=True))
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"channel_ids": list(channel_ids)})
            rows = result.mappings().all()
        state_map: dict[int, ChannelStateRecord] = {}
        for row in rows:
            record = _decode_state_row(row)
            state_map[record.channel_id] = record
        return state_map

    async def upsert_state(
        self,
        *,
        channel_id: int,
        paused_until: datetime | None,
        last_success_at: datetime | None,
    ) -> ChannelStateRecord:
        """Insert or update polling state for a channel."""
        statement = text(
            """
            INSERT INTO channel_state (channel_id, paused_until, last_success_at)
            VALUES (:channel_id, :paused_until, :last_success_at)
            ON CONFLICT(channel_id) DO UPDATE SET
                paused_until = :paused_until,
                last_success_at = :last_success_at,
                updated_at = CURRENT_TIMESTAMP
            RETURNING channel_id, cursor_json, paused_until, last_success_at
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "channel_id": channel_id,
                    "paused_until": paused_until,
                    "last_success_at": last_success_at,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_state_row(row)

    async def update_pause(
        self,
        *,
        channel_id: int,
        paused_until: datetime | None,
    ) -> ChannelStateRecord:
        """Insert or update channel pause timestamp only."""
        statement = text(
            """
            INSERT INTO channel_state (channel_id, paused_until)
            VALUES (:channel_id, :paused_until)
            ON CONFLICT(channel_id) DO UPDATE SET
                paused_until = :paused_until,
                updated_at = CURRENT_TIMESTAMP
            RETURNING channel_id, cursor_json, paused_until, last_success_at
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "channel_id": channel_id,
                    "paused_until": paused_until,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_state_row(row)

    async def update_cursor(
        self,
        *,
        channel_id: int,
        cursor: ChannelCursor | None,
    ) -> ChannelStateRecord:
        """Insert or update cursor state without mutating pause/success fields."""
        cursor_json = _encode_cursor_json(cursor)
        statement = text(
            """
            INSERT INTO channel_state (channel_id, cursor_json)
            VALUES (:channel_id, :cursor_json)
            ON CONFLICT(channel_id) DO UPDATE SET
                cursor_json = :cursor_json,
                updated_at = CURRENT_TIMESTAMP
            RETURNING channel_id, cursor_json, paused_until, last_success_at
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "channel_id": channel_id,
                    "cursor_json": cursor_json,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_state_row(row)


def _decode_state_row(row: Mapping[str, object]) -> ChannelStateRecord:
    channel_id = _coerce_int(value=row.get("channel_id"))
    cursor = _decode_cursor_json(value=row.get("cursor_json"))
    paused_until = _coerce_optional_datetime(value=row.get("paused_until"))
    last_success_at = _coerce_optional_datetime(value=row.get("last_success_at"))
    return ChannelStateRecord(
        channel_id=channel_id,
        cursor=cursor,
        paused_until=paused_until,
        last_success_at=last_success_at,
    )


def _coerce_int(*, value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ChannelStateDecodeError.from_details(details="missing integer `channel_id`")


def _coerce_optional_datetime(*, value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ChannelStateDecodeError.from_details(
                details="invalid datetime value",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    raise ChannelStateDecodeError.from_details(details="invalid datetime value")


def _encode_cursor_json(cursor: ChannelCursor | None) -> str | None:
    if cursor is None:
        return None
    payload = {
        "last_message_id": cursor.last_message_id,
        "next_offset_id": cursor.next_offset_id,
        "last_polled_at": _format_cursor_timestamp(cursor.last_polled_at),
    }
    return json.dumps(payload, separators=(",", ":"), allow_nan=False)


def _decode_cursor_json(*, value: object) -> ChannelCursor | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ChannelStateDecodeError.from_details(
            details="missing text `cursor_json`",
        )
    try:
        decoded = json.loads(
            value,
            parse_constant=_raise_invalid_json_constant,
        )
    except (JSONDecodeError, ValueError) as exc:
        raise ChannelStateDecodeError.from_details(
            details=f"invalid cursor_json: {exc}",
        ) from exc
    if not isinstance(decoded, dict):
        raise ChannelStateDecodeError.from_details(details="cursor_json must be object")
    if "last_message_id" not in decoded:
        raise ChannelStateDecodeError.from_details(
            details="cursor_json missing `last_message_id`",
        )
    if "next_offset_id" not in decoded:
        raise ChannelStateDecodeError.from_details(
            details="cursor_json missing `next_offset_id`",
        )
    if "last_polled_at" not in decoded:
        raise ChannelStateDecodeError.from_details(
            details="cursor_json missing `last_polled_at`",
        )
    last_message_id = _coerce_optional_int(
        value=decoded.get("last_message_id"),
        field="last_message_id",
    )
    next_offset_id = _coerce_optional_int(
        value=decoded.get("next_offset_id"),
        field="next_offset_id",
    )
    last_polled_at = _coerce_cursor_datetime(
        value=decoded.get("last_polled_at"),
        field="last_polled_at",
    )
    return ChannelCursor(
        last_message_id=last_message_id,
        next_offset_id=next_offset_id,
        last_polled_at=last_polled_at,
    )


def _coerce_optional_int(*, value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ChannelStateDecodeError.from_details(
        details=f"cursor_json invalid `{field}`",
    )


def _coerce_cursor_datetime(*, value: object, field: str) -> datetime:
    if isinstance(value, str):
        try:
            return _parse_cursor_timestamp(value)
        except ValueError as exc:
            raise ChannelStateDecodeError.from_details(
                details=f"cursor_json invalid `{field}`",
            ) from exc
    raise ChannelStateDecodeError.from_details(
        details=f"cursor_json invalid `{field}`",
    )


def _parse_cursor_timestamp(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_cursor_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def _raise_invalid_json_constant(value: str) -> object:
    raise ValueError(f"invalid numeric constant '{value}'")
