"""UI shell routes and static asset wiring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
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
    StorageRuntime,
    WriterQueueProtocol,
)

_UI_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=(_UI_DIR / "templates").as_posix())
static_files = StaticFiles(directory=(_UI_DIR / "static").as_posix())

router = APIRouter(tags=["ui"])


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


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def get_ui_shell(request: Request) -> HTMLResponse:
    """Render the minimal authenticated shell page."""
    return templates.TemplateResponse(
        request=request,
        name="shell.html",
        context={"page_title": "TCA"},
    )


@router.get("/ui/channels-groups", response_class=HTMLResponse, include_in_schema=False)
async def get_channels_groups_view(request: Request) -> HTMLResponse:
    """Render channels/groups management page with editable form controls."""
    return await _render_channels_groups_view(request=request)


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
    except IntegrityError:
        return await _render_channels_groups_view(
            request=request,
            status_code=409,
            error_message="Channel create conflict: duplicate telegram channel id.",
        )

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
        default_account_obj = account_result.scalar_one_or_none()

    channels = [_decode_ui_channel_row(row=row) for row in channel_rows]
    groups = [_decode_ui_group_row(row=row) for row in group_rows]
    if default_account_obj is None:
        return channels, groups, None
    if not isinstance(default_account_obj, int):
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
        _ = await groups_repository.add_channel_membership(
            group_id=created.id,
            channel_id=values.channel_id,
        )
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

    if current_channel_id is not None and current_channel_id != desired_channel_id:
        _ = await groups_repository.remove_channel_membership(
            group_id=group_id,
            channel_id=current_channel_id,
        )
    if current_channel_id != desired_channel_id:
        _ = await groups_repository.add_channel_membership(
            group_id=group_id,
            channel_id=desired_channel_id,
        )
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
        channel_id_obj = result.scalar_one_or_none()
    if channel_id_obj is None:
        return None
    if not isinstance(channel_id_obj, int):
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
