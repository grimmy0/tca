"""Notifications read routes for UI alerting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from tca.storage import (
    JSONValue,
    NotificationListRecord,
    NotificationsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

if TYPE_CHECKING:
    from datetime import datetime

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
    severity: Annotated[list[str] | None, Query()] = None,
    notification_type: Annotated[list[str] | None, Query(alias="type")] = None,
) -> list[NotificationResponse]:
    """List notifications in recency order with optional filters."""
    repository = _build_notifications_repository(request)
    records = await repository.list_notifications(
        severities=tuple(severity) if severity else None,
        types=tuple(notification_type) if notification_type else None,
    )
    return [_to_notification_response(record=record) for record in records]


@router.put(
    "/notifications/{notification_id}/ack",
    tags=["notifications"],
    response_model=NotificationResponse,
)
async def acknowledge_notification(
    notification_id: int,
    request: Request,
) -> NotificationResponse:
    """Acknowledge one notification and return the updated state."""
    repository = _build_notifications_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _acknowledge() -> NotificationResponse:
        record = await repository.acknowledge(notification_id=notification_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found.",
            )
        return _to_notification_response(record=record)

    return await writer_queue.submit(_acknowledge)


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


def _resolve_writer_queue(request: Request) -> WriterQueueProtocol:
    """Load app writer queue from FastAPI state with explicit failure mode."""
    state_obj = _resolve_app_state(request)
    queue_obj = cast("object | None", getattr(state_obj, "writer_queue", None))
    submit_obj = getattr(queue_obj, "submit", None)
    if queue_obj is None or not callable(submit_obj):
        message = "Missing app writer queue: app.state.writer_queue."
        raise RuntimeError(message)
    return cast("WriterQueueProtocol", queue_obj)


def _resolve_app_state(request: Request) -> object:
    """Resolve request app state with explicit object typing for static analysis."""
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    return cast("object", getattr(app_obj, "state", None))
