"""allow upload task cancellation

Revision ID: 20260815_0021
Revises: 20260814_0020
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260815_0021"
down_revision: str | None = "20260814_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("upload_tasks") as batch_op:
        batch_op.drop_constraint("ck_upload_tasks_status", type_="check")
        batch_op.create_check_constraint(
            "ck_upload_tasks_status",
            "status IN ('active', 'completed', 'cancelled')",
        )


def downgrade() -> None:
    op.execute("UPDATE upload_tasks SET status = 'active' WHERE status = 'cancelled'")
    with op.batch_alter_table("upload_tasks") as batch_op:
        batch_op.drop_constraint("ck_upload_tasks_status", type_="check")
        batch_op.create_check_constraint(
            "ck_upload_tasks_status",
            "status IN ('active', 'completed')",
        )
