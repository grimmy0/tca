"""Add poll jobs queue table."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f9a1b2c3d4e6"
down_revision = "b1a2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create poll jobs queue table."""
    op.create_table(
        "poll_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["telegram_channels.id"],
            name="fk_poll_jobs_channel_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("correlation_id", name="uq_poll_jobs_correlation_id"),
    )
    op.create_index(
        "ix_poll_jobs_channel_id",
        "poll_jobs",
        ["channel_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop poll jobs queue table."""
    op.drop_index("ix_poll_jobs_channel_id", table_name="poll_jobs")
    op.drop_table("poll_jobs")
