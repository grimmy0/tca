"""Cookie-based session authentication for browser UI routes."""

from __future__ import annotations

import hmac
import secrets
import time
from typing import cast

from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer

from tca.api.bearer_auth import require_bearer_auth

SESSION_COOKIE_NAME = "tca_session"
SESSION_COOKIE_MAX_AGE_SECONDS = 86400
SIGNING_KEY_BYTES = 32

_bearer_scheme = HTTPBearer(auto_error=False)


class UIAuthRedirectError(Exception):
    """Raised when an unauthenticated UI request should redirect to login."""


def generate_cookie_signing_key() -> bytes:
    """Generate a random signing key for HMAC cookie signatures."""
    return secrets.token_bytes(SIGNING_KEY_BYTES)


def create_signed_cookie_value(*, signing_key: bytes, issued_at: int) -> str:
    """Create an HMAC-signed cookie value from an issued-at timestamp."""
    timestamp_str = str(issued_at)
    signature = hmac.new(
        signing_key,
        timestamp_str.encode(),
        "sha256",
    ).hexdigest()
    return f"{timestamp_str}.{signature}"


def verify_signed_cookie_value(
    *,
    signing_key: bytes,
    cookie_value: str,
    max_age_seconds: int,
) -> bool:
    """Verify an HMAC-signed cookie value for authenticity and freshness."""
    parts = cookie_value.split(".", maxsplit=1)
    if len(parts) != 2:  # noqa: PLR2004
        return False

    timestamp_str, presented_signature = parts
    try:
        issued_at = int(timestamp_str)
    except ValueError:
        return False

    expected_signature = hmac.new(
        signing_key,
        timestamp_str.encode(),
        "sha256",
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, presented_signature):
        return False

    age = int(time.time()) - issued_at
    return age <= max_age_seconds


async def require_ui_auth(request: Request) -> None:
    """Require valid bearer token or signed session cookie for UI routes."""
    bearer_valid = False
    try:
        credentials = await _bearer_scheme(request)
        await require_bearer_auth(request, credentials)
        bearer_valid = True
    except HTTPException:
        pass

    if bearer_valid:
        return

    signing_key = _resolve_signing_key(request=request)
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_value is not None and verify_signed_cookie_value(
        signing_key=signing_key,
        cookie_value=cookie_value,
        max_age_seconds=SESSION_COOKIE_MAX_AGE_SECONDS,
    ):
        return

    raise UIAuthRedirectError


def _resolve_signing_key(*, request: Request) -> bytes:
    """Load cookie signing key from app state."""
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    state_obj = cast("object", getattr(app_obj, "state", None))
    key = getattr(state_obj, "cookie_signing_key", None)
    if not isinstance(key, bytes):
        message = "Missing app cookie signing key: app.state.cookie_signing_key."
        raise TypeError(message)
    return key
