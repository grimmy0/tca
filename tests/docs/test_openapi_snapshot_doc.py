"""Contract checks for OpenAPI snapshot refresh documentation."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUIDE_PATH = PROJECT_ROOT / "docs" / "testing-guide.md"


def test_testing_guide_documents_openapi_snapshot_update_process() -> None:
    """Ensure guide includes required command flow for snapshot refresh."""
    text = GUIDE_PATH.read_text(encoding="utf-8")
    required_fragments = [
        "tests/api/test_openapi_snapshot.py",
        "tests/api/snapshots/config_groups_openapi_snapshot.json",
        "TCA_UPDATE_OPENAPI_SNAPSHOT=1",
        "uv run pytest -q tests/api/test_openapi_snapshot.py",
    ]
    for fragment in required_fragments:
        if fragment not in text:
            raise AssertionError
