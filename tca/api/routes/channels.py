"""Channel management routes for CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from tca.storage import (
    ChannelRecord,
    ChannelStateRecord,
    ChannelStateRepository,
    ChannelsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

router = APIRouter()


class ChannelCreateRequest(BaseModel):
    """Request payload for creating one channel."""

    account_id: int = Field(gt=0)
    telegram_channel_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    username: str | None = Field(default=None, min_length=1)


class ChannelPatchRequest(BaseModel):
    """Request payload for partial channel updates."""

    name: str | None = Field(default=None, min_length=1)
    username: str | None = None
    is_enabled: bool | None = None
    paused_until: datetime | None = None
    last_success_at: datetime | None = None


class ChannelResponse(BaseModel):
    """Response payload for channel CRUD operations."""

    id: int
    account_id: int
    telegram_channel_id: int
    name: str
    username: str | None
    is_enabled: bool
    paused_until: datetime | None
    last_success_at: datetime | None


@router.get(
    "/channels",
    tags=["channels"],
    response_model=list[ChannelResponse],
)
async def list_channels(request: Request) -> list[ChannelResponse]:
    """List enabled channels ordered by ascending id."""
    repository = _build_channels_repository(request)
    state_repository = _build_channel_state_repository(request)
    channels = await repository.list_active_channels()
    channel_ids = [channel.id for channel in channels]
    states = await state_repository.list_states_by_channel_ids(channel_ids=channel_ids)
    return [
        _to_channel_response(channel=channel, state=states.get(channel.id))
        for channel in channels
    ]


@router.post(
    "/channels",
    tags=["channels"],
    response_model=ChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_channel(
    payload: ChannelCreateRequest,
    request: Request,
) -> ChannelResponse:
    """Create one channel via app writer queue serialization."""
    repository = _build_channels_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _create() -> ChannelResponse:
        created = await repository.create_channel(
            account_id=payload.account_id,
            telegram_channel_id=payload.telegram_channel_id,
            name=payload.name,
            username=payload.username,
        )
        return _to_channel_response(channel=created, state=None)

    return await writer_queue.submit(_create)


@router.patch(
    "/channels/{channel_id}",
    tags=["channels"],
    response_model=ChannelResponse,
)
async def patch_channel(
    channel_id: int,
    payload: ChannelPatchRequest,
    request: Request,
) -> ChannelResponse:
    """Patch one channel row and polling state updates."""
    repository = _build_channels_repository(request)
    state_repository = _build_channel_state_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _update() -> ChannelResponse:
        current = await repository.get_channel_by_id(channel_id=channel_id)
        if current is None:
            raise _channel_not_found(channel_id=channel_id)

        fields_set = payload.model_fields_set
        updated_name = current.name
        if "name" in fields_set:
            if payload.name is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Field `name` cannot be null.",
                )
            updated_name = payload.name
        updated_username = (
            payload.username if "username" in fields_set else current.username
        )
        updated_is_enabled = (
            payload.is_enabled if "is_enabled" in fields_set else current.is_enabled
        )

        updated = await repository.update_channel(
            channel_id=channel_id,
            name=updated_name,
            username=updated_username,
            is_enabled=updated_is_enabled,
        )
        if updated is None:
            raise _channel_not_found(channel_id=channel_id)

        state_fields = {"paused_until", "last_success_at"}
        state_fields_set = fields_set.intersection(state_fields)
        updated_state: ChannelStateRecord | None
        if state_fields_set:
            current_state = await state_repository.get_state(channel_id=channel_id)
            updated_paused_until = (
                current_state.paused_until if current_state else None
            )
            updated_last_success_at = (
                current_state.last_success_at if current_state else None
            )
            if "paused_until" in state_fields_set:
                updated_paused_until = payload.paused_until
            if "last_success_at" in state_fields_set:
                updated_last_success_at = payload.last_success_at
            updated_state = await state_repository.upsert_state(
                channel_id=channel_id,
                paused_until=updated_paused_until,
                last_success_at=updated_last_success_at,
            )
        else:
            updated_state = await state_repository.get_state(channel_id=channel_id)

        return _to_channel_response(channel=updated, state=updated_state)

    return await writer_queue.submit(_update)


def _to_channel_response(
    *,
    channel: ChannelRecord,
    state: ChannelStateRecord | None,
) -> ChannelResponse:
    """Map repository row payload to API response model."""
    return ChannelResponse(
        id=channel.id,
        account_id=channel.account_id,
        telegram_channel_id=channel.telegram_channel_id,
        name=channel.name,
        username=channel.username,
        is_enabled=channel.is_enabled,
        paused_until=state.paused_until if state else None,
        last_success_at=state.last_success_at if state else None,
    )


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


def _channel_not_found(*, channel_id: int) -> HTTPException:
    """Build deterministic not-found error for channels."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Channel '{channel_id}' was not found.",
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
