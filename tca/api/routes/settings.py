"""Settings write routes backed by the single-writer queue."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request
from pydantic import BaseModel

from tca.storage import (
    JSONValue,
    SettingsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

router = APIRouter()


class SettingUpsertRequest(BaseModel):
    """Request payload for dynamic settings upsert calls."""

    value: object


class SettingUpsertResponse(BaseModel):
    """Response payload for dynamic settings upsert calls."""

    key: str
    value: object


@router.put("/settings/{key}", tags=["settings"], response_model=SettingUpsertResponse)
async def put_setting(
    key: str,
    payload: SettingUpsertRequest,
    request: Request,
) -> SettingUpsertResponse:
    """Create or update one dynamic setting by key through writer queue."""
    runtime = _resolve_storage_runtime(request)
    writer_queue = _resolve_writer_queue(request)
    repository = SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    value = cast("JSONValue", payload.value)

    async def _write_setting() -> SettingUpsertResponse:
        updated = await repository.update(key=key, value=value)
        if updated is None:
            updated = await repository.create(key=key, value=value)
        return SettingUpsertResponse(key=updated.key, value=updated.value)

    return await writer_queue.submit(_write_setting)


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
    if queue_obj is None or not hasattr(queue_obj, "submit"):
        message = "Missing app writer queue: app.state.writer_queue."
        raise RuntimeError(message)
    return cast("WriterQueueProtocol", queue_obj)


def _resolve_app_state(request: Request) -> object:
    """Resolve request app state with explicit object typing for static analysis."""
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    return cast("object", getattr(app_obj, "state", None))
