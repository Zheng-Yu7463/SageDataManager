"""add logical activity operation projections

Revision ID: 20260814_0014
Revises: 20260814_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260814_0014"
down_revision: str | None = "20260814_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "activities",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "activities",
        sa.Column("operation_role", sa.String(length=20), nullable=True),
    )
    op.execute("UPDATE activities SET operation_role = 'single'")
    with op.batch_alter_table("activities") as batch_op:
        batch_op.alter_column(
            "operation_role",
            existing_type=sa.String(length=20),
            nullable=False,
        )
    op.create_index("ix_activities_operation_id", "activities", ["operation_id"])


def downgrade() -> None:
    op.drop_index("ix_activities_operation_id", table_name="activities")
    with op.batch_alter_table("activities") as batch_op:
        batch_op.drop_column("operation_role")
        batch_op.drop_column("operation_id")
