"""Notifications read routes for UI alerting."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from tca.storage import (
    JSONValue,
    NotificationListRecord,
    NotificationsRepository,
    StorageRuntime,
)

router = APIRouter()


class NotificationResponse(BaseModel):
    """Response payload for notification list entries."""

    id: int
    type: str
    severity: str
    message: str
    payload: JSONValue | None
    is_acknowledged: bool
    acknowledged_at: datetime | None
    created_at: datetime


@router.get(
    "/notifications",
    tags=["notifications"],
    response_model=list[NotificationResponse],
)
async def list_notifications(
    request: Request,
    severity: list[str] | None = Query(default=None),
    notification_type: list[str] | None = Query(default=None, alias="type"),
) -> list[NotificationResponse]:
    """List notifications in recency order with optional filters."""
    repository = _build_notifications_repository(request)
    records = await repository.list_notifications(
        severities=tuple(severity) if severity else None,
        types=tuple(notification_type) if notification_type else None,
    )
    return [_to_notification_response(record=record) for record in records]


def _build_notifications_repository(request: Request) -> NotificationsRepository:
    """Create notifications repository bound to app runtime read/write sessions."""
    runtime = _resolve_storage_runtime(request)
    return NotificationsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _to_notification_response(
    *,
    record: NotificationListRecord,
) -> NotificationResponse:
    """Convert notification record into API response payload."""
    return NotificationResponse(
        id=record.notification_id,
        type=record.type,
        severity=record.severity,
        message=record.message,
        payload=record.payload,
        is_acknowledged=record.is_acknowledged,
        acknowledged_at=record.acknowledged_at,
        created_at=record.created_at,
    )


def _resolve_storage_runtime(request: Request) -> StorageRuntime:
    """Load app storage runtime from FastAPI state with explicit failure mode."""
    state_obj = _resolve_app_state(request)
    runtime_obj = getattr(state_obj, "storage_runtime", None)
    if not isinstance(runtime_obj, StorageRuntime):
        message = "Missing app storage runtime: app.state.storage_runtime."
        raise TypeError(message)
    return runtime_obj


def _resolve_app_state(request: Request) -> object:
    """Resolve request app state with explicit object typing for static analysis."""
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    return cast("object", getattr(app_obj, "state", None))
