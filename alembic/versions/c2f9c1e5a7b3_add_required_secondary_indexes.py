"""Add required secondary indexes."""

from __future__ import annotations

from alembic import op

revision = "c2f9c1e5a7b3"
down_revision = "9c2a8f6d0f7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create C015 secondary indexes required by the design."""
    op.create_index("ix_items_published_at", "items", ["published_at"], unique=False)
    op.create_index(
        "ix_items_canonical_url_hash",
        "items",
        ["canonical_url_hash"],
        unique=False,
    )
    op.create_index("ix_items_content_hash", "items", ["content_hash"], unique=False)
    op.create_index(
        "ix_dedupe_members_item_id",
        "dedupe_members",
        ["item_id"],
        unique=False,
    )
    op.create_index(
        "ix_dedupe_clusters_representative_item_id",
        "dedupe_clusters",
        ["representative_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_ingest_errors_created_at",
        "ingest_errors",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop C015 secondary indexes."""
    op.drop_index("ix_ingest_errors_created_at", table_name="ingest_errors")
    op.drop_index(
        "ix_dedupe_clusters_representative_item_id",
        table_name="dedupe_clusters",
    )
    op.drop_index("ix_dedupe_members_item_id", table_name="dedupe_members")
    op.drop_index("ix_items_content_hash", table_name="items")
    op.drop_index("ix_items_canonical_url_hash", table_name="items")
    op.drop_index("ix_items_published_at", table_name="items")
