"""Add persistent storage scan runs.

Revision ID: 20260806_0002
Revises: 20260806_0001
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260806_0002"
down_revision: str | None = "20260806_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("files_discovered", sa.BigInteger(), nullable=False),
        sa.Column("files_indexed", sa.BigInteger(), nullable=False),
        sa.Column("files_missing", sa.BigInteger(), nullable=False),
        sa.Column("files_unclaimed", sa.BigInteger(), nullable=False),
        sa.Column("files_skipped", sa.BigInteger(), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_scan_runs_status", "scan_runs", ["status"])
    op.create_index("ix_scan_runs_started_at", "scan_runs", ["started_at"])


def downgrade() -> None:
    op.drop_table("scan_runs")
