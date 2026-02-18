"""Repository helpers for raw message upsert storage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from json import JSONDecodeError
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(slots=True, frozen=True)
class RawMessageRecord:
    """Typed raw_messages row payload with decoded JSON payload."""

    raw_message_id: int
    channel_id: int
    message_id: int
    payload: JSONValue
    created_at: datetime
    updated_at: datetime


class RawMessagesRepositoryError(RuntimeError):
    """Base exception for raw message repository operations."""


class RawMessagePayloadEncodeError(RawMessagesRepositoryError):
    """Raised when raw message payload cannot be encoded to JSON."""

    @classmethod
    def for_payload(cls, *, details: str) -> RawMessagePayloadEncodeError:
        """Build deterministic encode error message."""
        return cls(f"Raw message payload is not JSON-serializable: {details}")


class RawMessagePayloadDecodeError(RawMessagesRepositoryError):
    """Raised when stored payload_json cannot be decoded to JSON."""

    @classmethod
    def from_details(cls, *, details: str) -> RawMessagePayloadDecodeError:
        """Build deterministic decode error message."""
        return cls(f"Stored raw message payload is not valid JSON: {details}")


class RawMessagesRepository:
    """Upsert helper for raw message payload storage."""

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

    async def upsert_raw_message(
        self,
        *,
        channel_id: int,
        message_id: int,
        payload: JSONValue,
    ) -> RawMessageRecord:
        """Insert or update raw message payload for a channel message id."""
        payload_json = _encode_payload_json(payload=payload)
        statement = text(
            """
            INSERT INTO raw_messages (
                channel_id,
                message_id,
                payload_json
            )
            VALUES (
                :channel_id,
                :message_id,
                :payload_json
            )
            ON CONFLICT(channel_id, message_id)
            DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = CURRENT_TIMESTAMP
            RETURNING
                id,
                channel_id,
                message_id,
                payload_json,
                created_at,
                updated_at
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "payload_json": payload_json,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_raw_message_row(row)


def _encode_payload_json(*, payload: JSONValue) -> str:
    try:
        return json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RawMessagePayloadEncodeError.for_payload(details=str(exc)) from exc


def _decode_raw_message_row(row: object) -> RawMessageRecord:
    row_map = cast("dict[str, object]", row)
    raw_message_id = _coerce_int(value=row_map.get("id"), field="id")
    channel_id = _coerce_int(value=row_map.get("channel_id"), field="channel_id")
    message_id = _coerce_int(value=row_map.get("message_id"), field="message_id")
    payload_json = _coerce_str(value=row_map.get("payload_json"), field="payload_json")
    payload = _decode_payload_json(payload_json=payload_json)
    created_at = _coerce_datetime(value=row_map.get("created_at"), field="created_at")
    updated_at = _coerce_datetime(value=row_map.get("updated_at"), field="updated_at")
    return RawMessageRecord(
        raw_message_id=raw_message_id,
        channel_id=channel_id,
        message_id=message_id,
        payload=payload,
        created_at=created_at,
        updated_at=updated_at,
    )


def _decode_payload_json(*, payload_json: str) -> JSONValue:
    try:
        return cast("JSONValue", json.loads(payload_json))
    except JSONDecodeError as exc:
        raise RawMessagePayloadDecodeError.from_details(details=str(exc)) from exc


def _coerce_int(*, value: object, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise RawMessagePayloadDecodeError.from_details(
        details=f"missing integer `{field}`",
    )


def _coerce_str(*, value: object, field: str) -> str:
    if isinstance(value, str):
        return value
    raise RawMessagePayloadDecodeError.from_details(details=f"missing `{field}`")


def _coerce_datetime(*, value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise RawMessagePayloadDecodeError.from_details(
                details=f"invalid `{field}` value",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    raise RawMessagePayloadDecodeError.from_details(details=f"missing `{field}`")
