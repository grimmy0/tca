"""UI shell routes and static asset wiring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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
from tca.storage import (
    ChannelAlreadyAssignedToGroupError,
    ChannelGroupsRepository,
    ChannelsRepository,
    NotificationListRecord,
    NotificationsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

_UI_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=(_UI_DIR / "templates").as_posix())
static_files = StaticFiles(directory=(_UI_DIR / "static").as_posix())

router = APIRouter(tags=["ui"])
DEFAULT_THREAD_PAGE = 1
DEFAULT_THREAD_PAGE_SIZE = 20
MAX_THREAD_PAGE_SIZE = 100


@dataclass(slots=True, frozen=True)
class UIChannelRow:
    """Typed row payload used by channels/groups UI template rendering."""

    id: int
    account_id: int
    telegram_channel_id: int
    name: str
    username: str | None
    is_enabled: bool
    group_id: int | None


@dataclass(slots=True, frozen=True)
class UIGroupRow:
    """Typed row payload used by channels/groups UI template rendering."""

    id: int
    name: str
    description: str | None
    dedupe_horizon_minutes_override: int | None
    channel_id: int | None


@dataclass(slots=True, frozen=True)
class UIGroupFormValues:
    """Parsed group form payload used by create/edit handlers."""

    name: str
    description: str | None
    dedupe_horizon_minutes_override: int | None
    channel_id: int | None


@dataclass(slots=True, frozen=True)
class UIThreadEntryRow:
    """Typed row payload used by thread UI template rendering."""

    cluster_id: int
    cluster_key: str
    representative_item_id: int
    representative_published_at: str | None
    representative_title: str | None
    representative_body: str | None
    representative_canonical_url: str | None
    representative_channel_id: int
    representative_channel_name: str
    duplicate_count: int
    source_channel_names: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class UIThreadDecisionRow:
    """Typed row payload used by thread decision drill-down rendering."""

    decision_id: int
    item_id: int
    cluster_id: int | None
    candidate_item_id: int | None
    strategy_name: str
    outcome: str
    reason_code: str | None
    score: float | None
    metadata_json: str | None
    created_at: str


@dataclass(slots=True, frozen=True)
class UIThreadFilterChannelRow:
    """Typed channel option payload for thread filter controls."""

    id: int
    name: str


@dataclass(slots=True, frozen=True)
class UIThreadViewState:
    """Parsed thread UI query state used by render helpers."""

    page: int
    size: int
    selected_channel_id: int | None
    selected_item_id: int | None


@dataclass(slots=True, frozen=True)
class UINotificationRow:
    """Typed row payload used by notifications UI template rendering."""

    id: int
    type: str
    severity: str
    message: str
    is_acknowledged: bool
    acknowledged_at: str | None
    created_at: str
    is_high_severity: bool


class SupportsIsoformat(Protocol):
    """Protocol for datetime-like objects exposing isoformat()."""

    def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
        """Render datetime-like value into ISO-8601 text."""
        ...


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def get_ui_shell(request: Request) -> HTMLResponse:
    """Render the minimal authenticated shell page."""
    return templates.TemplateResponse(
        request=request,
        name="shell.html",
        context={"page_title": "TCA"},
    )


@router.get("/ui/thread", response_class=HTMLResponse, include_in_schema=False)
async def get_thread_view(request: Request) -> HTMLResponse:
    """Render merged thread page with pagination, filter controls, and drill-down."""
    page_raw = str(request.query_params.get("page", str(DEFAULT_THREAD_PAGE))).strip()
    size_raw = str(
        request.query_params.get("size", str(DEFAULT_THREAD_PAGE_SIZE)),
    ).strip()
    channel_id_raw = str(request.query_params.get("channel_id", "")).strip()
    selected_item_raw = str(request.query_params.get("selected_item_id", "")).strip()

    page = _parse_int(value=page_raw)
    size = _parse_int(value=size_raw)
    parsed_channel_id = _parse_optional_int(raw_value=channel_id_raw)
    parsed_selected_item_id = _parse_optional_int(raw_value=selected_item_raw)

    if page is None or page < 1:
        return await _render_thread_view(
            request=request,
            state=UIThreadViewState(
                page=DEFAULT_THREAD_PAGE,
                size=DEFAULT_THREAD_PAGE_SIZE,
                selected_channel_id=None,
                selected_item_id=None,
            ),
            status_code=400,
            error_message="Thread page must be an integer greater than zero.",
        )
    if size is None or size < 1 or size > MAX_THREAD_PAGE_SIZE:
        return await _render_thread_view(
            request=request,
            state=UIThreadViewState(
                page=page,
                size=DEFAULT_THREAD_PAGE_SIZE,
                selected_channel_id=None,
                selected_item_id=None,
            ),
            status_code=400,
            error_message=(
                f"Thread page size must be between 1 and {MAX_THREAD_PAGE_SIZE}."
            ),
        )
    if parsed_channel_id == "invalid":
        return await _render_thread_view(
            request=request,
            state=UIThreadViewState(
                page=page,
                size=size,
                selected_channel_id=None,
                selected_item_id=None,
            ),
            status_code=400,
            error_message="Thread filter channel id must be an integer.",
        )
    if parsed_selected_item_id == "invalid":
        return await _render_thread_view(
            request=request,
            state=UIThreadViewState(
                page=page,
                size=size,
                selected_channel_id=parsed_channel_id,
                selected_item_id=None,
            ),
            status_code=400,
            error_message="Selected thread item id must be an integer.",
        )

    return await _render_thread_view(
        request=request,
        state=UIThreadViewState(
            page=page,
            size=size,
            selected_channel_id=parsed_channel_id,
            selected_item_id=parsed_selected_item_id,
        ),
    )


@router.get("/ui/channels-groups", response_class=HTMLResponse, include_in_schema=False)
async def get_channels_groups_view(request: Request) -> HTMLResponse:
    """Render channels/groups management page with editable form controls."""
    return await _render_channels_groups_view(request=request)


@router.get("/ui/notifications", response_class=HTMLResponse, include_in_schema=False)
async def get_notifications_view(request: Request) -> HTMLResponse:
    """Render notifications list and acknowledgement controls."""
    return await _render_notifications_view(request=request)


@router.post(
    "/ui/notifications/{notification_id}/ack",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def post_acknowledge_notification(
    notification_id: int,
    request: Request,
) -> Response:
    """Acknowledge one notification and redirect back to notifications view."""
    repository = _build_notifications_repository(request=request)
    writer_queue = _resolve_writer_queue(request=request)
    acknowledged = await writer_queue.submit(
        lambda: repository.acknowledge(notification_id=notification_id),
    )
    if acknowledged is None:
        return await _render_notifications_view(
            request=request,
            status_code=404,
            error_message=f"Notification '{notification_id}' was not found.",
        )
    return RedirectResponse(url="/ui/notifications", status_code=303)


@router.post("/ui/channels", response_class=HTMLResponse, include_in_schema=False)
async def post_create_channel(request: Request) -> Response:
    """Create one channel from UI form payload and redirect to management view."""
    form = await _parse_urlencoded_form(request=request)
    account_id_value = str(form.get("account_id", "")).strip()
    telegram_channel_id_value = str(form.get("telegram_channel_id", "")).strip()
    name = str(form.get("name", "")).strip()
    username_raw = str(form.get("username", "")).strip()

    account_id = _parse_int(value=account_id_value)
    telegram_channel_id = _parse_int(value=telegram_channel_id_value)
    if account_id is None or telegram_channel_id is None or not name:
        return await _render_channels_groups_view(
            request=request,
            status_code=400,
            error_message="Channel create requires account, channel id, and name.",
        )

    repository = _build_channels_repository(request=request)
    writer_queue = _resolve_writer_queue(request=request)
    try:
        _ = await writer_queue.submit(
            lambda: repository.create_channel(
                account_id=account_id,
                telegram_channel_id=telegram_channel_id,
                name=name,
                username=username_raw or None,
            ),
        )
    except IntegrityError as exc:
        if _is_duplicate_channel_integrity_error(exc=exc):
            return await _render_channels_groups_view(
                request=request,
                status_code=409,
                error_message="Channel create conflict: duplicate telegram channel id.",
            )
        if _is_channel_account_fk_integrity_error(exc=exc):
            return await _render_channels_groups_view(
                request=request,
                status_code=404,
                error_message=f"Account '{account_id}' was not found.",
            )
        raise

    return RedirectResponse(url="/ui/channels-groups", status_code=303)


@router.post(
    "/ui/channels/{channel_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def post_edit_channel(channel_id: int, request: Request) -> Response:
    """Edit mutable channel fields from UI and persist via writer queue."""
    form = await _parse_urlencoded_form(request=request)
    name = str(form.get("name", "")).strip()
    username_raw = str(form.get("username", "")).strip()
    if not name:
        return await _render_channels_groups_view(
            request=request,
            status_code=400,
            error_message="Channel name cannot be empty.",
        )

    repository = _build_channels_repository(request=request)
    writer_queue = _resolve_writer_queue(request=request)

    async def _update() -> bool:
        current = await repository.get_channel_by_id(channel_id=channel_id)
        if current is None:
            return False
        updated = await repository.update_channel(
            channel_id=channel_id,
            name=name,
            username=username_raw or None,
            is_enabled=current.is_enabled,
        )
        return updated is not None

    updated = await writer_queue.submit(_update)
    if not updated:
        return await _render_channels_groups_view(
            request=request,
            status_code=404,
            error_message=f"Channel '{channel_id}' was not found.",
        )

    return RedirectResponse(url="/ui/channels-groups", status_code=303)


@router.post(
    "/ui/channels/{channel_id}/disable",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def post_disable_channel(channel_id: int, request: Request) -> Response:
    """Disable one channel from UI and keep historical rows intact."""
    repository = _build_channels_repository(request=request)
    writer_queue = _resolve_writer_queue(request=request)
    disabled = await writer_queue.submit(
        lambda: repository.disable_channel(channel_id=channel_id),
    )
    if disabled is None:
        return await _render_channels_groups_view(
            request=request,
            status_code=404,
            error_message=f"Channel '{channel_id}' was not found.",
        )
    return RedirectResponse(url="/ui/channels-groups", status_code=303)


@router.post("/ui/groups", response_class=HTMLResponse, include_in_schema=False)
async def post_create_group(request: Request) -> Response:
    """Create one channel group and optionally assign one channel member."""
    form = await _parse_urlencoded_form(request=request)
    parsed_values = _parse_group_form_values(form=form)
    if isinstance(parsed_values, str):
        return await _render_channels_groups_view(
            request=request,
            status_code=400,
            error_message=parsed_values,
        )

    groups_repository = _build_channel_groups_repository(request=request)
    channels_repository = _build_channels_repository(request=request)
    writer_queue = _resolve_writer_queue(request=request)

    try:
        created = await writer_queue.submit(
            lambda: _create_group_from_form_values(
                values=parsed_values,
                groups_repository=groups_repository,
                channels_repository=channels_repository,
            ),
        )
    except ChannelAlreadyAssignedToGroupError as exc:
        return await _render_channels_groups_view(
            request=request,
            status_code=409,
            error_message=str(exc),
        )
    if not created:
        return await _render_channels_groups_view(
            request=request,
            status_code=404,
            error_message="Assigned channel was not found.",
        )
    return RedirectResponse(url="/ui/channels-groups", status_code=303)


@router.post(
    "/ui/groups/{group_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def post_edit_group(group_id: int, request: Request) -> Response:
    """Edit one group row including persisted horizon override field."""
    form = await _parse_urlencoded_form(request=request)
    parsed_values = _parse_group_form_values(form=form, include_channel=False)
    if isinstance(parsed_values, str):
        return await _render_channels_groups_view(
            request=request,
            status_code=400,
            error_message=parsed_values,
        )

    repository = _build_channel_groups_repository(request=request)
    writer_queue = _resolve_writer_queue(request=request)
    updated = await writer_queue.submit(
        lambda: repository.update_group(
            group_id=group_id,
            name=parsed_values.name,
            description=parsed_values.description,
            dedupe_horizon_minutes_override=parsed_values.dedupe_horizon_minutes_override,
        ),
    )
    if updated is None:
        return await _render_channels_groups_view(
            request=request,
            status_code=404,
            error_message=f"Channel group '{group_id}' was not found.",
        )
    return RedirectResponse(url="/ui/channels-groups", status_code=303)


@router.post(
    "/ui/groups/{group_id}/channel",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def post_assign_group_channel(group_id: int, request: Request) -> Response:
    """Assign or clear one channel membership for a group from UI controls."""
    form = await _parse_urlencoded_form(request=request)
    channel_id = _parse_channel_assignment(form=form)
    if channel_id == "invalid":
        return await _render_channels_groups_view(
            request=request,
            status_code=400,
            error_message="Group channel assignment must be an integer.",
        )

    runtime = _resolve_storage_runtime(request=request)
    groups_repository = _build_channel_groups_repository(request=request)
    channels_repository = _build_channels_repository(request=request)
    writer_queue = _resolve_writer_queue(request=request)

    try:
        assigned = await writer_queue.submit(
            lambda: _assign_group_channel_membership(
                runtime=runtime,
                group_id=group_id,
                desired_channel_id=channel_id,
                groups_repository=groups_repository,
                channels_repository=channels_repository,
            ),
        )
    except ChannelAlreadyAssignedToGroupError as exc:
        return await _render_channels_groups_view(
            request=request,
            status_code=409,
            error_message=str(exc),
        )

    if not assigned:
        return await _render_channels_groups_view(
            request=request,
            status_code=404,
            error_message="Requested group or channel was not found.",
        )
    return RedirectResponse(url="/ui/channels-groups", status_code=303)


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


async def _render_channels_groups_view(
    *,
    request: Request,
    status_code: int = 200,
    error_message: str | None = None,
) -> HTMLResponse:
    channels, groups, default_account_id = await _load_channels_groups_data(
        request=request,
    )
    return templates.TemplateResponse(
        request=request,
        name="channels_groups.html",
        context={
            "page_title": "TCA Channels + Groups",
            "channels": channels,
            "groups": groups,
            "default_account_id": default_account_id,
            "error_message": error_message,
        },
        status_code=status_code,
    )


async def _render_thread_view(
    *,
    request: Request,
    state: UIThreadViewState,
    status_code: int = 200,
    error_message: str | None = None,
) -> HTMLResponse:
    entries, has_next_page = await _load_thread_entries(
        request=request,
        page=state.page,
        size=state.size,
        selected_channel_id=state.selected_channel_id,
    )
    filter_channels = await _load_thread_filter_channels(request=request)
    selected_decisions = await _load_thread_decisions_for_item(
        request=request,
        selected_item_id=state.selected_item_id,
    )
    return templates.TemplateResponse(
        request=request,
        name="thread.html",
        context={
            "page_title": "TCA Thread",
            "thread_entries": entries,
            "thread_filter_channels": filter_channels,
            "thread_selected_channel_id": state.selected_channel_id,
            "thread_selected_item_id": state.selected_item_id,
            "thread_selected_decisions": selected_decisions,
            "thread_page": state.page,
            "thread_size": state.size,
            "thread_has_prev_page": state.page > 1,
            "thread_has_next_page": has_next_page,
            "error_message": error_message,
        },
        status_code=status_code,
    )


async def _render_notifications_view(
    *,
    request: Request,
    status_code: int = 200,
    error_message: str | None = None,
) -> HTMLResponse:
    notifications = await _load_notifications(request=request)
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={
            "page_title": "TCA Notifications",
            "notifications": notifications,
            "error_message": error_message,
        },
        status_code=status_code,
    )


async def _load_notifications(
    *,
    request: Request,
) -> list[UINotificationRow]:
    repository = _build_notifications_repository(request=request)
    records = await repository.list_notifications()
    return [_to_ui_notification_row(record=record) for record in records]


def _to_ui_notification_row(*, record: NotificationListRecord) -> UINotificationRow:
    return UINotificationRow(
        id=record.notification_id,
        type=record.type,
        severity=record.severity,
        message=record.message,
        is_acknowledged=record.is_acknowledged,
        acknowledged_at=_format_datetime(record.acknowledged_at),
        created_at=_format_datetime(record.created_at) or "",
        is_high_severity=record.severity.strip().lower() == "high",
    )


def _format_datetime(value: object) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if not callable(isoformat):
        message = "Expected datetime-compatible object."
        raise TypeError(message)
    typed_value = cast("SupportsIsoformat", value)
    return typed_value.isoformat(sep=" ", timespec="seconds")


async def _load_thread_entries(
    *,
    request: Request,
    page: int,
    size: int,
    selected_channel_id: int | None,
) -> tuple[list[UIThreadEntryRow], bool]:
    runtime = _resolve_storage_runtime(request=request)
    offset = (page - 1) * size
    limit = size + 1
    statement = text(
        """
        SELECT
            clusters.id AS cluster_id,
            clusters.cluster_key AS cluster_key,
            representative.id AS representative_item_id,
            representative.published_at AS representative_published_at,
            representative.title AS representative_title,
            representative.body AS representative_body,
            representative.canonical_url AS representative_canonical_url,
            representative_channel.id AS representative_channel_id,
            representative_channel.name AS representative_channel_name,
            COUNT(DISTINCT members.item_id) AS duplicate_count,
            COALESCE(
                GROUP_CONCAT(DISTINCT member_channels.name),
                representative_channel.name
            ) AS source_channel_names
        FROM dedupe_clusters AS clusters
        INNER JOIN items AS representative
            ON representative.id = clusters.representative_item_id
        INNER JOIN telegram_channels AS representative_channel
            ON representative_channel.id = representative.channel_id
        LEFT JOIN dedupe_members AS members
            ON members.cluster_id = clusters.id
        LEFT JOIN items AS member_items
            ON member_items.id = members.item_id
        LEFT JOIN telegram_channels AS member_channels
            ON member_channels.id = member_items.channel_id
        WHERE :selected_channel_id IS NULL
            OR representative.channel_id = :selected_channel_id
        GROUP BY
            clusters.id,
            clusters.cluster_key,
            representative.id,
            representative.published_at,
            representative.title,
            representative.body,
            representative.canonical_url,
            representative_channel.id,
            representative_channel.name
        ORDER BY
            CASE
                WHEN representative.published_at IS NULL THEN 1
                ELSE 0
            END ASC,
            representative.published_at DESC,
            clusters.id DESC
        LIMIT :limit
        OFFSET :offset
        """,
    )
    async with runtime.read_session_factory() as session:
        result = await session.execute(
            statement,
            {
                "selected_channel_id": selected_channel_id,
                "limit": limit,
                "offset": offset,
            },
        )
        rows = result.mappings().all()

    has_next = len(rows) > size
    bounded_rows = rows[:size]
    return [_decode_ui_thread_entry_row(row=row) for row in bounded_rows], has_next


async def _load_thread_filter_channels(
    *,
    request: Request,
) -> list[UIThreadFilterChannelRow]:
    runtime = _resolve_storage_runtime(request=request)
    statement = text(
        """
        SELECT id, name
        FROM telegram_channels
        ORDER BY name ASC, id ASC
        """,
    )
    async with runtime.read_session_factory() as session:
        result = await session.execute(statement)
        rows = result.mappings().all()
    channels: list[UIThreadFilterChannelRow] = []
    for row in rows:
        row_map = cast("dict[str, object]", cast("object", row))
        channel_id = _require_int_field(row_map=row_map, field_name="id")
        name = _require_text_field(row_map=row_map, field_name="name")
        channels.append(UIThreadFilterChannelRow(id=channel_id, name=name))
    return channels


async def _load_thread_decisions_for_item(
    *,
    request: Request,
    selected_item_id: int | None,
) -> list[UIThreadDecisionRow]:
    if selected_item_id is None:
        return []

    runtime = _resolve_storage_runtime(request=request)
    statement = text(
        """
        SELECT
            id,
            item_id,
            cluster_id,
            candidate_item_id,
            strategy_name,
            outcome,
            reason_code,
            score,
            metadata_json,
            created_at
        FROM dedupe_decisions
        WHERE item_id = :item_id
        ORDER BY id ASC
        """,
    )
    async with runtime.read_session_factory() as session:
        result = await session.execute(statement, {"item_id": selected_item_id})
        rows = result.mappings().all()
    return [_decode_ui_thread_decision_row(row=row) for row in rows]


def _decode_ui_thread_entry_row(*, row: object) -> UIThreadEntryRow:
    row_map = cast("dict[str, object]", row)
    published_obj = row_map.get("representative_published_at")
    if published_obj is not None and not isinstance(published_obj, str):
        message = "Expected `representative_published_at` to be text or null."
        raise TypeError(message)
    title_obj = row_map.get("representative_title")
    if title_obj is not None and not isinstance(title_obj, str):
        message = "Expected `representative_title` to be text or null."
        raise TypeError(message)
    body_obj = row_map.get("representative_body")
    if body_obj is not None and not isinstance(body_obj, str):
        message = "Expected `representative_body` to be text or null."
        raise TypeError(message)
    url_obj = row_map.get("representative_canonical_url")
    if url_obj is not None and not isinstance(url_obj, str):
        message = "Expected `representative_canonical_url` to be text or null."
        raise TypeError(message)
    duplicate_count_obj = row_map.get("duplicate_count")
    if type(duplicate_count_obj) is int:
        duplicate_count = duplicate_count_obj
    elif isinstance(duplicate_count_obj, str) and duplicate_count_obj.isdigit():
        duplicate_count = int(duplicate_count_obj)
    else:
        message = "Expected `duplicate_count` to be an integer."
        raise TypeError(message)
    source_names = _parse_source_channel_names(
        source_channel_names=row_map.get("source_channel_names"),
    )
    return UIThreadEntryRow(
        cluster_id=_require_int_field(row_map=row_map, field_name="cluster_id"),
        cluster_key=_require_text_field(row_map=row_map, field_name="cluster_key"),
        representative_item_id=_require_int_field(
            row_map=row_map,
            field_name="representative_item_id",
        ),
        representative_published_at=published_obj,
        representative_title=title_obj,
        representative_body=body_obj,
        representative_canonical_url=url_obj,
        representative_channel_id=_require_int_field(
            row_map=row_map,
            field_name="representative_channel_id",
        ),
        representative_channel_name=_require_text_field(
            row_map=row_map,
            field_name="representative_channel_name",
        ),
        duplicate_count=duplicate_count,
        source_channel_names=source_names,
    )


def _decode_ui_thread_decision_row(*, row: object) -> UIThreadDecisionRow:
    row_map = cast("dict[str, object]", row)
    cluster_obj = row_map.get("cluster_id")
    if cluster_obj is not None and type(cluster_obj) is not int:
        message = "Expected `cluster_id` to be integer or null."
        raise TypeError(message)
    candidate_obj = row_map.get("candidate_item_id")
    if candidate_obj is not None and type(candidate_obj) is not int:
        message = "Expected `candidate_item_id` to be integer or null."
        raise TypeError(message)
    score_obj = row_map.get("score")
    if score_obj is None:
        score: float | None = None
    elif type(score_obj) in (int, float):
        score = float(cast("int | float", score_obj))
    else:
        message = "Expected `score` to be number or null."
        raise TypeError(message)
    reason_obj = row_map.get("reason_code")
    if reason_obj is not None and not isinstance(reason_obj, str):
        message = "Expected `reason_code` to be text or null."
        raise TypeError(message)
    metadata_obj = row_map.get("metadata_json")
    if metadata_obj is not None and not isinstance(metadata_obj, str):
        message = "Expected `metadata_json` to be text or null."
        raise TypeError(message)
    created_at_obj = row_map.get("created_at")
    if not isinstance(created_at_obj, str):
        message = "Expected `created_at` to be text."
        raise TypeError(message)
    return UIThreadDecisionRow(
        decision_id=_require_int_field(row_map=row_map, field_name="id"),
        item_id=_require_int_field(row_map=row_map, field_name="item_id"),
        cluster_id=cluster_obj,
        candidate_item_id=candidate_obj,
        strategy_name=_require_text_field(row_map=row_map, field_name="strategy_name"),
        outcome=_require_text_field(row_map=row_map, field_name="outcome"),
        reason_code=reason_obj,
        score=score,
        metadata_json=metadata_obj,
        created_at=created_at_obj,
    )


def _parse_source_channel_names(*, source_channel_names: object) -> tuple[str, ...]:
    if source_channel_names is None:
        return ()
    if not isinstance(source_channel_names, str):
        message = "Expected `source_channel_names` to be text or null."
        raise TypeError(message)
    unique_names = sorted(
        {part.strip() for part in source_channel_names.split(",") if part.strip()},
    )
    return tuple(unique_names)


async def _load_channels_groups_data(
    *,
    request: Request,
) -> tuple[list[UIChannelRow], list[UIGroupRow], int | None]:
    runtime = _resolve_storage_runtime(request=request)
    channels_statement = text(
        """
        SELECT
            channels.id,
            channels.account_id,
            channels.telegram_channel_id,
            channels.name,
            channels.username,
            channels.is_enabled,
            members.group_id
        FROM telegram_channels AS channels
        LEFT JOIN channel_group_members AS members
            ON members.channel_id = channels.id
        ORDER BY channels.id ASC
        """,
    )
    groups_statement = text(
        """
        SELECT
            groups.id,
            groups.name,
            groups.description,
            groups.dedupe_horizon_minutes_override,
            members.channel_id
        FROM channel_groups AS groups
        LEFT JOIN channel_group_members AS members
            ON members.group_id = groups.id
        ORDER BY groups.id ASC
        """,
    )
    account_statement = text(
        """
        SELECT id
        FROM telegram_accounts
        ORDER BY id ASC
        LIMIT 1
        """,
    )
    async with runtime.read_session_factory() as session:
        channels_result = await session.execute(channels_statement)
        groups_result = await session.execute(groups_statement)
        account_result = await session.execute(account_statement)
        channel_rows = channels_result.mappings().all()
        group_rows = groups_result.mappings().all()
        default_account_obj = cast("object", account_result.scalar_one_or_none())

    channels = [_decode_ui_channel_row(row=row) for row in channel_rows]
    groups = [_decode_ui_group_row(row=row) for row in group_rows]
    if default_account_obj is None:
        return channels, groups, None
    if type(default_account_obj) is not int:
        message = "Expected integer `telegram_accounts.id` value."
        raise TypeError(message)
    return channels, groups, default_account_obj


def _decode_ui_channel_row(*, row: object) -> UIChannelRow:
    row_map = cast("dict[str, object]", row)
    channel_id = _require_int_field(row_map=row_map, field_name="id")
    account_id = _require_int_field(row_map=row_map, field_name="account_id")
    telegram_channel_id = _require_int_field(
        row_map=row_map,
        field_name="telegram_channel_id",
    )
    name = _require_text_field(row_map=row_map, field_name="name")
    username_obj = row_map.get("username")
    if username_obj is not None and not isinstance(username_obj, str):
        message = "Expected `username` to be text or null."
        raise TypeError(message)
    is_enabled_obj = row_map.get("is_enabled")
    if type(is_enabled_obj) is not bool and type(is_enabled_obj) is not int:
        message = "Expected `is_enabled` to be boolean."
        raise TypeError(message)
    group_obj = row_map.get("group_id")
    if group_obj is not None and type(group_obj) is not int:
        message = "Expected `group_id` to be integer or null."
        raise TypeError(message)
    return UIChannelRow(
        id=channel_id,
        account_id=account_id,
        telegram_channel_id=telegram_channel_id,
        name=name,
        username=username_obj,
        is_enabled=bool(is_enabled_obj),
        group_id=group_obj,
    )


def _decode_ui_group_row(*, row: object) -> UIGroupRow:
    row_map = cast("dict[str, object]", row)
    group_id = _require_int_field(row_map=row_map, field_name="id")
    name = _require_text_field(row_map=row_map, field_name="name")
    description_obj = row_map.get("description")
    if description_obj is not None and not isinstance(description_obj, str):
        message = "Expected `description` to be text or null."
        raise TypeError(message)
    horizon_obj = row_map.get("dedupe_horizon_minutes_override")
    if horizon_obj is not None and type(horizon_obj) is not int:
        message = "Expected `dedupe_horizon_minutes_override` to be integer or null."
        raise TypeError(message)
    channel_obj = row_map.get("channel_id")
    if channel_obj is not None and type(channel_obj) is not int:
        message = "Expected `channel_id` to be integer or null."
        raise TypeError(message)
    return UIGroupRow(
        id=group_id,
        name=name,
        description=description_obj,
        dedupe_horizon_minutes_override=horizon_obj,
        channel_id=channel_obj,
    )


def _require_int_field(*, row_map: dict[str, object], field_name: str) -> int:
    value = row_map.get(field_name)
    if type(value) is not int:
        message = f"Expected integer `{field_name}` field."
        raise TypeError(message)
    return value


def _require_text_field(*, row_map: dict[str, object], field_name: str) -> str:
    value = row_map.get(field_name)
    if not isinstance(value, str):
        message = f"Expected text `{field_name}` field."
        raise TypeError(message)
    return value


def _parse_group_form_values(
    *,
    form: dict[str, str],
    include_channel: bool = True,
) -> UIGroupFormValues | str:
    name = str(form.get("name", "")).strip()
    if not name:
        return "Group name cannot be empty."
    description_raw = str(form.get("description", "")).strip()
    horizon_raw = str(form.get("dedupe_horizon_minutes_override", "")).strip()
    parsed_horizon = _parse_optional_int(raw_value=horizon_raw)
    if parsed_horizon == "invalid":
        return "Group horizon override must be an integer."
    horizon_override = parsed_horizon
    channel_id: int | None = None
    if include_channel:
        parsed_channel = _parse_channel_assignment(form=form)
        if parsed_channel == "invalid":
            return "Group channel assignment must be an integer."
        channel_id = parsed_channel
    return UIGroupFormValues(
        name=name,
        description=description_raw or None,
        dedupe_horizon_minutes_override=horizon_override,
        channel_id=channel_id,
    )


def _parse_channel_assignment(
    *,
    form: dict[str, str],
) -> int | None | Literal["invalid"]:
    channel_id_raw = str(form.get("channel_id", "")).strip()
    return _parse_optional_int(raw_value=channel_id_raw)


async def _create_group_from_form_values(
    *,
    values: UIGroupFormValues,
    groups_repository: ChannelGroupsRepository,
    channels_repository: ChannelsRepository,
) -> bool:
    if values.channel_id is not None:
        channel = await channels_repository.get_channel_by_id(
            channel_id=values.channel_id,
        )
        if channel is None:
            return False
        membership = await groups_repository.get_membership_by_channel_id(
            channel_id=values.channel_id,
        )
        if membership is not None:
            raise ChannelAlreadyAssignedToGroupError.for_channel(values.channel_id)
    created = await groups_repository.create_group(
        name=values.name,
        description=values.description,
        dedupe_horizon_minutes_override=values.dedupe_horizon_minutes_override,
    )
    if values.channel_id is not None:
        try:
            _ = await groups_repository.add_channel_membership(
                group_id=created.id,
                channel_id=values.channel_id,
            )
        except IntegrityError as exc:
            _ = await groups_repository.delete_group(group_id=created.id)
            if _is_membership_channel_fk_integrity_error(exc=exc):
                return False
            raise
        except ChannelAlreadyAssignedToGroupError:
            _ = await groups_repository.delete_group(group_id=created.id)
            raise
    return True


async def _assign_group_channel_membership(
    *,
    runtime: StorageRuntime,
    group_id: int,
    desired_channel_id: int | None,
    groups_repository: ChannelGroupsRepository,
    channels_repository: ChannelsRepository,
) -> bool:
    group = await groups_repository.get_group_by_id(group_id=group_id)
    if group is None:
        return False

    current_channel_id = await _get_group_channel_assignment(
        runtime=runtime,
        group_id=group_id,
    )
    if desired_channel_id is None:
        if current_channel_id is not None:
            _ = await groups_repository.remove_channel_membership(
                group_id=group_id,
                channel_id=current_channel_id,
            )
        return True

    channel = await channels_repository.get_channel_by_id(channel_id=desired_channel_id)
    if channel is None:
        return False

    membership = await groups_repository.get_membership_by_channel_id(
        channel_id=desired_channel_id,
    )
    if membership is not None and membership.group_id != group_id:
        raise ChannelAlreadyAssignedToGroupError.for_channel(desired_channel_id)

    removed_current_assignment = False
    if current_channel_id is not None and current_channel_id != desired_channel_id:
        _ = await groups_repository.remove_channel_membership(
            group_id=group_id,
            channel_id=current_channel_id,
        )
        removed_current_assignment = True
    if current_channel_id != desired_channel_id:
        try:
            _ = await groups_repository.add_channel_membership(
                group_id=group_id,
                channel_id=desired_channel_id,
            )
        except (ChannelAlreadyAssignedToGroupError, IntegrityError):
            if removed_current_assignment and current_channel_id is not None:
                _ = await groups_repository.add_channel_membership(
                    group_id=group_id,
                    channel_id=current_channel_id,
                )
            raise
    return True


def _parse_optional_int(*, raw_value: str) -> int | None | Literal["invalid"]:
    value = raw_value.strip()
    if not value:
        return None
    parsed = _parse_int(value=value)
    if parsed is None:
        return "invalid"
    return parsed


async def _get_group_channel_assignment(
    *,
    runtime: StorageRuntime,
    group_id: int,
) -> int | None:
    statement = text(
        """
        SELECT channel_id
        FROM channel_group_members
        WHERE group_id = :group_id
        """,
    )
    async with runtime.read_session_factory() as session:
        result = await session.execute(statement, {"group_id": group_id})
        channel_id_obj = cast("object", result.scalar_one_or_none())
    if channel_id_obj is None:
        return None
    if type(channel_id_obj) is not int:
        message = "Expected integer `channel_id` in `channel_group_members` row."
        raise TypeError(message)
    return channel_id_obj


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


def _build_channels_repository(*, request: Request) -> ChannelsRepository:
    runtime = _resolve_storage_runtime(request=request)
    return ChannelsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _build_channel_groups_repository(*, request: Request) -> ChannelGroupsRepository:
    runtime = _resolve_storage_runtime(request=request)
    return ChannelGroupsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _build_notifications_repository(*, request: Request) -> NotificationsRepository:
    runtime = _resolve_storage_runtime(request=request)
    return NotificationsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _resolve_writer_queue(*, request: Request) -> WriterQueueProtocol:
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    state_obj = cast("object", getattr(app_obj, "state", None))
    queue_obj = cast("object", getattr(state_obj, "writer_queue", None))
    submit_obj = getattr(queue_obj, "submit", None)
    if not callable(submit_obj):
        message = "Missing app writer queue: app.state.writer_queue."
        raise TypeError(message)
    return cast("WriterQueueProtocol", queue_obj)


async def _parse_urlencoded_form(*, request: Request) -> dict[str, str]:
    raw_body = (await request.body()).decode("utf-8", errors="ignore")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values}


def _parse_int(*, value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _is_duplicate_channel_integrity_error(*, exc: IntegrityError) -> bool:
    message = _normalized_integrity_message(exc=exc)
    if "uq_telegram_channels_telegram_channel_id" in message:
        return True
    return (
        "unique constraint failed" in message
        and "telegram_channels.telegram_channel_id" in message
    )


def _is_channel_account_fk_integrity_error(*, exc: IntegrityError) -> bool:
    message = _normalized_integrity_message(exc=exc)
    if "fk_telegram_channels_account_id" in message:
        return True
    return "foreign key constraint failed" in message


def _is_membership_channel_fk_integrity_error(*, exc: IntegrityError) -> bool:
    message = _normalized_integrity_message(exc=exc)
    if "fk_channel_group_members_channel_id" in message:
        return True
    return "foreign key constraint failed" in message


def _normalized_integrity_message(*, exc: IntegrityError) -> str:
    driver_error = cast("object | None", getattr(exc, "orig", None))
    message_parts = [str(exc)]
    if driver_error is not None:
        message_parts.append(str(driver_error))
    return " ".join(message_parts).lower()
