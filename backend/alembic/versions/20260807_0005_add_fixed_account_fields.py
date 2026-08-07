"""Add fixed account fields.

Revision ID: 20260807_0005
Revises: 20260807_0004
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260807_0005"
down_revision: str | None = "20260807_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=80), nullable=True))
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=30), server_default="admin", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
