"""Tests for the /health endpoint."""

from __future__ import annotations

from typing import cast

import httpx
from fastapi.testclient import TestClient

from tca.api.app import create_app


def test_get_health_returns_ok() -> None:
    """Ensure GET /health returns 200 and deterministic schema."""
    app = create_app()
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == httpx.codes.OK  # noqa: S101
    data = cast("dict[str, object]", response.json())
    assert data["status"] == "ok"  # noqa: S101
    assert "timestamp" in data  # noqa: S101
