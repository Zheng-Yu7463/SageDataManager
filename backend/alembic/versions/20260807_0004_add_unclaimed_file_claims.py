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
    with op.batch_alter_table("unclaimed_files") as batch_op:
        batch_op.add_column(
            sa.Column(
                "claimed_asset_id",
                sa.UUID(),
                sa.ForeignKey(
                    "assets.id",
                    name="fk_unclaimed_files_claimed_asset_id_assets",
                    ondelete="SET NULL",
                ),
            )
        )
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_unclaimed_files_claimed_asset_id", "unclaimed_files", ["claimed_asset_id"])


def downgrade() -> None:
    op.drop_index("ix_unclaimed_files_claimed_asset_id", table_name="unclaimed_files")
    with op.batch_alter_table("unclaimed_files") as batch_op:
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("claimed_asset_id")
