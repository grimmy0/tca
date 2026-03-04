"""Contract checks for final release checklist documentation."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKLIST_PATH = PROJECT_ROOT / "docs" / "release-checklist.md"


def test_release_checklist_covers_required_phase1_areas() -> None:
    """Ensure checklist explicitly covers all required release domains."""
    text = CHECKLIST_PATH.read_text(encoding="utf-8")
    required_sections = [
        "## Schema",
        "## Auth",
        "## Ingestion",
        "## Dedupe",
        "## UI",
        "## Backups",
        "## Shutdown",
    ]
    for section in required_sections:
        if section not in text:
            raise AssertionError


def test_release_checklist_items_map_to_test_or_manual_validation() -> None:
    """Ensure every checklist entry has a test or manual validation mapping."""
    text = CHECKLIST_PATH.read_text(encoding="utf-8")
    checklist_lines = [
        line.strip() for line in text.splitlines() if line.strip().startswith("- [ ]")
    ]
    if not checklist_lines:
        raise AssertionError

    for line in checklist_lines:
        if "[Test:" in line:
            continue
        if "[Manual:" in line:
            continue
        raise AssertionError

    manual_ids = set(re.findall(r"\[Manual:\s*`([^`]+)`\]", text))
    declared_steps = set(re.findall(r"^###\s+([A-Z0-9-]+)$", text, flags=re.MULTILINE))
    if not manual_ids.issubset(declared_steps):
        raise AssertionError


def test_release_checklist_contains_second_engineer_dry_run_flow() -> None:
    """Ensure checklist includes explicit execution flow for another engineer."""
    text = CHECKLIST_PATH.read_text(encoding="utf-8")
    required_fragments = [
        "## Dry-Run Procedure (Second Engineer)",
        "uv sync --frozen",
        "uv run alembic upgrade head",
        "Run each test command linked in checklist items below.",
        "Release decision:",
        "GO",
        "NO-GO",
    ]
    for fragment in required_fragments:
        if fragment not in text:
            raise AssertionError
