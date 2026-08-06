"""Create the research asset catalogue.

Revision ID: 20260806_0001
Revises:
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260806_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

asset_type = sa.Enum("PAPER", "DATASET", "LITERATURE", "PROJECT", "MODEL", name="asset_type")
asset_visibility = sa.Enum("LAB", "PROJECT", "RESTRICTED", name="asset_visibility")
file_health_status = sa.Enum("HEALTHY", "MISSING", "UNVERIFIED", name="file_health_status")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("avatar_url", sa.String(length=500)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False, unique=True),
    )
    op.create_index("ix_tags_name", "tags", ["name"], unique=True)
    op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("type", asset_type, nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False, unique=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("visibility", asset_visibility, nullable=False),
        sa.Column(
            "owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )
    for name in ("type", "slug", "title", "status", "owner_id", "updated_at"):
        op.create_index(f"ix_assets_{name}", "assets", [name], unique=name == "slug")
    op.create_table(
        "asset_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("release_notes", sa.Text(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_asset_versions_asset_id", "asset_versions", ["asset_id"])
    op.create_index("ix_asset_versions_is_current", "asset_versions", ["is_current"])
    op.create_table(
        "asset_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("asset_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("relative_path", sa.String(length=1000), nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("file_kind", sa.String(length=80), nullable=False),
        sa.Column("mime_type", sa.String(length=160)),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=128)),
        sa.Column("health_status", file_health_status, nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_asset_files_asset_id", "asset_files", ["asset_id"])
    op.create_index("ix_asset_files_file_name", "asset_files", ["file_name"])
    op.create_table(
        "asset_tags",
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "asset_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(length=60), nullable=False),
    )
    op.create_index("ix_asset_relations_source_asset_id", "asset_relations", ["source_asset_id"])
    op.create_index("ix_asset_relations_target_asset_id", "asset_relations", ["target_asset_id"])
    op.create_index("ix_asset_relations_relation_type", "asset_relations", ["relation_type"])
    op.create_table(
        "activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_activities_created_at", "activities", ["created_at"])


def downgrade() -> None:
    for table in (
        "activities",
        "asset_relations",
        "asset_tags",
        "asset_files",
        "asset_versions",
        "assets",
        "tags",
        "users",
    ):
        op.drop_table(table)
    file_health_status.drop(op.get_bind(), checkfirst=True)
    asset_visibility.drop(op.get_bind(), checkfirst=True)
    asset_type.drop(op.get_bind(), checkfirst=True)
