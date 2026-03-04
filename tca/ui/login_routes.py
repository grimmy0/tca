"""Login and logout routes for browser-based UI authentication."""

from __future__ import annotations

import time
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from tca.api.cookie_auth import (
    SESSION_COOKIE_MAX_AGE_SECONDS,
    SESSION_COOKIE_NAME,
    create_signed_cookie_value,
    verify_signed_cookie_value,
)
from tca.auth import BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY, compute_token_sha256_digest
from tca.storage import SettingsRepository, StorageRuntime

_UI_DIR = Path(__file__).resolve().parent
_templates = Jinja2Templates(directory=(_UI_DIR / "templates").as_posix())

login_router = APIRouter(tags=["ui-auth"])


@login_router.get("/ui/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    """Render login form or redirect to UI if already authenticated."""
    signing_key = _resolve_signing_key(request)
    if signing_key is not None:
        cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
        if cookie_value is not None and verify_signed_cookie_value(
            signing_key=signing_key,
            cookie_value=cookie_value,
            max_age_seconds=SESSION_COOKIE_MAX_AGE_SECONDS,
        ):
            return RedirectResponse(url="/ui", status_code=302)

    return _templates.TemplateResponse(request, "login.html")


@login_router.post("/ui/login", response_class=HTMLResponse)
async def login_submit(request: Request) -> Response:
    """Validate submitted token and set session cookie or show error."""
    form = await request.form()
    token = form.get("token")
    if not isinstance(token, str) or not token.strip():
        return _templates.TemplateResponse(
            request,
            "login.html",
            context={"error_message": "Token is required."},
        )

    runtime = _resolve_storage_runtime(request)
    repository = SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    stored_digest_record = await repository.get_by_key(
        key=BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY,
    )
    if stored_digest_record is None or not isinstance(
        stored_digest_record.value,
        str,
    ):
        return _templates.TemplateResponse(
            request,
            "login.html",
            context={"error_message": "Invalid token."},
        )

    presented_digest = compute_token_sha256_digest(token=token.strip())
    stored_digest = stored_digest_record.value
    if presented_digest != stored_digest:
        return _templates.TemplateResponse(
            request,
            "login.html",
            context={"error_message": "Invalid token."},
        )

    signing_key_bytes = _resolve_signing_key(request)
    if signing_key_bytes is None:
        message = "Missing app cookie signing key: app.state.cookie_signing_key."
        raise TypeError(message)

    cookie_value = create_signed_cookie_value(
        signing_key=signing_key_bytes,
        issued_at=int(time.time()),
    )
    response = RedirectResponse(url="/ui", status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/ui",
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
    )
    return response


@login_router.post("/ui/logout")
async def logout() -> Response:
    """Clear session cookie and redirect to login page."""
    response = RedirectResponse(url="/ui/login", status_code=302)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/ui")
    return response


def _resolve_signing_key(request: Request) -> bytes | None:
    """Load cookie signing key from app state, returning None if absent."""
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    state_obj = cast("object", getattr(app_obj, "state", None))
    key = getattr(state_obj, "cookie_signing_key", None)
    if isinstance(key, bytes):
        return key
    return None


def _resolve_storage_runtime(request: Request) -> StorageRuntime:
    """Load app storage runtime with explicit failure mode."""
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    state_obj = cast("object", getattr(app_obj, "state", None))
    runtime = getattr(state_obj, "storage_runtime", None)
    if not isinstance(runtime, StorageRuntime):
        message = "Missing app storage runtime: app.state.storage_runtime."
        raise TypeError(message)
    return runtime
