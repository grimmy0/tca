"""Add FTS5 external-content table for items."""

from __future__ import annotations

from alembic import op

revision = "8f3a7b0c1d2e"
down_revision = "c2f9c1e5a7b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create C016 external-content FTS table for item title/body search."""
    op.execute(
        """
CREATE VIRTUAL TABLE items_fts USING fts5(
    title,
    body,
    content='items',
    content_rowid='id'
)
""".strip(),
    )


def downgrade() -> None:
    """Drop C016 external-content FTS table."""
    op.execute("DROP TABLE IF EXISTS items_fts")
