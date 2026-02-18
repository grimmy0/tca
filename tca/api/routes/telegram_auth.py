"""Telegram auth endpoints for OTP-driven login start."""

from __future__ import annotations

import logging
import secrets
from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    ConnectionApiIdInvalidError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from tca.auth import (
    SENSITIVE_OPERATION_LOCKED_MESSAGE,
    AuthSessionExpiredError,
    AuthSessionState,
    AuthSessionStateNotFoundError,
    AuthSessionStateRepository,
    SensitiveOperationLockedError,
    TelegramAccountStorage,
    TelegramSessionStorage,
    request_login_code,
    require_sensitive_operation_unlock,
    resolve_key_encryption_key,
)
from tca.ingest import record_account_risk_breach
from tca.storage import AccountPauseRepository, StorageRuntime, WriterQueueProtocol
from tca.storage.notifications_repo import NotificationsRepository
from tca.storage.settings_repo import SettingsRepository

router = APIRouter()

logger = logging.getLogger(__name__)

_AUTH_STATUS_CODE_SENT = "code_sent"
_AUTH_STATUS_AUTHENTICATED = "authenticated"
_AUTH_STATUS_PASSWORD_REQUIRED = "password_required"  # noqa: S105
_INVALID_API_CREDENTIALS_DETAIL = "Invalid Telegram API credentials."
_OTP_REQUEST_FAILED_DETAIL = "Unable to send Telegram login code."
_INVALID_LOGIN_CODE_DETAIL = "Invalid Telegram login code."
_EXPIRED_LOGIN_CODE_DETAIL = "Telegram login code expired."
_INVALID_PASSWORD_DETAIL = "Invalid Telegram password."  # noqa: S105
_MISSING_PASSWORD_SESSION_DETAIL = "Auth session missing Telegram session state."  # noqa: S105
_PASSWORD_SESSION_CAPTURE_FAILED_DETAIL = "Unable to capture Telegram auth session."  # noqa: S105
_SENSITIVE_OPERATION_LOCKED_DETAIL = SENSITIVE_OPERATION_LOCKED_MESSAGE
_AUTH_REGISTRATION_BLOCKED_DETAIL = (
    "Telegram registration/login is blocked. Retry later."
)
_AUTH_LOGIN_FAILED_DETAIL = "Telegram login failed. Retry after verifying credentials."

_NOTIFICATION_TYPE_REGISTRATION_BLOCKED = "auth_registration_blocked"
_NOTIFICATION_TYPE_LOGIN_FAILED = "auth_login_failed"
_NOTIFICATION_SEVERITY_HIGH = "high"
_NOTIFICATION_SEVERITY_MEDIUM = "medium"
_DEFAULT_RETRY_AFTER_SECONDS = 3600


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


class TelegramAuthVerifyPasswordRequest(BaseModel):
    """Payload for verifying a Telegram 2FA password."""

    session_id: str = Field(min_length=1)
    api_id: int = Field(gt=0)
    api_hash: str = Field(min_length=1)
    password: str = Field(min_length=1)


class TelegramAuthVerifyPasswordResponse(BaseModel):
    """Response payload for Telegram password verification results."""

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

    @property
    def session(self) -> object | None:
        """Return the underlying session object, if available."""


class TelegramAuthClientFactory(Protocol):
    """Factory for creating Telegram clients for auth flow."""

    def __call__(
        self,
        api_id: int,
        api_hash: str,
        session_string: str | None = None,
    ) -> TelegramAuthClientProtocol:
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
    except (
        PhoneNumberBannedError,
        PhoneNumberFloodError,
        PhoneNumberInvalidError,
        PhoneNumberUnoccupiedError,
    ) as exc:
        writer_queue = _resolve_writer_queue(request)
        notification_type = await _record_auth_failure_notification(
            request=request,
            writer_queue=writer_queue,
            error=exc,
            phone_number=payload.phone_number,
        )
        raise _auth_failure_http_error(
            error=exc,
            notification_type=notification_type,
        ) from exc
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
async def verify_telegram_code(  # noqa: C901
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

    try:
        require_sensitive_operation_unlock()
    except SensitiveOperationLockedError as exc:
        raise _sensitive_operation_locked_error() from exc

    client = _build_auth_client(
        request=request,
        api_id=payload.api_id,
        api_hash=payload.api_hash,
    )
    try:
        _ = await _sign_in_with_code(
            client=client,
            phone_number=session_state.phone_number,
            code=payload.code,
        )
    except SessionPasswordNeededError:
        session_string = _extract_session_string(client)
        if not session_string:
            raise _password_session_capture_error() from None
        updated = await _update_auth_session_status(
            writer_queue=writer_queue,
            repository=repository,
            session_id=session_state.session_id,
            status=_AUTH_STATUS_PASSWORD_REQUIRED,
            telegram_session=session_string,
            update_session=True,
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
    except (
        PhoneNumberBannedError,
        PhoneNumberFloodError,
        PhoneNumberInvalidError,
        PhoneNumberUnoccupiedError,
    ) as exc:
        notification_type = await _record_auth_failure_notification(
            request=request,
            writer_queue=writer_queue,
            error=exc,
            phone_number=session_state.phone_number,
        )
        raise _auth_failure_http_error(
            error=exc,
            notification_type=notification_type,
        ) from exc
    except (ApiIdInvalidError, ConnectionApiIdInvalidError) as exc:
        raise _invalid_api_credentials_error() from exc

    session_string = _extract_session_string(client)
    if not session_string:
        raise _password_session_capture_error()
    try:
        await _persist_authenticated_session(
            request=request,
            writer_queue=writer_queue,
            api_id=payload.api_id,
            api_hash=payload.api_hash,
            phone_number=session_state.phone_number,
            session_string=session_string,
        )
    except SensitiveOperationLockedError as exc:
        raise _sensitive_operation_locked_error() from exc

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


@router.post(
    "/auth/telegram/verify-password",
    tags=["auth"],
    response_model=TelegramAuthVerifyPasswordResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_telegram_password(  # noqa: C901
    payload: TelegramAuthVerifyPasswordRequest,
    request: Request,
) -> TelegramAuthVerifyPasswordResponse:
    """Verify a Telegram 2FA password and advance the auth session state."""
    repository = _build_auth_session_repository(request)
    writer_queue = _resolve_writer_queue(request)

    try:
        session_state = await repository.get_session(session_id=payload.session_id)
    except AuthSessionStateNotFoundError as exc:
        raise _auth_session_not_found_error(session_id=payload.session_id) from exc
    except AuthSessionExpiredError as exc:
        raise _auth_session_expired_error(session_id=payload.session_id) from exc

    if session_state.status != _AUTH_STATUS_PASSWORD_REQUIRED:
        raise _auth_session_password_status_conflict_error(
            current_status=session_state.status,
        )

    if not session_state.telegram_session:
        raise _missing_password_session_error()

    try:
        require_sensitive_operation_unlock()
    except SensitiveOperationLockedError as exc:
        raise _sensitive_operation_locked_error() from exc

    client = _build_auth_client(
        request=request,
        api_id=payload.api_id,
        api_hash=payload.api_hash,
        session_string=session_state.telegram_session,
    )
    try:
        _ = await _sign_in_with_password(
            client=client,
            password=payload.password,
        )
    except PasswordHashInvalidError as exc:
        raise _invalid_password_error() from exc
    except (
        PhoneNumberBannedError,
        PhoneNumberFloodError,
        PhoneNumberInvalidError,
        PhoneNumberUnoccupiedError,
    ) as exc:
        notification_type = await _record_auth_failure_notification(
            request=request,
            writer_queue=writer_queue,
            error=exc,
            phone_number=session_state.phone_number,
        )
        raise _auth_failure_http_error(
            error=exc,
            notification_type=notification_type,
        ) from exc
    except (ApiIdInvalidError, ConnectionApiIdInvalidError) as exc:
        raise _invalid_api_credentials_error() from exc

    session_string = _extract_session_string(client)
    if not session_string:
        raise _password_session_capture_error()
    try:
        await _persist_authenticated_session(
            request=request,
            writer_queue=writer_queue,
            api_id=payload.api_id,
            api_hash=payload.api_hash,
            phone_number=session_state.phone_number,
            session_string=session_string,
        )
    except SensitiveOperationLockedError as exc:
        raise _sensitive_operation_locked_error() from exc

    updated = await _update_auth_session_status(
        writer_queue=writer_queue,
        repository=repository,
        session_id=session_state.session_id,
        status=_AUTH_STATUS_AUTHENTICATED,
        telegram_session=None,
        update_session=True,
    )
    return TelegramAuthVerifyPasswordResponse(
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


async def _sign_in_with_password(
    *,
    client: TelegramAuthClientProtocol,
    password: str,
) -> object:
    """Sign in with 2FA password using safe connect/disconnect handling."""
    should_disconnect = False
    if not client.is_connected():
        await client.connect()
        should_disconnect = True

    try:
        return await client.sign_in(password=password)
    finally:
        if should_disconnect:
            await client.disconnect()


def _default_auth_client_factory(
    api_id: int,
    api_hash: str,
    session_string: str | None = None,
) -> TelegramAuthClientProtocol:
    """Create a Telethon client using in-memory StringSession."""
    session_obj = StringSession(session_string) if session_string else StringSession()
    return cast(
        "TelegramAuthClientProtocol",
        TelegramClient(session_obj, api_id, api_hash),
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


def _invalid_password_error() -> HTTPException:
    """Build deterministic password error."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=_INVALID_PASSWORD_DETAIL,
    )


def _missing_password_session_error() -> HTTPException:
    """Build deterministic error for missing password session state."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=_MISSING_PASSWORD_SESSION_DETAIL,
    )


def _password_session_capture_error() -> HTTPException:
    """Build deterministic error when session capture fails."""
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=_PASSWORD_SESSION_CAPTURE_FAILED_DETAIL,
    )


def _sensitive_operation_locked_error() -> HTTPException:
    """Build deterministic error for locked sensitive operations."""
    return HTTPException(
        status_code=status.HTTP_423_LOCKED,
        detail=_SENSITIVE_OPERATION_LOCKED_DETAIL,
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
            f"Auth session cannot accept login code when status is '{current_status}'."
        ),
    )


def _auth_session_password_status_conflict_error(
    *,
    current_status: str,
) -> HTTPException:
    """Build deterministic error for invalid password step status."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Auth session cannot accept password when status is '{current_status}'."
        ),
    )


async def _update_auth_session_status(  # noqa: PLR0913
    *,
    writer_queue: WriterQueueProtocol,
    repository: AuthSessionStateRepository,
    session_id: str,
    status: str,
    telegram_session: str | None = None,
    update_session: bool = False,
) -> AuthSessionState:
    """Update auth session status through the writer queue."""

    async def _update() -> AuthSessionState:
        return await repository.update_status(
            session_id=session_id,
            status=status,
            telegram_session=telegram_session,
            update_session=update_session,
        )

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


def _build_settings_repository(request: Request) -> SettingsRepository:
    """Create settings repository bound to app runtime sessions."""
    runtime = _resolve_storage_runtime(request)
    return SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


async def _persist_authenticated_session(  # noqa: PLR0913
    *,
    request: Request,
    writer_queue: WriterQueueProtocol,
    api_id: int,
    api_hash: str,
    phone_number: str,
    session_string: str,
) -> None:
    """Persist account credentials and StringSession after successful login."""
    runtime = _resolve_storage_runtime(request)
    settings_repository = _build_settings_repository(request)
    key_encryption_key = await resolve_key_encryption_key(
        settings_repository=settings_repository,
        writer_queue=writer_queue,
    )
    account_storage = TelegramAccountStorage(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    session_storage = TelegramSessionStorage(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )

    async def _persist() -> None:
        account_id = await account_storage.upsert_account(
            api_id=api_id,
            api_hash=api_hash,
            phone_number=phone_number,
            key_encryption_key=key_encryption_key,
        )
        await session_storage.persist_session(
            account_id=account_id,
            string_session=session_string,
            key_encryption_key=key_encryption_key,
        )

    await writer_queue.submit(_persist)


def _build_auth_client(
    *,
    request: Request,
    api_id: int,
    api_hash: str,
    session_string: str | None = None,
) -> TelegramAuthClientProtocol:
    """Build auth client from factory with optional StringSession reuse."""
    factory = _resolve_auth_client_factory(request)
    if session_string is None:
        return factory(api_id, api_hash)
    try:
        return factory(api_id, api_hash, session_string)
    except TypeError as exc:
        message = "Telegram auth client factory does not accept session string."
        raise TypeError(message) from exc


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


def _extract_session_string(client: TelegramAuthClientProtocol) -> str | None:
    """Extract StringSession data from the Telethon client if available."""
    session_obj = getattr(client, "session", None)
    save_obj = getattr(session_obj, "save", None)
    if session_obj is None or not callable(save_obj):
        return None
    session_string = save_obj()
    if isinstance(session_string, str) and session_string:
        return session_string
    return None


def _auth_registration_blocked_error(*, error: BaseException) -> HTTPException:
    """Build deterministic error for blocked registration/login failures."""
    _ = error
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=_AUTH_REGISTRATION_BLOCKED_DETAIL,
    )


def _auth_login_failed_error(*, error: BaseException) -> HTTPException:
    """Build deterministic error for login failures that are not blocks."""
    _ = error
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=_AUTH_LOGIN_FAILED_DETAIL,
    )


async def _record_auth_failure_notification(
    *,
    request: Request,
    writer_queue: WriterQueueProtocol,
    error: BaseException,
    phone_number: str | None,
) -> str:
    """Persist a notification for registration/login failures."""
    runtime = _resolve_storage_runtime(request)
    repository = NotificationsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    notification_type, severity, message, payload = _map_auth_error_notification(
        error=error,
    )

    async def _persist() -> None:
        _ = await repository.create(
            notification_type=notification_type,
            severity=severity,
            message=message,
            payload=payload,
        )

    await writer_queue.submit(_persist)
    if phone_number:
        await _record_auth_failure_risk_breach(
            request=request,
            writer_queue=writer_queue,
            phone_number=phone_number,
        )
    return notification_type


async def _record_auth_failure_risk_breach(
    *,
    request: Request,
    writer_queue: WriterQueueProtocol,
    phone_number: str,
) -> None:
    """Record account risk breach for existing accounts tied to a phone number."""
    account_id = await _resolve_account_id_for_phone_number(
        request=request,
        phone_number=phone_number,
    )
    if account_id is None:
        return
    runtime = _resolve_storage_runtime(request)
    pause_repository = AccountPauseRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    settings_repository = SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    notifications_repository = NotificationsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    try:
        await record_account_risk_breach(
            writer_queue=writer_queue,
            settings_repository=settings_repository,
            pause_repository=pause_repository,
            notifications_repository=notifications_repository,
            account_id=account_id,
            breach_reason="auth-failure",
        )
    except Exception:
        logger.exception(
            "Failed to record account risk breach for phone number %s",
            phone_number,
        )


async def _resolve_account_id_for_phone_number(
    *,
    request: Request,
    phone_number: str,
) -> int | None:
    """Resolve account id for a phone number when a persisted account exists."""
    runtime = _resolve_storage_runtime(request)
    account_storage = TelegramAccountStorage(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    return await account_storage.get_account_id_by_phone_number(
        phone_number=phone_number,
    )


def _auth_failure_http_error(
    *,
    error: BaseException,
    notification_type: str,
) -> HTTPException:
    """Select HTTP error based on notification type."""
    if notification_type == _NOTIFICATION_TYPE_REGISTRATION_BLOCKED:
        return _auth_registration_blocked_error(error=error)
    return _auth_login_failed_error(error=error)


def _map_auth_error_notification(
    *,
    error: BaseException,
) -> tuple[str, str, str, dict[str, object]]:
    """Map auth errors into notification details with retry guidance."""
    if isinstance(error, (PhoneNumberBannedError, PhoneNumberFloodError)):
        retry_hint = (
            "Wait before retrying. If this persists, review the Telegram account."
        )
        return (
            _NOTIFICATION_TYPE_REGISTRATION_BLOCKED,
            _NOTIFICATION_SEVERITY_HIGH,
            "Telegram registration/login is blocked for this account.",
            _build_retry_payload(error=error, retry_hint=retry_hint),
        )
    retry_hint = "Confirm the phone number and retry the login flow."
    return (
        _NOTIFICATION_TYPE_LOGIN_FAILED,
        _NOTIFICATION_SEVERITY_MEDIUM,
        "Telegram login failed for the supplied account details.",
        _build_retry_payload(
            error=error,
            retry_hint=retry_hint,
        ),
    )


def _build_retry_payload(
    *,
    error: BaseException,
    retry_hint: str,
) -> dict[str, object]:
    """Build notification payload containing retry guidance."""
    retry_after_seconds = _extract_retry_after_seconds(error=error)
    if retry_after_seconds is None:
        retry_after_seconds = _DEFAULT_RETRY_AFTER_SECONDS
    return {
        "error_type": error.__class__.__name__,
        "retry_after_seconds": retry_after_seconds,
        "retry_hint": retry_hint,
    }


def _extract_retry_after_seconds(*, error: BaseException) -> int | None:
    """Extract retry-after seconds if the error exposes a wait duration."""
    retry_after = getattr(error, "seconds", None)
    if isinstance(retry_after, int) and retry_after > 0:
        return retry_after
    return None
