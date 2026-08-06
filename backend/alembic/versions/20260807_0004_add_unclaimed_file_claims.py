"""Add unclaimed file claims.

Revision ID: 20260807_0004
Revises: 20260807_0003
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260807_0004"
down_revision: str | None = "20260807_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "unclaimed_files",
        sa.Column("claimed_asset_id", sa.UUID(), sa.ForeignKey("assets.id", ondelete="SET NULL")),
    )
    op.add_column("unclaimed_files", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_unclaimed_files_claimed_asset_id", "unclaimed_files", ["claimed_asset_id"])


def downgrade() -> None:
    op.drop_index("ix_unclaimed_files_claimed_asset_id", table_name="unclaimed_files")
    op.drop_column("unclaimed_files", "claimed_at")
    op.drop_column("unclaimed_files", "claimed_asset_id")
