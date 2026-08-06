"""Add unclaimed scanned files.

Revision ID: 20260807_0003
Revises: 20260806_0002
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260807_0003"
down_revision: str | None = "20260806_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "unclaimed_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("relative_path", sa.String(length=1000), nullable=False, unique=True),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("file_kind", sa.String(length=80), nullable=False),
        sa.Column("mime_type", sa.String(length=160)),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_unclaimed_files_relative_path", "unclaimed_files", ["relative_path"])


def downgrade() -> None:
    op.drop_table("unclaimed_files")
