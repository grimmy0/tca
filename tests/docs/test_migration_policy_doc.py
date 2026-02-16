"""Contract checks for SQLite migration policy documentation."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT / "docs" / "migration-policy.md"
PLAN_PATH = PROJECT_ROOT / "docs" / "implementation-plan.md"


def test_policy_references_sqlite_alter_table_and_batch_requirement() -> None:
    """Ensure policy states SQLite ALTER TABLE limits and batch mode requirement."""
    text = POLICY_PATH.read_text(encoding="utf-8")
    required_fragments = [
        "SQLite",
        "ALTER TABLE",
        "render_as_batch=True",
    ]
    for fragment in required_fragments:
        if fragment not in text:
            raise AssertionError


def test_policy_includes_concise_migration_checklist() -> None:
    """Ensure checklist covers pre-checks, lock considerations, rollback."""
    text = POLICY_PATH.read_text(encoding="utf-8")
    required_sections = [
        "Pre-checks",
        "Lock considerations",
        "Rollback expectations",
    ]
    for section in required_sections:
        if section not in text:
            raise AssertionError


def test_c011_section_references_migration_policy_document() -> None:
    """Ensure C011 implementation notes reference migration policy document."""
    text = PLAN_PATH.read_text(encoding="utf-8")
    c011_anchor = "### C011 - Initialize Alembic with SQLite Batch Mode"
    c011a_anchor = "### C011A - Add Migration Policy Note for SQLite Batch Mode"
    c011_start = text.find(c011_anchor)
    c011a_start = text.find(c011a_anchor)
    if c011_start < 0 or c011a_start < 0:
        raise AssertionError
    c011_section = text[c011_start:c011a_start]
    if "docs/migration-policy.md" not in c011_section:
        raise AssertionError
