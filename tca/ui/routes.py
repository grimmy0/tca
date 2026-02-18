"""UI shell routes and static asset wiring."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from starlette.staticfiles import StaticFiles

from tca.api.routes.telegram_auth import (
    TelegramAuthStartRequest,
    TelegramAuthVerifyCodeRequest,
    TelegramAuthVerifyPasswordRequest,
    start_telegram_auth,
    verify_telegram_code,
    verify_telegram_password,
)
from tca.auth import (
    SensitiveOperationLockedError,
    require_sensitive_operation_unlock,
    unlock_with_passphrase,
)
from tca.storage import StorageRuntime

_UI_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=(_UI_DIR / "templates").as_posix())
static_files = StaticFiles(directory=(_UI_DIR / "static").as_posix())

router = APIRouter(tags=["ui"])


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def get_ui_shell(request: Request) -> HTMLResponse:
    """Render the minimal authenticated shell page."""
    return templates.TemplateResponse(
        request=request,
        name="shell.html",
        context={"page_title": "TCA"},
    )


@router.get("/ui/setup", response_class=HTMLResponse, include_in_schema=False)
async def get_setup_wizard(request: Request) -> Response:
    """Render first-run setup wizard while account configuration is pending."""
    if await _has_persisted_account(request=request):
        return RedirectResponse(url="/ui", status_code=302)
    return _render_setup_step(request=request, step="unlock")


@router.post("/ui/setup/unlock", response_class=HTMLResponse, include_in_schema=False)
async def post_setup_unlock(
    request: Request,
) -> Response:
    """Unlock sensitive operations using setup passphrase input."""
    if await _has_persisted_account(request=request):
        return RedirectResponse(url="/ui", status_code=302)

    form = await _parse_urlencoded_form(request=request)
    passphrase = str(form.get("passphrase", ""))
    candidate = passphrase.strip()
    if not candidate:
        return _render_setup_step(
            request=request,
            step="unlock",
            error_message="Unlock passphrase cannot be empty.",
            status_code=400,
        )

    try:
        require_sensitive_operation_unlock()
    except SensitiveOperationLockedError:
        unlock_with_passphrase(passphrase=candidate)

    return _render_setup_step(request=request, step="auth-start")


@router.post(
    "/ui/setup/start-auth",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def post_setup_start_auth(
    request: Request,
) -> Response:
    """Start Telegram auth session after unlock step succeeds."""
    if await _has_persisted_account(request=request):
        return RedirectResponse(url="/ui", status_code=302)

    form = await _parse_urlencoded_form(request=request)
    api_id_value = str(form.get("api_id", "")).strip()
    api_hash = str(form.get("api_hash", "")).strip()
    phone_number = str(form.get("phone_number", "")).strip()
    if not api_id_value or not api_hash or not phone_number:
        return _render_setup_step(
            request=request,
            step="auth-start",
            error_message="Missing required credentials fields.",
            status_code=400,
        )
    try:
        api_id = int(api_id_value)
    except ValueError:
        return _render_setup_step(
            request=request,
            step="auth-start",
            error_message="API ID must be an integer.",
            status_code=400,
        )

    try:
        require_sensitive_operation_unlock()
    except SensitiveOperationLockedError:
        return _render_setup_step(
            request=request,
            step="unlock",
            error_message="Setup step transition is invalid.",
            status_code=409,
        )

    try:
        started = await start_telegram_auth(
            TelegramAuthStartRequest(
                api_id=api_id,
                api_hash=api_hash,
                phone_number=phone_number,
            ),
            request,
        )
    except HTTPException as exc:
        return _render_setup_step(
            request=request,
            step="auth-start",
            error_message=str(exc.detail),
            status_code=exc.status_code,
        )

    return _render_setup_step(
        request=request,
        step="verify-code",
        step_context={
            "session_id": started.session_id,
            "api_id": api_id,
            "api_hash": api_hash,
        },
    )


@router.post(
    "/ui/setup/verify-code",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def post_setup_verify_code(
    request: Request,
) -> Response:
    """Submit OTP code and advance setup to password or completion."""
    if await _has_persisted_account(request=request):
        return RedirectResponse(url="/ui", status_code=302)

    form = await _parse_urlencoded_form(request=request)
    session_id = str(form.get("session_id", "")).strip()
    api_id_value = str(form.get("api_id", "")).strip()
    api_hash = str(form.get("api_hash", "")).strip()
    code = str(form.get("code", "")).strip()
    api_id = _parse_int(value=api_id_value)
    if api_id is None or not api_hash or not code:
        return _render_setup_step(
            request=request,
            step="verify-code",
            error_message="Missing required OTP fields.",
            status_code=400,
        )

    if not session_id:
        return _render_setup_step(
            request=request,
            step="auth-start",
            error_message="Setup step transition is invalid.",
            status_code=409,
        )

    try:
        verified = await verify_telegram_code(
            TelegramAuthVerifyCodeRequest(
                session_id=session_id,
                api_id=api_id,
                api_hash=api_hash,
                code=code,
            ),
            request,
        )
    except HTTPException as exc:
        return _render_setup_step(
            request=request,
            step="verify-code",
            error_message=str(exc.detail),
            status_code=exc.status_code,
            step_context={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
            },
        )

    if verified.status == "password_required":
        return _render_setup_step(
            request=request,
            step="verify-password",
            step_context={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
            },
        )

    return _render_setup_step(request=request, step="complete")


@router.post(
    "/ui/setup/verify-password",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def post_setup_verify_password(
    request: Request,
) -> Response:
    """Submit 2FA password and complete setup when authenticated."""
    if await _has_persisted_account(request=request):
        return RedirectResponse(url="/ui", status_code=302)

    form = await _parse_urlencoded_form(request=request)
    session_id = str(form.get("session_id", "")).strip()
    api_id_value = str(form.get("api_id", "")).strip()
    api_hash = str(form.get("api_hash", "")).strip()
    password = str(form.get("password", "")).strip()
    if not api_id_value or not api_hash or not password:
        return _render_setup_step(
            request=request,
            step="verify-password",
            error_message="Missing required password fields.",
            status_code=400,
        )
    try:
        api_id = int(api_id_value)
    except ValueError:
        return _render_setup_step(
            request=request,
            step="verify-password",
            error_message="API ID must be an integer.",
            status_code=400,
        )

    if not session_id:
        return _render_setup_step(
            request=request,
            step="auth-start",
            error_message="Setup step transition is invalid.",
            status_code=409,
        )

    try:
        _ = await verify_telegram_password(
            TelegramAuthVerifyPasswordRequest(
                session_id=session_id,
                api_id=api_id,
                api_hash=api_hash,
                password=password,
            ),
            request,
        )
    except HTTPException as exc:
        return _render_setup_step(
            request=request,
            step="verify-password",
            error_message=str(exc.detail),
            status_code=exc.status_code,
            step_context={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
            },
        )

    return _render_setup_step(request=request, step="complete")


def _render_setup_step(
    *,
    request: Request,
    step: str,
    status_code: int = 200,
    error_message: str | None = None,
    step_context: dict[str, object] | None = None,
) -> HTMLResponse:
    context: dict[str, object] = {
        "page_title": "TCA Setup Wizard",
        "step": step,
        "error_message": error_message,
    }
    if step_context is not None:
        context.update(step_context)
    return templates.TemplateResponse(
        request=request,
        name="setup_wizard.html",
        context=context,
        status_code=status_code,
    )


async def _has_persisted_account(*, request: Request) -> bool:
    runtime = _resolve_storage_runtime(request=request)
    async with runtime.read_session_factory() as session:
        result = await session.execute(text("SELECT 1 FROM telegram_accounts LIMIT 1"))
        return result.scalar_one_or_none() is not None


def _resolve_storage_runtime(*, request: Request) -> StorageRuntime:
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    state_obj = cast("object", getattr(app_obj, "state", None))
    runtime_obj = getattr(state_obj, "storage_runtime", None)
    if not isinstance(runtime_obj, StorageRuntime):
        message = "Missing app storage runtime: app.state.storage_runtime."
        raise TypeError(message)
    return runtime_obj


async def _parse_urlencoded_form(*, request: Request) -> dict[str, str]:
    raw_body = (await request.body()).decode("utf-8", errors="ignore")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}


def _parse_int(*, value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
