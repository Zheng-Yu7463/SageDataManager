"""add revocable browser session generation

Revision ID: 20260816_0022
Revises: 20260815_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_0022"
down_revision: str | None = "20260815_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "session_generation", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "session_generation")
