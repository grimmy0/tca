"""Repository helpers for ingest error capture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class IngestErrorRecord:
    """Typed ingest error payload for stored ingest failures."""

    error_id: int
    channel_id: int | None
    stage: str
    error_code: str
    error_message: str
    payload_ref: str | None
    created_at: datetime


class IngestErrorsRepositoryError(RuntimeError):
    """Base exception for ingest errors repository operations."""


class IngestErrorDecodeError(IngestErrorsRepositoryError):
    """Raised when ingest error rows cannot be decoded."""

    @classmethod
    def from_details(cls, *, details: str) -> IngestErrorDecodeError:
        """Build deterministic decode error message."""
        return cls(f"Ingest error payload invalid: {details}")


class IngestErrorsRepository:
    """Insert helper for ingest error rows."""

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

    async def create(
        self,
        *,
        channel_id: int | None,
        stage: str,
        error_code: str,
        error_message: str,
        payload_ref: str | None = None,
    ) -> IngestErrorRecord:
        """Insert a new ingest error row and return the stored payload."""
        statement = text(
            """
            INSERT INTO ingest_errors (
                channel_id,
                stage,
                error_code,
                error_message,
                payload_ref
            )
            VALUES (
                :channel_id,
                :stage,
                :error_code,
                :error_message,
                :payload_ref
            )
            RETURNING
                id,
                channel_id,
                stage,
                error_code,
                error_message,
                payload_ref,
                created_at
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "channel_id": channel_id,
                    "stage": stage,
                    "error_code": error_code,
                    "error_message": error_message,
                    "payload_ref": payload_ref,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_ingest_error_row(row)


def _decode_ingest_error_row(row: object) -> IngestErrorRecord:
    row_map = cast("Mapping[str, object]", row)
    error_id = _coerce_int(value=row_map.get("id"), field="id")
    channel_id = _coerce_optional_int(
        value=row_map.get("channel_id"),
        field="channel_id",
    )
    stage = _coerce_str(value=row_map.get("stage"), field="stage")
    error_code = _coerce_str(value=row_map.get("error_code"), field="error_code")
    error_message = _coerce_str(
        value=row_map.get("error_message"),
        field="error_message",
    )
    payload_ref = _coerce_optional_str(
        value=row_map.get("payload_ref"),
        field="payload_ref",
    )
    created_at = _coerce_datetime(value=row_map.get("created_at"))
    return IngestErrorRecord(
        error_id=error_id,
        channel_id=channel_id,
        stage=stage,
        error_code=error_code,
        error_message=error_message,
        payload_ref=payload_ref,
        created_at=created_at,
    )


def _coerce_int(*, value: object, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise IngestErrorDecodeError.from_details(details=f"missing integer `{field}`")


def _coerce_optional_int(*, value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise IngestErrorDecodeError.from_details(details=f"invalid `{field}` value")


def _coerce_str(*, value: object, field: str) -> str:
    if isinstance(value, str):
        return value
    raise IngestErrorDecodeError.from_details(details=f"missing `{field}`")


def _coerce_optional_str(*, value: object, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise IngestErrorDecodeError.from_details(details=f"invalid `{field}` value")


def _coerce_datetime(*, value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise IngestErrorDecodeError.from_details(
                details="invalid created_at value",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    raise IngestErrorDecodeError.from_details(details="missing created_at value")
