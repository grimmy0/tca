"""UI shell routes and static asset wiring."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.staticfiles import StaticFiles

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
