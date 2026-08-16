"""remove redundant catalogue indexes

Revision ID: 20260816_0023
Revises: 20260816_0022
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260816_0023"
down_revision: str | None = "20260816_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_assets_slug", table_name="assets")
    op.drop_index("ix_tags_name", table_name="tags")
    op.drop_index("ix_unclaimed_files_relative_path", table_name="unclaimed_files")


def downgrade() -> None:
    op.create_index("ix_assets_slug", "assets", ["slug"], unique=True)
    op.create_index("ix_tags_name", "tags", ["name"], unique=True)
    op.create_index(
        "ix_unclaimed_files_relative_path",
        "unclaimed_files",
        ["relative_path"],
    )
