"""Add APNs device registrations.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_registrations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("device_token", sa.String(length=200), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("bundle_id", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_token"),
    )
    op.create_index("ix_device_registrations_user_id", "device_registrations", ["user_id"])
    op.create_index("ix_device_registrations_device_token", "device_registrations", ["device_token"], unique=True)
    op.create_index("ix_device_registrations_user_active", "device_registrations", ["user_id", "is_active"])


def downgrade() -> None:
    op.drop_table("device_registrations")
