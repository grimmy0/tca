"""API routes for Telegram Bot configuration and diagnostics."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from tca.bot import BotApiClient, BotTokenInvalidError
from tca.storage import SettingsRepository, StorageRuntime, WriterQueueProtocol

router = APIRouter(prefix="/bot", tags=["bot"])


class BotConfigRequest(BaseModel):
    """Request payload to set Telegram Bot token and destination chat ID."""

    token: str
    chat_id: str


class BotConfigResponse(BaseModel):
    """Response payload returned on successful bot configuration."""

    bot_username: str
    chat_id: str


class BotConfigStatusResponse(BaseModel):
    """Response payload detailing the active Telegram Bot configuration status."""

    token_masked: str | None
    chat_id: str | None
    enabled: bool


class BotTestResponse(BaseModel):
    """Response payload indicating test message delivery success."""

    message_id: int


@router.post("/config", response_model=BotConfigResponse)
async def configure_bot(
    payload: BotConfigRequest,
    request: Request,
) -> BotConfigResponse:
    """Validate bot token and persist bot delivery configuration."""
    client = BotApiClient()
    try:
        bot_info = await client.validate_token(payload.token)
    except BotTokenInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid Telegram bot token: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to validate bot token: {exc}",
        ) from exc

    repository = _build_settings_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _write_config() -> BotConfigResponse:
        for key, val in [
            ("bot.token", payload.token),
            ("bot.chat_id", payload.chat_id),
            ("bot.enabled", True),
        ]:
            updated = await repository.update(key=key, value=val)
            if updated is None:
                await repository.create(key=key, value=val)
        return BotConfigResponse(bot_username=bot_info.username, chat_id=payload.chat_id)

    return await writer_queue.submit(_write_config)


@router.get("/config", response_model=BotConfigStatusResponse)
async def get_bot_config(request: Request) -> BotConfigStatusResponse:
    """Retrieve the masked bot token and destination chat details."""
    repository = _build_settings_repository(request)

    token_rec = await repository.get_by_key(key="bot.token")
    chat_id_rec = await repository.get_by_key(key="bot.chat_id")
    enabled_rec = await repository.get_by_key(key="bot.enabled")

    token = str(token_rec.value) if token_rec else None
    chat_id = str(chat_id_rec.value) if chat_id_rec else None
    enabled = bool(enabled_rec.value) if enabled_rec else False

    if not token or not chat_id:
        return BotConfigStatusResponse(token_masked=None, chat_id=None, enabled=False)

    # Mask token: last 4 chars visible, rest replaced with '*'
    token_masked = "*" * (len(token) - 4) + token[-4:] if len(token) > 4 else "*" * len(token)

    return BotConfigStatusResponse(
        token_masked=token_masked,
        chat_id=chat_id,
        enabled=enabled,
    )


@router.delete("/config", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot_config(request: Request) -> None:
    """Clear all bot configuration keys from database and disable delivery."""
    repository = _build_settings_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _clear_config() -> None:
        await repository.delete(key="bot.token")
        await repository.delete(key="bot.chat_id")
        # Ensure enabled flag is updated to false on removal
        await repository.upsert(key="bot.enabled", value=False)

    await writer_queue.submit(_clear_config)


@router.post("/test", response_model=BotTestResponse)
async def test_bot_config(request: Request) -> BotTestResponse:
    """Send a connection verification test message to the configured channel."""
    repository = _build_settings_repository(request)

    token_rec = await repository.get_by_key(key="bot.token")
    chat_id_rec = await repository.get_by_key(key="bot.chat_id")

    token = str(token_rec.value) if token_rec else None
    chat_id = str(chat_id_rec.value) if chat_id_rec else None

    if not token or not chat_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Telegram bot delivery is not configured.",
        )

    client = BotApiClient()
    try:
        sent = await client.send_message(
            token=token,
            chat_id=chat_id,
            text="TCA bot delivery test — connection verified.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Test message delivery failed: {exc}",
        ) from exc

    return BotTestResponse(message_id=sent.message_id)


def _build_settings_repository(request: Request) -> SettingsRepository:
    """Create settings repository bound to app runtime read/write sessions."""
    runtime = _resolve_storage_runtime(request)
    return SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
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
