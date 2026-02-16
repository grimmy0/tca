"""Health check endpoint for application monitoring."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["monitoring"])
async def get_health() -> dict[str, object]:
    """Return application health status and current timestamp."""
    return {
        "status": "ok",
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }
