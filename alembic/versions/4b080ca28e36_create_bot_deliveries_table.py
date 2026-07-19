from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '4b080ca28e36'
down_revision = 'a3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create bot_deliveries table."""
    op.create_table(
        "bot_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("telegram_message_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["cluster_id"],
            ["dedupe_clusters.id"],
            name="fk_bot_deliveries_cluster_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("cluster_id", name="uq_bot_deliveries_cluster_id"),
    )
    op.create_index(
        "ix_bot_deliveries_cluster_id",
        "bot_deliveries",
        ["cluster_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop bot_deliveries table."""
    op.drop_index("ix_bot_deliveries_cluster_id", table_name="bot_deliveries")
    op.drop_table("bot_deliveries")

