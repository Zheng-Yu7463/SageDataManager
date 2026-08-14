"""add one-time account invitations

Revision ID: 20260814_0020
Revises: 20260814_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260814_0020"
down_revision: str | None = "20260814_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("users", "name", existing_type=sa.String(length=80), nullable=True)
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=True)
    op.add_column(
        "users",
        sa.Column(
            "is_registered",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.create_table(
        "account_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('registration', 'recovery')",
            name="ck_account_invitations_purpose",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_invitations_created_by_id",
        "account_invitations",
        ["created_by_id"],
    )
    op.create_index(
        "ix_account_invitations_expires_at",
        "account_invitations",
        ["expires_at"],
    )
    op.create_index(
        "ix_account_invitations_token_hash",
        "account_invitations",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_account_invitations_user_id",
        "account_invitations",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_invitations_user_id", table_name="account_invitations")
    op.drop_index("ix_account_invitations_token_hash", table_name="account_invitations")
    op.drop_index("ix_account_invitations_expires_at", table_name="account_invitations")
    op.drop_index("ix_account_invitations_created_by_id", table_name="account_invitations")
    op.drop_table("account_invitations")
    op.execute("UPDATE users SET name = username WHERE name IS NULL")
    op.execute("UPDATE users SET email = username || '@pending.invalid' WHERE email IS NULL")
    op.drop_column("users", "is_registered")
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("users", "name", existing_type=sa.String(length=80), nullable=False)
