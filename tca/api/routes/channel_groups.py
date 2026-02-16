"""Channel-group management routes for CRUD and membership operations."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from tca.storage import (
    ChannelAlreadyAssignedToGroupError,
    ChannelGroupRecord,
    ChannelGroupsRepository,
    ChannelsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

router = APIRouter()


class ChannelGroupCreateRequest(BaseModel):
    """Request payload for creating one channel group."""

    name: str = Field(min_length=1)
    description: str | None = None
    dedupe_horizon_minutes_override: int | None = None


class ChannelGroupPatchRequest(BaseModel):
    """Request payload for partial channel-group updates."""

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    dedupe_horizon_minutes_override: int | None = None


class ChannelGroupResponse(BaseModel):
    """Response payload for channel-group API operations."""

    id: int
    name: str
    description: str | None
    dedupe_horizon_minutes_override: int | None


class ChannelGroupDeleteResponse(BaseModel):
    """Response payload for channel-group delete operations."""

    deleted_group_id: int


class ChannelGroupMembershipResponse(BaseModel):
    """Response payload for channel-group membership mutations."""

    group_id: int
    channel_id: int
    is_member: bool


@router.get(
    "/channel-groups",
    tags=["channel-groups"],
    response_model=list[ChannelGroupResponse],
)
async def list_channel_groups(request: Request) -> list[ChannelGroupResponse]:
    """List channel groups ordered by ascending id."""
    repository = _build_channel_groups_repository(request)
    groups = await repository.list_groups()
    return [_to_channel_group_response(group=group) for group in groups]


@router.post(
    "/channel-groups",
    tags=["channel-groups"],
    response_model=ChannelGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_channel_group(
    payload: ChannelGroupCreateRequest,
    request: Request,
) -> ChannelGroupResponse:
    """Create one channel group via app writer queue serialization."""
    repository = _build_channel_groups_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _create() -> ChannelGroupResponse:
        created = await repository.create_group(
            name=payload.name,
            description=payload.description,
            dedupe_horizon_minutes_override=payload.dedupe_horizon_minutes_override,
        )
        return _to_channel_group_response(group=created)

    return await writer_queue.submit(_create)


@router.patch(
    "/channel-groups/{group_id}",
    tags=["channel-groups"],
    response_model=ChannelGroupResponse,
)
async def patch_channel_group(
    group_id: int,
    payload: ChannelGroupPatchRequest,
    request: Request,
) -> ChannelGroupResponse:
    """Patch one channel-group row, supporting horizon override clear via null."""
    repository = _build_channel_groups_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _update() -> ChannelGroupResponse:
        current = await repository.get_group_by_id(group_id=group_id)
        if current is None:
            raise _group_not_found(group_id=group_id)

        fields_set = payload.model_fields_set
        updated_name = current.name
        if "name" in fields_set:
            if payload.name is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Field `name` cannot be null.",
                )
            updated_name = payload.name
        updated_description = (
            payload.description if "description" in fields_set else current.description
        )
        updated_horizon = (
            payload.dedupe_horizon_minutes_override
            if "dedupe_horizon_minutes_override" in fields_set
            else current.dedupe_horizon_minutes_override
        )

        updated = await repository.update_group(
            group_id=group_id,
            name=updated_name,
            description=updated_description,
            dedupe_horizon_minutes_override=updated_horizon,
        )
        if updated is None:
            raise _group_not_found(group_id=group_id)
        return _to_channel_group_response(group=updated)

    return await writer_queue.submit(_update)


@router.delete(
    "/channel-groups/{group_id}",
    tags=["channel-groups"],
    response_model=ChannelGroupDeleteResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_channel_group(
    group_id: int,
    request: Request,
) -> ChannelGroupDeleteResponse:
    """Delete one channel group by id through writer queue execution."""
    repository = _build_channel_groups_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _delete() -> ChannelGroupDeleteResponse:
        deleted = await repository.delete_group(group_id=group_id)
        if not deleted:
            raise _group_not_found(group_id=group_id)
        return ChannelGroupDeleteResponse(deleted_group_id=group_id)

    return await writer_queue.submit(_delete)


@router.put(
    "/channel-groups/{group_id}/channels/{channel_id}",
    tags=["channel-groups"],
    response_model=ChannelGroupMembershipResponse,
    status_code=status.HTTP_200_OK,
)
async def put_channel_group_membership(
    group_id: int,
    channel_id: int,
    request: Request,
) -> ChannelGroupMembershipResponse:
    """Assign one channel to one group, preserving PUT idempotency semantics."""
    groups_repository = _build_channel_groups_repository(request)
    channels_repository = _build_channels_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _assign_membership() -> ChannelGroupMembershipResponse:
        existing_group = await groups_repository.get_group_by_id(group_id=group_id)
        if existing_group is None:
            raise _group_not_found(group_id=group_id)

        existing_channel = await channels_repository.get_channel_by_id(
            channel_id=channel_id,
        )
        if existing_channel is None:
            raise _channel_not_found(channel_id=channel_id)

        existing_membership = await groups_repository.get_membership_by_channel_id(
            channel_id=channel_id,
        )
        if existing_membership is not None:
            if existing_membership.group_id == group_id:
                return ChannelGroupMembershipResponse(
                    group_id=group_id,
                    channel_id=channel_id,
                    is_member=True,
                )
            raise _channel_assignment_conflict(
                channel_id=channel_id,
                assigned_group_id=existing_membership.group_id,
            )

        try:
            _ = await groups_repository.add_channel_membership(
                group_id=group_id,
                channel_id=channel_id,
            )
        except ChannelAlreadyAssignedToGroupError as exc:
            refreshed_membership = await groups_repository.get_membership_by_channel_id(
                channel_id=channel_id,
            )
            assigned_group_id = (
                refreshed_membership.group_id
                if refreshed_membership is not None
                else group_id
            )
            raise _channel_assignment_conflict(
                channel_id=channel_id,
                assigned_group_id=assigned_group_id,
            ) from exc

        return ChannelGroupMembershipResponse(
            group_id=group_id,
            channel_id=channel_id,
            is_member=True,
        )

    return await writer_queue.submit(_assign_membership)


@router.delete(
    "/channel-groups/{group_id}/channels/{channel_id}",
    tags=["channel-groups"],
    response_model=ChannelGroupMembershipResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_channel_group_membership(
    group_id: int,
    channel_id: int,
    request: Request,
) -> ChannelGroupMembershipResponse:
    """Remove one channel-group membership; missing membership is idempotent."""
    repository = _build_channel_groups_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _remove_membership() -> ChannelGroupMembershipResponse:
        existing_group = await repository.get_group_by_id(group_id=group_id)
        if existing_group is None:
            raise _group_not_found(group_id=group_id)

        membership = await repository.get_membership_by_channel_id(
            channel_id=channel_id,
        )
        if membership is None:
            return ChannelGroupMembershipResponse(
                group_id=group_id,
                channel_id=channel_id,
                is_member=False,
            )
        if membership.group_id != group_id:
            return ChannelGroupMembershipResponse(
                group_id=group_id,
                channel_id=channel_id,
                is_member=False,
            )

        _ = await repository.remove_channel_membership(
            group_id=group_id,
            channel_id=channel_id,
        )
        return ChannelGroupMembershipResponse(
            group_id=group_id,
            channel_id=channel_id,
            is_member=False,
        )

    return await writer_queue.submit(_remove_membership)


def _to_channel_group_response(*, group: ChannelGroupRecord) -> ChannelGroupResponse:
    """Map repository row payload to API response model."""
    return ChannelGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        dedupe_horizon_minutes_override=group.dedupe_horizon_minutes_override,
    )


def _build_channel_groups_repository(request: Request) -> ChannelGroupsRepository:
    """Create channel-groups repository bound to app runtime sessions."""
    runtime = _resolve_storage_runtime(request)
    return ChannelGroupsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _build_channels_repository(request: Request) -> ChannelsRepository:
    """Create channels repository bound to app runtime sessions."""
    runtime = _resolve_storage_runtime(request)
    return ChannelsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _group_not_found(*, group_id: int) -> HTTPException:
    """Build deterministic not-found error for channel groups."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Channel group '{group_id}' was not found.",
    )


def _channel_not_found(*, channel_id: int) -> HTTPException:
    """Build deterministic not-found error for channels."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Channel '{channel_id}' was not found.",
    )


def _channel_assignment_conflict(
    *,
    channel_id: int,
    assigned_group_id: int,
) -> HTTPException:
    """Build deterministic conflict error for one-channel-per-group enforcement."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Channel '{channel_id}' is already assigned to group "
            f"'{assigned_group_id}'."
        ),
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
