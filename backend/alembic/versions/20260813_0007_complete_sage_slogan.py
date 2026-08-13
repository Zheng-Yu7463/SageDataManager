"""Complete the default SAGE slogan.

Revision ID: 20260813_0007
Revises: 20260812_0006
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0007"
down_revision: str | None = "20260812_0006"
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
            branding.c.slogan == "数据 · 知识 · 传承",
            branding.c.slogan_secondary == "Science · Archive · Growth",
        )
        .values(
            slogan="科学 · 归档 · 成长 · 演进",
            slogan_secondary="Science · Archive · Growth · Evolution",
        )
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
            branding.c.slogan == "科学 · 归档 · 成长 · 演进",
            branding.c.slogan_secondary == "Science · Archive · Growth · Evolution",
        )
        .values(
            slogan="数据 · 知识 · 传承",
            slogan_secondary="Science · Archive · Growth",
        )
    )
