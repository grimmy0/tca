"""Job control routes for manual poll requests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from tca.storage import (
    ChannelsRepository,
    ChannelStateRepository,
    PollJobsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

router = APIRouter()


class PollNowResponse(BaseModel):
    """Response payload for manual poll trigger requests."""

    channel_id: int
    correlation_id: str


@router.post(
    "/jobs/poll-now/{channel_id}",
    tags=["jobs"],
    response_model=PollNowResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def poll_now(channel_id: int, request: Request) -> PollNowResponse:
    """Enqueue a manual poll job for an active channel."""
    channels_repository = _build_channels_repository(request)
    state_repository = _build_channel_state_repository(request)
    jobs_repository = _build_poll_jobs_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _enqueue() -> PollNowResponse:
        channel = await channels_repository.get_channel_by_id(channel_id=channel_id)
        if channel is None:
            raise _channel_not_found(channel_id=channel_id)
        if not channel.is_enabled:
            raise _channel_disabled(channel_id=channel_id)
        state = await state_repository.get_state(channel_id=channel_id)
        paused_until = state.paused_until if state else None
        if paused_until is not None and paused_until > datetime.now(UTC):
            raise _channel_paused(channel_id=channel_id, paused_until=paused_until)
        correlation_id = str(uuid4())
        job = await jobs_repository.enqueue_poll_job(
            channel_id=channel_id,
            correlation_id=correlation_id,
        )
        return PollNowResponse(
            channel_id=job.channel_id,
            correlation_id=job.correlation_id,
        )

    return await writer_queue.submit(_enqueue)


def _build_channels_repository(request: Request) -> ChannelsRepository:
    """Create channels repository bound to app runtime sessions."""
    runtime = _resolve_storage_runtime(request)
    return ChannelsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _build_channel_state_repository(request: Request) -> ChannelStateRepository:
    """Create channel state repository bound to app runtime sessions."""
    runtime = _resolve_storage_runtime(request)
    return ChannelStateRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _build_poll_jobs_repository(request: Request) -> PollJobsRepository:
    """Create poll jobs repository bound to app runtime sessions."""
    runtime = _resolve_storage_runtime(request)
    return PollJobsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _channel_not_found(*, channel_id: int) -> HTTPException:
    """Build deterministic not-found error for channels."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Channel '{channel_id}' was not found.",
    )


def _channel_disabled(*, channel_id: int) -> HTTPException:
    """Build deterministic rejection error for disabled channels."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Channel '{channel_id}' is disabled.",
    )


def _channel_paused(*, channel_id: int, paused_until: datetime) -> HTTPException:
    """Build deterministic rejection error for paused channels."""
    detail = f"Channel '{channel_id}' is paused until {paused_until.isoformat()}."
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


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
