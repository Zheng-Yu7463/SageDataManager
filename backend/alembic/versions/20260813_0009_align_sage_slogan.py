"""Align the default SAGE slogan with DataManager.

Revision ID: 20260813_0009
Revises: 20260813_0008
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0009"
down_revision: str | None = "20260813_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    branding = sa.table(
        "instance_branding",
        sa.column("slogan", sa.String()),
        sa.column("slogan_secondary", sa.String()),
    )
    op.execute(
        branding.update()
        .where(
            branding.c.slogan == "求真 · 典藏 · 生长 · 卓越",
            branding.c.slogan_secondary == "Science · Archive · Growth · Excellence",
        )
        .values(slogan="科学 · 数据 · 成长 · 卓越")
    )


def downgrade() -> None:
    branding = sa.table(
        "instance_branding",
        sa.column("slogan", sa.String()),
        sa.column("slogan_secondary", sa.String()),
    )
    op.execute(
        branding.update()
        .where(
            branding.c.slogan == "科学 · 数据 · 成长 · 卓越",
            branding.c.slogan_secondary == "Science · Archive · Growth · Excellence",
        )
        .values(slogan="求真 · 典藏 · 生长 · 卓越")
    )
