"""Repository helpers for `notifications` table CRUD and JSON payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

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
