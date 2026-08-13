"""add immutable activity display snapshots

Revision ID: 20260814_0013
Revises: 20260813_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0013"
down_revision: str | None = "20260813_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("actor_display_name", sa.String(length=80)))
    op.add_column("activities", sa.Column("asset_title_snapshot", sa.String(length=500)))
    op.add_column("activities", sa.Column("asset_type_snapshot", sa.String(length=30)))
    activities = sa.table(
        "activities",
        sa.column("actor_id"),
        sa.column("asset_id"),
        sa.column("actor_display_name"),
        sa.column("asset_title_snapshot"),
        sa.column("asset_type_snapshot"),
    )
    users = sa.table("users", sa.column("id"), sa.column("name"))
    assets = sa.table(
        "assets", sa.column("id"), sa.column("title"), sa.column("type")
    )
    actor_name = (
        sa.select(users.c.name)
        .where(users.c.id == activities.c.actor_id)
        .scalar_subquery()
    )
    asset_title = (
        sa.select(assets.c.title)
        .where(assets.c.id == activities.c.asset_id)
        .scalar_subquery()
    )
    asset_type = (
        sa.select(sa.func.lower(sa.cast(assets.c.type, sa.String(length=30))))
        .where(assets.c.id == activities.c.asset_id)
        .scalar_subquery()
    )
    op.execute(
        activities.update().values(
            actor_display_name=sa.func.coalesce(actor_name, "系统"),
            asset_title_snapshot=asset_title,
            asset_type_snapshot=asset_type,
        )
    )
    with op.batch_alter_table("activities") as batch_op:
        batch_op.alter_column(
            "actor_display_name",
            existing_type=sa.String(length=80),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("activities") as batch_op:
        batch_op.drop_column("asset_type_snapshot")
        batch_op.drop_column("asset_title_snapshot")
        batch_op.drop_column("actor_display_name")
