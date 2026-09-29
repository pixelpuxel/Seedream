"""Use a username as the primary login identifier.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "username" not in columns:
        op.add_column("users", sa.Column("username", sa.String(length=80), nullable=True))
        op.execute("UPDATE users SET username = lower(split_part(email, '@', 1)) WHERE username IS NULL")
        op.alter_column("users", "username", nullable=False)
        op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=True)


def downgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=False)
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
