"""Settings read/write routes for dynamic allowlisted configuration keys."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from tca.storage import (
    DYNAMIC_SETTINGS_DEFAULTS,
    JSONValue,
    SettingsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

router = APIRouter()
_DEFAULT_DYNAMIC_SETTING_VALUES: dict[str, JSONValue] = dict(DYNAMIC_SETTINGS_DEFAULTS)
_ALLOWED_DYNAMIC_SETTING_KEYS = frozenset(_DEFAULT_DYNAMIC_SETTING_VALUES)


class SettingUpsertRequest(BaseModel):
    """Request payload for dynamic settings upsert calls."""

    value: object


class SettingUpsertResponse(BaseModel):
    """Response payload for dynamic settings upsert calls."""

    key: str
    value: object


@router.get("/settings/{key}", tags=["settings"], response_model=SettingUpsertResponse)
async def get_setting(
    key: str,
    request: Request,
) -> SettingUpsertResponse:
    """Read one allowlisted dynamic setting key with seeded-default fallback."""
    _validate_allowed_setting_key(key)
    repository = _build_settings_repository(request)
    return await _resolve_effective_setting_value(repository=repository, key=key)


@router.put("/settings/{key}", tags=["settings"], response_model=SettingUpsertResponse)
async def put_setting(
    key: str,
    payload: SettingUpsertRequest,
    request: Request,
) -> SettingUpsertResponse:
    """Create or update one dynamic setting by key through writer queue."""
    _validate_allowed_setting_key(key)
    repository = _build_settings_repository(request)
    writer_queue = _resolve_writer_queue(request)
    value = cast("JSONValue", payload.value)

    async def _write_setting() -> SettingUpsertResponse:
        updated = await repository.update(key=key, value=value)
        if updated is None:
            updated = await repository.create(key=key, value=value)
        return SettingUpsertResponse(key=updated.key, value=updated.value)

    return await writer_queue.submit(_write_setting)


def _build_settings_repository(request: Request) -> SettingsRepository:
    """Create settings repository bound to app runtime read/write sessions."""
    runtime = _resolve_storage_runtime(request)
    return SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _validate_allowed_setting_key(key: str) -> None:
    """Reject unknown dynamic setting keys with explicit bad-request error."""
    if key not in _ALLOWED_DYNAMIC_SETTING_KEYS:
        message = f"Unknown setting key '{key}'."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )


async def _resolve_effective_setting_value(
    *,
    repository: SettingsRepository,
    key: str,
) -> SettingUpsertResponse:
    """Return effective value from persisted row or seeded default fallback."""
    record = await repository.get_by_key(key=key)
    if record is not None:
        return SettingUpsertResponse(key=record.key, value=record.value)
    default = _DEFAULT_DYNAMIC_SETTING_VALUES[key]
    return SettingUpsertResponse(key=key, value=default)


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
