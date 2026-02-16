"""Add auth session state table for login wizard."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e7c8d9f0a1b2"
down_revision = "d4b1e2c7a9f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create auth session state storage."""
    op.create_table(
        "auth_session_state",
        sa.Column("session_id", sa.String(length=128), primary_key=True),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    """Remove auth session state storage."""
    op.drop_table("auth_session_state")
