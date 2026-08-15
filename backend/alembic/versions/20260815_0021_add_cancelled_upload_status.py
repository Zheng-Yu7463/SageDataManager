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
    op.drop_constraint("ck_upload_tasks_status", "upload_tasks", type_="check")
    op.create_check_constraint(
        "ck_upload_tasks_status",
        "upload_tasks",
        "status IN ('active', 'completed', 'cancelled')",
    )


def downgrade() -> None:
    op.execute("UPDATE upload_tasks SET status = 'active' WHERE status = 'cancelled'")
    op.drop_constraint("ck_upload_tasks_status", "upload_tasks", type_="check")
    op.create_check_constraint(
        "ck_upload_tasks_status",
        "upload_tasks",
        "status IN ('active', 'completed')",
    )
