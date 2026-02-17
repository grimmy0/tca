"""Telegram auth endpoints for OTP-driven login start."""

from __future__ import annotations

import secrets
from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    ConnectionApiIdInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from tca.auth import (
    AuthSessionExpiredError,
    AuthSessionState,
    AuthSessionStateNotFoundError,
    AuthSessionStateRepository,
    request_login_code,
)
from tca.storage import StorageRuntime, WriterQueueProtocol

router = APIRouter()

_AUTH_STATUS_CODE_SENT = "code_sent"
_AUTH_STATUS_AUTHENTICATED = "authenticated"
_AUTH_STATUS_PASSWORD_REQUIRED = "password_required"
_INVALID_API_CREDENTIALS_DETAIL = "Invalid Telegram API credentials."
_OTP_REQUEST_FAILED_DETAIL = "Unable to send Telegram login code."
_INVALID_LOGIN_CODE_DETAIL = "Invalid Telegram login code."
_EXPIRED_LOGIN_CODE_DETAIL = "Telegram login code expired."


class TelegramAuthStartRequest(BaseModel):
    """Payload for starting Telegram OTP login."""

    api_id: int = Field(gt=0)
    api_hash: str = Field(min_length=1)
    phone_number: str = Field(min_length=1)


class TelegramAuthStartResponse(BaseModel):
    """Response payload for starting Telegram OTP login."""

    session_id: str


class TelegramAuthVerifyCodeRequest(BaseModel):
    """Payload for verifying a Telegram OTP code."""

    session_id: str = Field(min_length=1)
    api_id: int = Field(gt=0)
    api_hash: str = Field(min_length=1)
    code: str = Field(min_length=1)


class TelegramAuthVerifyCodeResponse(BaseModel):
    """Response payload for Telegram OTP verification results."""

    session_id: str
    status: str


class TelegramAuthClientProtocol(Protocol):
    """Minimum client surface for OTP login requests."""

    async def send_code_request(self, phone: str) -> object:
        """Request an OTP code for the provided phone number."""

    async def sign_in(
        self,
        phone: str | None = None,
        code: str | None = None,
        password: str | None = None,
        bot_token: str | None = None,
    ) -> object:
        """Sign in with provided credentials."""

    async def connect(self) -> None:
        """Connect to Telegram."""

    async def disconnect(self) -> None:
        """Disconnect from Telegram."""

    def is_connected(self) -> bool:
        """Return True when the client is currently connected."""


class TelegramAuthClientFactory(Protocol):
    """Factory for creating Telegram clients for auth flow."""

    def __call__(self, api_id: int, api_hash: str) -> TelegramAuthClientProtocol:
        """Create a Telegram client instance."""


@router.post(
    "/auth/telegram/start",
    tags=["auth"],
    response_model=TelegramAuthStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_telegram_auth(
    payload: TelegramAuthStartRequest,
    request: Request,
) -> TelegramAuthStartResponse:
    """Request a Telegram login code and create a temporary auth session."""
    client_factory = _resolve_auth_client_factory(request)
    client = client_factory(payload.api_id, payload.api_hash)
    try:
        send_code_result = await _send_login_code(
            client=client,
            phone_number=payload.phone_number,
        )
    except (ApiIdInvalidError, ConnectionApiIdInvalidError) as exc:
        raise _invalid_api_credentials_error() from exc

    if not send_code_result:
        raise _otp_request_failed_error()

    repository = _build_auth_session_repository(request)
    writer_queue = _resolve_writer_queue(request)

    async def _create_session() -> TelegramAuthStartResponse:
        created = await repository.create_session(
            session_id=_generate_session_id(),
            phone_number=payload.phone_number,
            status=_AUTH_STATUS_CODE_SENT,
        )
        return TelegramAuthStartResponse(session_id=created.session_id)

    return await writer_queue.submit(_create_session)


@router.post(
    "/auth/telegram/verify-code",
    tags=["auth"],
    response_model=TelegramAuthVerifyCodeResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_telegram_code(
    payload: TelegramAuthVerifyCodeRequest,
    request: Request,
) -> TelegramAuthVerifyCodeResponse:
    """Verify a Telegram login code and advance the auth session state."""
    repository = _build_auth_session_repository(request)
    writer_queue = _resolve_writer_queue(request)

    try:
        session_state = await repository.get_session(session_id=payload.session_id)
    except AuthSessionStateNotFoundError as exc:
        raise _auth_session_not_found_error(session_id=payload.session_id) from exc
    except AuthSessionExpiredError as exc:
        raise _auth_session_expired_error(session_id=payload.session_id) from exc

    if session_state.status != _AUTH_STATUS_CODE_SENT:
        raise _auth_session_status_conflict_error(current_status=session_state.status)

    client_factory = _resolve_auth_client_factory(request)
    client = client_factory(payload.api_id, payload.api_hash)
    try:
        _ = await _sign_in_with_code(
            client=client,
            phone_number=session_state.phone_number,
            code=payload.code,
        )
    except SessionPasswordNeededError:
        updated = await _update_auth_session_status(
            writer_queue=writer_queue,
            repository=repository,
            session_id=session_state.session_id,
            status=_AUTH_STATUS_PASSWORD_REQUIRED,
        )
        return TelegramAuthVerifyCodeResponse(
            session_id=updated.session_id,
            status=updated.status,
        )
    except PhoneCodeInvalidError as exc:
        raise _invalid_login_code_error() from exc
    except PhoneCodeExpiredError as exc:
        await _delete_auth_session(
            writer_queue=writer_queue,
            repository=repository,
            session_id=session_state.session_id,
        )
        raise _expired_login_code_error() from exc
    except (ApiIdInvalidError, ConnectionApiIdInvalidError) as exc:
        raise _invalid_api_credentials_error() from exc

    updated = await _update_auth_session_status(
        writer_queue=writer_queue,
        repository=repository,
        session_id=session_state.session_id,
        status=_AUTH_STATUS_AUTHENTICATED,
    )
    return TelegramAuthVerifyCodeResponse(
        session_id=updated.session_id,
        status=updated.status,
    )


async def _send_login_code(
    *,
    client: TelegramAuthClientProtocol,
    phone_number: str,
) -> bool:
    """Send OTP login request with safe connect/disconnect handling."""
    should_disconnect = False
    if not client.is_connected():
        await client.connect()
        should_disconnect = True

    try:
        result = await request_login_code(client, phone_number)
    finally:
        if should_disconnect:
            await client.disconnect()
    return bool(result)


async def _sign_in_with_code(
    *,
    client: TelegramAuthClientProtocol,
    phone_number: str,
    code: str,
) -> object:
    """Sign in with OTP code using safe connect/disconnect handling."""
    should_disconnect = False
    if not client.is_connected():
        await client.connect()
        should_disconnect = True

    try:
        return await client.sign_in(phone=phone_number, code=code)
    finally:
        if should_disconnect:
            await client.disconnect()


def _default_auth_client_factory(
    api_id: int,
    api_hash: str,
) -> TelegramAuthClientProtocol:
    """Create a Telethon client using in-memory StringSession."""
    return cast(
        "TelegramAuthClientProtocol",
        TelegramClient(StringSession(), api_id, api_hash),
    )


def _generate_session_id() -> str:
    """Generate a random auth session id."""
    return secrets.token_urlsafe(32)


def _invalid_api_credentials_error() -> HTTPException:
    """Build deterministic API credential error."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=_INVALID_API_CREDENTIALS_DETAIL,
    )


def _otp_request_failed_error() -> HTTPException:
    """Build deterministic OTP failure error."""
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=_OTP_REQUEST_FAILED_DETAIL,
    )


def _invalid_login_code_error() -> HTTPException:
    """Build deterministic OTP code error."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=_INVALID_LOGIN_CODE_DETAIL,
    )


def _expired_login_code_error() -> HTTPException:
    """Build deterministic OTP expiry error."""
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=_EXPIRED_LOGIN_CODE_DETAIL,
    )


def _auth_session_not_found_error(*, session_id: str) -> HTTPException:
    """Build deterministic error for missing auth session."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Auth session state not found for session_id='{session_id}'.",
    )


def _auth_session_expired_error(*, session_id: str) -> HTTPException:
    """Build deterministic error for expired auth session."""
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=f"Auth session state expired for session_id='{session_id}'.",
    )


def _auth_session_status_conflict_error(*, current_status: str) -> HTTPException:
    """Build deterministic error for invalid auth session status."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Auth session cannot accept login code when status is "
            f"'{current_status}'."
        ),
    )


async def _update_auth_session_status(
    *,
    writer_queue: WriterQueueProtocol,
    repository: AuthSessionStateRepository,
    session_id: str,
    status: str,
) -> AuthSessionState:
    """Update auth session status through the writer queue."""

    async def _update() -> AuthSessionState:
        return await repository.update_status(session_id=session_id, status=status)

    try:
        return await writer_queue.submit(_update)
    except AuthSessionStateNotFoundError as exc:
        raise _auth_session_not_found_error(session_id=session_id) from exc
    except AuthSessionExpiredError as exc:
        raise _auth_session_expired_error(session_id=session_id) from exc


async def _delete_auth_session(
    *,
    writer_queue: WriterQueueProtocol,
    repository: AuthSessionStateRepository,
    session_id: str,
) -> None:
    """Delete auth session state through the writer queue."""

    async def _delete() -> None:
        _ = await repository.delete_session(session_id=session_id)

    await writer_queue.submit(_delete)


def _build_auth_session_repository(request: Request) -> AuthSessionStateRepository:
    """Create auth session repository bound to app runtime sessions."""
    runtime = _resolve_storage_runtime(request)
    return AuthSessionStateRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _resolve_auth_client_factory(request: Request) -> TelegramAuthClientFactory:
    """Resolve Telegram auth client factory from app state or defaults."""
    state_obj = _resolve_app_state(request)
    factory_obj = getattr(state_obj, "telegram_auth_client_factory", None)
    if factory_obj is None:
        return _default_auth_client_factory
    if not callable(factory_obj):
        message = "Invalid Telegram auth client factory on app.state."
        raise TypeError(message)
    return cast("TelegramAuthClientFactory", factory_obj)


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
