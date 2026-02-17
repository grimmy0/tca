"""Add telegram session column to auth session state."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c4a3d2b1f6e8"
down_revision = "e7c8d9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add optional Telegram session payload for password-required flows."""
    op.add_column(
        "auth_session_state",
        sa.Column("telegram_session", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove Telegram session payload column."""
    op.drop_column("auth_session_state", "telegram_session")
