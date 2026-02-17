"""Repository helpers for `notifications` table CRUD and JSON payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from sqlalchemy import bindparam, text

from tca.storage.settings_repo import JSONValue

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class NotificationRecord:
    """Typed notifications row payload resolved from JSON storage."""

    notification_id: int
    type: str
    severity: str
    message: str
    payload: JSONValue | None


@dataclass(slots=True, frozen=True)
class NotificationListRecord:
    """Typed notifications payload for read/list responses."""

    notification_id: int
    type: str
    severity: str
    message: str
    payload: JSONValue | None
    is_acknowledged: bool
    acknowledged_at: datetime | None
    created_at: datetime


class NotificationsRepositoryError(RuntimeError):
    """Base exception for notifications repository operations."""


class NotificationPayloadEncodeError(NotificationsRepositoryError):
    """Raised when a notification payload cannot be encoded into JSON."""

    @classmethod
    def for_type(
        cls,
        notification_type: str,
        *,
        details: str,
    ) -> NotificationPayloadEncodeError:
        """Build deterministic encode error with type-localized context."""
        message = (
            "Notification payload for type "
            f"'{notification_type}' is not JSON-serializable: {details}"
        )
        return cls(message)


class NotificationsRepository:
    """CRUD helper for notifications rows."""

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
        notification_type: str,
        severity: str,
        message: str,
        payload: JSONValue | None = None,
    ) -> NotificationRecord:
        """Insert a new notification row and return the created payload."""
        encoded_payload = _encode_payload_json(
            notification_type=notification_type,
            payload=payload,
        )
        statement = text(
            """
            INSERT INTO notifications (type, severity, message, payload_json)
            VALUES (:type, :severity, :message, :payload_json)
            RETURNING id, type, severity, message, payload_json
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "type": notification_type,
                    "severity": severity,
                    "message": message,
                    "payload_json": encoded_payload,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_row(row)

    async def list_notifications(
        self,
        *,
        severities: tuple[str, ...] | None = None,
        types: tuple[str, ...] | None = None,
    ) -> list[NotificationListRecord]:
        """List notifications with optional severity/type filters."""
        statement = """
            SELECT
                id,
                type,
                severity,
                message,
                payload_json,
                is_acknowledged,
                acknowledged_at,
                created_at
            FROM notifications
        """
        conditions: list[str] = []
        params: dict[str, object] = {}

        if severities:
            conditions.append("severity IN :severities")
            params["severities"] = list(severities)
        if types:
            conditions.append("type IN :types")
            params["types"] = list(types)
        if conditions:
            statement += " WHERE " + " AND ".join(conditions)
        statement += " ORDER BY created_at DESC, id DESC"

        sql = text(statement)
        if severities:
            sql = sql.bindparams(bindparam("severities", expanding=True))
        if types:
            sql = sql.bindparams(bindparam("types", expanding=True))

        async with self._read_session_factory() as session:
            result = await session.execute(sql, params)
            rows = result.mappings().all()
        return [_decode_list_row(row) for row in rows]


def _encode_payload_json(
    *,
    notification_type: str,
    payload: JSONValue | None,
) -> str | None:
    """Encode payload into JSON string or return None for empty payloads."""
    if payload is None:
        return None
    try:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise NotificationPayloadEncodeError.for_type(
            notification_type,
            details=str(exc),
        ) from exc


def _decode_payload_json(payload_json: object) -> JSONValue | None:
    """Decode payload JSON string back into Python value."""
    if payload_json is None:
        return None
    if not isinstance(payload_json, str):
        raise NotificationsRepositoryError(
            "Notification payload_json is not a string.",
        )
    return cast("JSONValue", json.loads(payload_json))


def _decode_row(row: object) -> NotificationRecord:
    """Decode row mapping into NotificationRecord."""
    row_map = cast("dict[str, object]", row)
    notification_id = row_map.get("id")
    notification_type = row_map.get("type")
    severity = row_map.get("severity")
    message = row_map.get("message")
    payload_json = row_map.get("payload_json")
    if not isinstance(notification_id, int):
        raise NotificationsRepositoryError("Notification row missing id.")
    if not isinstance(notification_type, str):
        raise NotificationsRepositoryError("Notification row missing type.")
    if not isinstance(severity, str):
        raise NotificationsRepositoryError("Notification row missing severity.")
    if not isinstance(message, str):
        raise NotificationsRepositoryError("Notification row missing message.")
    payload = _decode_payload_json(payload_json)
    return NotificationRecord(
        notification_id=notification_id,
        type=notification_type,
        severity=severity,
        message=message,
        payload=payload,
    )


def _decode_list_row(row: object) -> NotificationListRecord:
    """Decode row mapping into NotificationListRecord."""
    row_map = cast("dict[str, object]", row)
    notification_id = row_map.get("id")
    notification_type = row_map.get("type")
    severity = row_map.get("severity")
    message = row_map.get("message")
    payload_json = row_map.get("payload_json")
    is_acknowledged = row_map.get("is_acknowledged")
    acknowledged_at = row_map.get("acknowledged_at")
    created_at = row_map.get("created_at")

    if not isinstance(notification_id, int):
        raise NotificationsRepositoryError("Notification row missing id.")
    if not isinstance(notification_type, str):
        raise NotificationsRepositoryError("Notification row missing type.")
    if not isinstance(severity, str):
        raise NotificationsRepositoryError("Notification row missing severity.")
    if not isinstance(message, str):
        raise NotificationsRepositoryError("Notification row missing message.")
    if not isinstance(is_acknowledged, (bool, int)):
        raise NotificationsRepositoryError(
            "Notification row missing is_acknowledged flag.",
        )
    acknowledged_at_value = _coerce_optional_datetime(value=acknowledged_at)
    created_at_value = _coerce_datetime(value=created_at)
    payload = _decode_payload_json(payload_json)

    return NotificationListRecord(
        notification_id=notification_id,
        type=notification_type,
        severity=severity,
        message=message,
        payload=payload,
        is_acknowledged=bool(is_acknowledged),
        acknowledged_at=acknowledged_at_value,
        created_at=created_at_value,
    )


def _coerce_datetime(*, value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise NotificationsRepositoryError(
                "Notification row invalid created_at.",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    raise NotificationsRepositoryError("Notification row missing created_at.")


def _coerce_optional_datetime(*, value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise NotificationsRepositoryError(
                "Notification row invalid acknowledged_at.",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    raise NotificationsRepositoryError("Notification row invalid acknowledged_at.")
