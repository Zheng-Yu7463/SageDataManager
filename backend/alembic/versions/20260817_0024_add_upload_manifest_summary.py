"""add upload manifest summary

Revision ID: 20260817_0024
Revises: 20260816_0023
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0024"
down_revision: str | None = "20260816_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("upload_tasks") as batch_op:
        batch_op.add_column(
            sa.Column("expected_file_count", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("expected_total_size", sa.BigInteger(), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_upload_tasks_expected_manifest",
            "(expected_file_count IS NULL AND expected_total_size IS NULL) OR "
            "(expected_file_count IS NOT NULL AND expected_total_size IS NOT NULL AND "
            "expected_file_count > 0 AND expected_total_size >= expected_file_count)",
        )


def downgrade() -> None:
    with op.batch_alter_table("upload_tasks") as batch_op:
        batch_op.drop_constraint("ck_upload_tasks_expected_manifest", type_="check")
        batch_op.drop_column("expected_total_size")
        batch_op.drop_column("expected_file_count")
