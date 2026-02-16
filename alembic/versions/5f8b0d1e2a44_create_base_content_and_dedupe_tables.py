"""Create base content and dedupe tables."""

import sqlalchemy as sa

from alembic import op

revision = "5f8b0d1e2a44"
down_revision = "70bbc5b6d2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create C013 content and dedupe tables."""
    op.create_table(
        "raw_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
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
            ["channel_id"],
            ["telegram_channels.id"],
            name="fk_raw_messages_channel_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "channel_id",
            "message_id",
            name="uq_raw_messages_channel_id_message_id",
        ),
    )

    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_message_id", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("canonical_url_hash", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "dedupe_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
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
            ["channel_id"],
            ["telegram_channels.id"],
            name="fk_items_channel_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["raw_message_id"],
            ["raw_messages.id"],
            name="fk_items_raw_message_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "channel_id",
            "message_id",
            name="uq_items_channel_id_message_id",
        ),
        sa.UniqueConstraint(
            "raw_message_id",
            name="uq_items_raw_message_id",
        ),
    )

    op.create_table(
        "dedupe_clusters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cluster_key", sa.String(length=36), nullable=False),
        sa.Column("representative_item_id", sa.Integer(), nullable=True),
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
            ["representative_item_id"],
            ["items.id"],
            name="fk_dedupe_clusters_representative_item_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("cluster_key", name="uq_dedupe_clusters_cluster_key"),
    )

    op.create_table(
        "dedupe_members",
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id"],
            ["dedupe_clusters.id"],
            name="fk_dedupe_members_cluster_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.id"],
            name="fk_dedupe_members_item_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "cluster_id",
            "item_id",
            name="pk_dedupe_members",
        ),
    )

    op.create_table(
        "dedupe_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=True),
        sa.Column("candidate_item_id", sa.Integer(), nullable=True),
        sa.Column("strategy_name", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.id"],
            name="fk_dedupe_decisions_item_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id"],
            ["dedupe_clusters.id"],
            name="fk_dedupe_decisions_cluster_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_item_id"],
            ["items.id"],
            name="fk_dedupe_decisions_candidate_item_id",
            ondelete="SET NULL",
        ),
    )


def downgrade() -> None:
    """Drop C013 content and dedupe tables."""
    op.drop_table("dedupe_decisions")
    op.drop_table("dedupe_members")
    op.drop_table("dedupe_clusters")
    op.drop_table("items")
    op.drop_table("raw_messages")
