"""Add key rotation metadata table and row version markers."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d4b1e2c7a9f5"
down_revision = "a1f6e7c9d2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add rotation metadata state and row-level key version tracking."""
    with op.batch_alter_table("telegram_accounts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "key_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
        )

    op.create_table(
        "auth_key_rotation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_key_version", sa.Integer(), nullable=False),
        sa.Column(
            "last_rotated_account_id",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "started_at",
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove rotation metadata and row-level key version markers."""
    op.drop_table("auth_key_rotation")
    with op.batch_alter_table("telegram_accounts") as batch_op:
        batch_op.drop_column("key_version")
