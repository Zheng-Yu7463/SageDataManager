"""index archived asset pagination

Revision ID: 20260814_0017
Revises: 20260814_0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260814_0017"
down_revision: str | None = "20260814_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_assets_archived_at_id", "assets", ["archived_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_assets_archived_at_id", table_name="assets")
