"""add agent access tokens

Revision ID: 20260813_0012
Revises: 20260813_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260813_0012"
down_revision: str | None = "20260813_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personal_access_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_personal_access_tokens_user_id"),
        "personal_access_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_personal_access_tokens_public_id"),
        "personal_access_tokens",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_personal_access_tokens_created_at"),
        "personal_access_tokens",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_personal_access_tokens_expires_at"),
        "personal_access_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_personal_access_tokens_revoked_at"),
        "personal_access_tokens",
        ["revoked_at"],
        unique=False,
    )
    op.add_column("activities", sa.Column("credential_name", sa.String(length=100)))


def downgrade() -> None:
    op.drop_column("activities", "credential_name")
    op.drop_index(
        op.f("ix_personal_access_tokens_revoked_at"),
        table_name="personal_access_tokens",
    )
    op.drop_index(
        op.f("ix_personal_access_tokens_expires_at"),
        table_name="personal_access_tokens",
    )
    op.drop_index(
        op.f("ix_personal_access_tokens_created_at"),
        table_name="personal_access_tokens",
    )
    op.drop_index(
        op.f("ix_personal_access_tokens_public_id"),
        table_name="personal_access_tokens",
    )
    op.drop_index(
        op.f("ix_personal_access_tokens_user_id"),
        table_name="personal_access_tokens",
    )
    op.drop_table("personal_access_tokens")
