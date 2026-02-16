"""Add FTS5 triggers to sync `items` rows into `items_fts`."""

from __future__ import annotations

from alembic import op

revision = "a1f6e7c9d2b4"
down_revision = "8f3a7b0c1d2e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create C017 triggers that keep the FTS index synchronized."""
    op.execute(
        """
CREATE TRIGGER items_fts_ai AFTER INSERT ON items
BEGIN
    INSERT INTO items_fts(rowid, title, body)
    VALUES (new.id, new.title, new.body);
END
""".strip(),
    )
    op.execute(
        """
CREATE TRIGGER items_fts_ad AFTER DELETE ON items
BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
END
""".strip(),
    )
    op.execute(
        """
CREATE TRIGGER items_fts_au AFTER UPDATE ON items
BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO items_fts(rowid, title, body)
    VALUES (new.id, new.title, new.body);
END
""".strip(),
    )


def downgrade() -> None:
    """Drop C017 synchronization triggers."""
    op.execute("DROP TRIGGER IF EXISTS items_fts_au")
    op.execute("DROP TRIGGER IF EXISTS items_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS items_fts_ai")
