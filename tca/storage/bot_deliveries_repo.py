"""Repository helpers for tracking bot deliveries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class BotDeliveryRecord:
    """Record of a sent telegram bot delivery."""

    delivery_id: int
    cluster_id: int
    delivered_at: datetime
    telegram_message_id: str | None


@dataclass(slots=True, frozen=True)
class BotDeliveryEntryRecord:
    """An undelivered cluster entry with representative item metadata."""

    cluster_id: int
    representative_title: str | None
    representative_body: str | None
    representative_canonical_url: str | None
    representative_published_at: datetime | None
    channel_name: str
    channel_username: str | None
    duplicate_count: int


class BotDeliveriesRepositoryError(RuntimeError):
    """Base exception for bot deliveries repository operations."""


class BotDeliveryAlreadyExistsError(BotDeliveriesRepositoryError):
    """Raised when trying to log a delivery for an already delivered cluster."""

    @classmethod
    def for_cluster(cls, cluster_id: int) -> BotDeliveryAlreadyExistsError:
        """Build deterministic duplicate cluster error."""
        return cls(f"Delivery already recorded for cluster_id {cluster_id}")


class BotDeliveryDecodeError(BotDeliveriesRepositoryError):
    """Raised when database rows cannot be decoded."""

    @classmethod
    def from_details(cls, *, details: str) -> BotDeliveryDecodeError:
        """Build decode error with details."""
        return cls(f"Bot delivery decode error: {details}")


class BotDeliveriesRepository:
    """CRUD repository for tracking cluster deliveries via Telegram."""

    _read_session_factory: SessionFactory
    _write_session_factory: SessionFactory

    def __init__(
        self,
        *,
        read_session_factory: SessionFactory,
        write_session_factory: SessionFactory,
    ) -> None:
        """Create repository with read/write session factories."""
        self._read_session_factory = read_session_factory
        self._write_session_factory = write_session_factory

    async def record_delivery(
        self,
        *,
        cluster_id: int,
        telegram_message_id: str | None = None,
    ) -> BotDeliveryRecord:
        """Record a successful delivery for a cluster, raising on duplicate."""
        statement = text(
            """
            INSERT INTO bot_deliveries (cluster_id, telegram_message_id)
            VALUES (:cluster_id, :telegram_message_id)
            RETURNING id, cluster_id, delivered_at, telegram_message_id
            """,
        )
        async with self._write_session_factory() as session:
            try:
                result = await session.execute(
                    statement,
                    {
                        "cluster_id": cluster_id,
                        "telegram_message_id": telegram_message_id,
                    },
                )
                row = result.mappings().one()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                if _is_duplicate_cluster_integrity_error(exc=exc):
                    raise BotDeliveryAlreadyExistsError.for_cluster(cluster_id) from exc
                raise
        return _decode_delivery_row(row)

    async def list_undelivered_entries(
        self,
        *,
        limit: int,
    ) -> list[BotDeliveryEntryRecord]:
        """Fetch oldest undelivered clusters ready to format and send."""
        statement = text(
            """
            SELECT
                clusters.id AS cluster_id,
                representative.title AS representative_title,
                representative.body AS representative_body,
                representative.canonical_url AS representative_canonical_url,
                representative.published_at AS representative_published_at,
                representative_channel.name AS channel_name,
                representative_channel.username AS channel_username,
                COUNT(members.item_id) AS duplicate_count
            FROM dedupe_clusters AS clusters
            INNER JOIN items AS representative
                ON representative.id = clusters.representative_item_id
            INNER JOIN telegram_channels AS representative_channel
                ON representative_channel.id = representative.channel_id
            LEFT JOIN dedupe_members AS members
                ON members.cluster_id = clusters.id
            LEFT JOIN bot_deliveries AS deliveries
                ON deliveries.cluster_id = clusters.id
            WHERE deliveries.id IS NULL
            GROUP BY
                clusters.id,
                representative.title,
                representative.body,
                representative.canonical_url,
                representative.published_at,
                representative_channel.name,
                representative_channel.username
            ORDER BY clusters.id ASC
            LIMIT :limit
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"limit": limit})
            rows = result.mappings().all()
        return [_decode_entry_row(row) for row in rows]

    async def has_been_delivered(self, cluster_id: int) -> bool:
        """Check if a cluster has already been delivered."""
        statement = text(
            """
            SELECT 1
            FROM bot_deliveries
            WHERE cluster_id = :cluster_id
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"cluster_id": cluster_id})
            row = result.fetchone()
        return row is not None


def _decode_delivery_row(row: object) -> BotDeliveryRecord:
    row_map = cast("Mapping[str, object]", row)
    delivery_id = _coerce_int(value=row_map.get("id"), field="id")
    cluster_id = _coerce_int(value=row_map.get("cluster_id"), field="cluster_id")
    delivered_at = _coerce_datetime(value=row_map.get("delivered_at"))
    telegram_message_id = _coerce_optional_str(
        value=row_map.get("telegram_message_id"),
    )
    return BotDeliveryRecord(
        delivery_id=delivery_id,
        cluster_id=cluster_id,
        delivered_at=delivered_at,
        telegram_message_id=telegram_message_id,
    )


def _decode_entry_row(row: object) -> BotDeliveryEntryRecord:
    row_map = cast("Mapping[str, object]", row)
    cluster_id = _coerce_int(value=row_map.get("cluster_id"), field="cluster_id")
    representative_title = _coerce_optional_str(
        value=row_map.get("representative_title"),
    )
    representative_body = _coerce_optional_str(
        value=row_map.get("representative_body"),
    )
    representative_canonical_url = _coerce_optional_str(
        value=row_map.get("representative_canonical_url"),
    )
    representative_published_at = _coerce_optional_datetime(
        value=row_map.get("representative_published_at"),
        field="representative_published_at",
    )
    channel_name = _coerce_str(value=row_map.get("channel_name"), field="channel_name")
    channel_username = _coerce_optional_str(value=row_map.get("channel_username"))
    duplicate_count = _coerce_int(
        value=row_map.get("duplicate_count"),
        field="duplicate_count",
    )
    return BotDeliveryEntryRecord(
        cluster_id=cluster_id,
        representative_title=representative_title,
        representative_body=representative_body,
        representative_canonical_url=representative_canonical_url,
        representative_published_at=representative_published_at,
        channel_name=channel_name,
        channel_username=channel_username,
        duplicate_count=duplicate_count,
    )


def _coerce_int(*, value: object, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise BotDeliveryDecodeError.from_details(details=f"missing integer `{field}`")


def _coerce_str(*, value: object, field: str) -> str:
    if isinstance(value, str):
        return value
    raise BotDeliveryDecodeError.from_details(details=f"missing `{field}`")


def _coerce_optional_str(*, value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise BotDeliveryDecodeError.from_details(
        details="invalid string value",
    )


def _coerce_datetime(*, value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise BotDeliveryDecodeError.from_details(
                details="invalid datetime format",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    raise BotDeliveryDecodeError.from_details(details="missing datetime value")


def _coerce_optional_datetime(*, value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise BotDeliveryDecodeError.from_details(
                details=f"invalid datetime format for `{field}`",
            ) from exc
    else:
        raise BotDeliveryDecodeError.from_details(
            details=f"invalid `{field}` value type",
        )
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _is_duplicate_cluster_integrity_error(*, exc: IntegrityError) -> bool:
    message = _normalized_integrity_message(exc=exc)
    if "uq_bot_deliveries_cluster_id" in message:
        return True
    return "unique constraint failed" in message and "bot_deliveries.cluster_id" in message


def _normalized_integrity_message(*, exc: IntegrityError) -> str:
    driver_error = cast("object | None", getattr(exc, "orig", None))
    message_parts = [str(exc)]
    if driver_error is not None:
        message_parts.append(str(driver_error))
    return " ".join(message_parts).lower()
