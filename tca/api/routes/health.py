"""Health check endpoint for application monitoring."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Stable response model for the unauthenticated health endpoint."""

    status: Literal["ok"]
    timestamp: datetime


@router.get("/health", tags=["monitoring"], response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Return application health status and current timestamp."""
    return HealthResponse(status="ok", timestamp=datetime.now(tz=UTC))
