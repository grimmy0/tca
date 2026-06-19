"""Add items embedding column."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f9a1b2c3d4e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add optional embedding BLOB column to items table."""
    op.add_column(
        "items",
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    """Remove embedding column from items table."""
    op.drop_column("items", "embedding")
