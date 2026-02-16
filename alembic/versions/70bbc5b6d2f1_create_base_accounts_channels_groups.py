"""Create base accounts/channels/groups tables."""

import sqlalchemy as sa

from alembic import op

revision = "70bbc5b6d2f1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create C012 base account/channel/group/state tables."""
    op.create_table(
        "telegram_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("api_id", sa.Integer(), nullable=False),
        sa.Column("api_hash_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=True),
        sa.Column("session_encrypted", sa.LargeBinary(), nullable=True),
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

    op.create_table(
        "telegram_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("telegram_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
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
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["telegram_accounts.id"],
            name="fk_telegram_channels_account_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "telegram_channel_id",
            name="uq_telegram_channels_telegram_channel_id",
        ),
    )

    op.create_table(
        "channel_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("dedupe_horizon_minutes_override", sa.Integer(), nullable=True),
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

    op.create_table(
        "channel_group_members",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["channel_groups.id"],
            name="fk_channel_group_members_group_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["telegram_channels.id"],
            name="fk_channel_group_members_channel_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "group_id",
            "channel_id",
            name="pk_channel_group_members",
        ),
        sa.UniqueConstraint(
            "channel_id",
            name="uq_channel_group_members_channel_id",
        ),
    )

    op.create_table(
        "channel_state",
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("cursor_json", sa.Text(), nullable=True),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["telegram_channels.id"],
            name="fk_channel_state_channel_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", name="pk_channel_state"),
    )


def downgrade() -> None:
    """Drop C012 base account/channel/group/state tables."""
    op.drop_table("channel_state")
    op.drop_table("channel_group_members")
    op.drop_table("channel_groups")
    op.drop_table("telegram_channels")
    op.drop_table("telegram_accounts")
