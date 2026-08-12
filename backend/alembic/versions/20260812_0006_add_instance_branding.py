"""Add instance branding.

Revision ID: 20260812_0006
Revises: 20260807_0005
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0006"
down_revision: str | None = "20260807_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instance_branding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.String(length=80), nullable=False),
        sa.Column("product_subtitle", sa.String(length=120), nullable=False),
        sa.Column("organization_name", sa.String(length=120), nullable=False),
        sa.Column("slogan", sa.String(length=160), nullable=False),
        sa.Column("slogan_secondary", sa.String(length=160), nullable=False),
        sa.Column("primary_color", sa.String(length=7), nullable=False),
        sa.Column("logo_data", sa.LargeBinary(), nullable=True),
        sa.Column("logo_mime_type", sa.String(length=40), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("instance_branding")
