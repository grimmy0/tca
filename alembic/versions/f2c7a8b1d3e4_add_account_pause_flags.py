"""Add pause state columns for Telegram accounts."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f2c7a8b1d3e4"
down_revision = "d4b1e2c7a9f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add pause state metadata for Telegram accounts."""
    with op.batch_alter_table("telegram_accounts") as batch_op:
        batch_op.add_column(sa.Column("paused_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("pause_reason", sa.Text()))


def downgrade() -> None:
    """Remove pause state metadata for Telegram accounts."""
    with op.batch_alter_table("telegram_accounts") as batch_op:
        batch_op.drop_column("pause_reason")
        batch_op.drop_column("paused_at")
