"""add auditable file access grants

Revision ID: 20260814_0015
Revises: 20260814_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260814_0015"
down_revision: str | None = "20260814_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_accessed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "mode IN ('download', 'preview')", name="ck_file_access_grants_mode"
        ),
        sa.ForeignKeyConstraint(["file_id"], ["asset_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_file_access_grants_file_id", "file_access_grants", ["file_id"])
    op.create_index("ix_file_access_grants_user_id", "file_access_grants", ["user_id"])
    op.create_index(
        "ix_file_access_grants_expires_at", "file_access_grants", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_file_access_grants_expires_at", table_name="file_access_grants")
    op.drop_index("ix_file_access_grants_user_id", table_name="file_access_grants")
    op.drop_index("ix_file_access_grants_file_id", table_name="file_access_grants")
    op.drop_table("file_access_grants")
