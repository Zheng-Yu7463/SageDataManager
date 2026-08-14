"""add first-run administrator onboarding

Revision ID: 20260814_0019
Revises: 20260814_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0019"
down_revision: str | None = "20260814_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "is_instance_owner",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE users SET is_instance_owner = true "
        "WHERE id = (SELECT id FROM users WHERE role = 'admin' AND username IS NOT NULL "
        "ORDER BY username, id LIMIT 1)"
    )
    op.create_index(
        "uq_users_single_instance_owner",
        "users",
        ["is_instance_owner"],
        unique=True,
        postgresql_where=sa.text("is_instance_owner"),
        sqlite_where=sa.text("is_instance_owner = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_single_instance_owner", table_name="users")
    op.drop_column("users", "is_instance_owner")
    op.drop_column("users", "password_hash")
