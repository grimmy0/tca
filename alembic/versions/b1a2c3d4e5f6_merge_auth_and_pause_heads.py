"""Merge auth-session and account-pause heads."""

from alembic import op

revision = "b1a2c3d4e5f6"
down_revision = ("c4a3d2b1f6e8", "f2c7a8b1d3e4")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Merge heads without schema changes."""
    _ = op.get_bind()


def downgrade() -> None:
    """No-op downgrade for merge revision."""
    _ = op.get_bind()
