"""add persistent upload tasks

Revision ID: 20260814_0018
Revises: 20260814_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260814_0018"
down_revision: str | None = "20260814_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "upload_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("access_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_subdirectory", sa.String(length=400), nullable=False),
        sa.Column("transfer_mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'completed')", name="ck_upload_tasks_status"
        ),
        sa.CheckConstraint(
            "transfer_mode IN ('scp', 'agent')", name="ck_upload_tasks_transfer_mode"
        ),
        sa.CheckConstraint(
            "(transfer_mode = 'agent' AND access_token_id IS NOT NULL) OR "
            "(transfer_mode = 'scp' AND access_token_id IS NULL)",
            name="ck_upload_tasks_credential",
        ),
        sa.ForeignKeyConstraint(
            ["access_token_id"], ["personal_access_tokens.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upload_tasks_access_token_id", "upload_tasks", ["access_token_id"])
    op.create_index("ix_upload_tasks_asset_id", "upload_tasks", ["asset_id"])
    op.create_index("ix_upload_tasks_expires_at", "upload_tasks", ["expires_at"])
    op.create_index("ix_upload_tasks_user_id", "upload_tasks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_upload_tasks_user_id", table_name="upload_tasks")
    op.drop_index("ix_upload_tasks_expires_at", table_name="upload_tasks")
    op.drop_index("ix_upload_tasks_asset_id", table_name="upload_tasks")
    op.drop_index("ix_upload_tasks_access_token_id", table_name="upload_tasks")
    op.drop_table("upload_tasks")
