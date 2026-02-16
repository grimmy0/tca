"""Bearer authentication dependency for protected API routes."""

from __future__ import annotations

import secrets
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tca.auth import BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY, compute_token_sha256_digest
from tca.storage import SettingsRepository, StorageRuntime

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_bearer_auth(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> None:
    """Require valid bearer token digest for protected routes."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized_error()

    repository = _build_settings_repository(request=request)
    stored_digest_record = await repository.get_by_key(
        key=BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY,
    )
    if stored_digest_record is None:
        raise _unauthorized_error()

    stored_digest = stored_digest_record.value
    if not isinstance(stored_digest, str):
        raise _unauthorized_error()

    presented_digest = compute_token_sha256_digest(token=credentials.credentials)
    if not secrets.compare_digest(stored_digest, presented_digest):
        raise _unauthorized_error()


def _build_settings_repository(*, request: Request) -> SettingsRepository:
    """Create settings repository bound to app runtime read/write sessions."""
    runtime = _resolve_storage_runtime(request=request)
    return SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _resolve_storage_runtime(*, request: Request) -> StorageRuntime:
    """Load app storage runtime from FastAPI state with explicit failure mode."""
    state_obj = _resolve_app_state(request=request)
    runtime_obj = getattr(state_obj, "storage_runtime", None)
    if not isinstance(runtime_obj, StorageRuntime):
        message = "Missing app storage runtime: app.state.storage_runtime."
        raise TypeError(message)
    return runtime_obj


def _resolve_app_state(*, request: Request) -> object:
    """Resolve request app state with explicit object typing for static analysis."""
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    return cast("object", getattr(app_obj, "state", None))


def _unauthorized_error() -> HTTPException:
    """Build deterministic unauthorized error for bearer auth failures."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized.",
        headers={"WWW-Authenticate": "Bearer"},
    )
