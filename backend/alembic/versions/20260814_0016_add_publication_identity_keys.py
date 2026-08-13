"""add publication identity keys and activity query indexes

Revision ID: 20260814_0016
Revises: 20260814_0015
"""

from collections.abc import Sequence
from hashlib import sha256
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260814_0016"
down_revision: str | None = "20260814_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize_identity_text(value: object) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character.casefold() for character in decomposed if character.isalnum())


def _normalize_doi(value: object) -> str:
    import re

    normalized = str(value or "").strip().casefold()
    return re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", normalized)


def _identity_values(title: str, details: dict) -> tuple[tuple[str, str], ...]:
    authors = details.get("authors") or []
    first_author = str(authors[0]) if authors else ""
    title_author = (
        _normalize_identity_text(title),
        _normalize_identity_text(first_author),
    )
    values = (
        ("doi", _normalize_doi(details.get("doi"))),
        ("source_id", str(details.get("source_id", "")).strip().casefold()),
        ("title_author", "\0".join(title_author) if all(title_author) else ""),
    )
    return tuple((kind, value) for kind, value in values if value)


def upgrade() -> None:
    identity_table = op.create_table(
        "publication_identity_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "kind", "digest", name="uq_publication_identity_keys_identity"
        ),
    )
    op.create_index(
        "ix_publication_identity_keys_asset_id",
        "publication_identity_keys",
        ["asset_id"],
    )

    connection = op.get_bind()
    assets = sa.table(
        "assets",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("type", sa.String()),
        sa.column("title", sa.String()),
        sa.column("details", sa.JSON()),
    )
    seen: dict[tuple[str, str], object] = {}
    rows = connection.execute(
        sa.select(assets.c.id, assets.c.title, assets.c.details).where(
            assets.c.type.in_(("PAPER", "LITERATURE"))
        )
    )
    records = []
    for row in rows:
        details = row.details or {}
        if not details.get("source_id"):
            continue
        for kind, value in _identity_values(row.title, details):
            digest = sha256(value.encode("utf-8")).hexdigest()
            identity = (kind, digest)
            existing_asset_id = seen.setdefault(identity, row.id)
            if existing_asset_id != row.id:
                raise RuntimeError(
                    "检测到重复出版物身份；请先合并重复记录再执行数据库升级。"
                )
            records.append(
                {
                    "id": uuid4(),
                    "asset_id": row.id,
                    "kind": kind,
                    "digest": digest,
                }
            )
    if records:
        connection.execute(identity_table.insert(), records)

    op.create_index(
        "ix_activities_primary_created_id",
        "activities",
        ["created_at", "id"],
        postgresql_where=sa.text("operation_role <> 'target'"),
        sqlite_where=sa.text("operation_role <> 'target'"),
    )
    op.create_index(
        "ix_activities_primary_action_created_id",
        "activities",
        ["action", "created_at", "id"],
        postgresql_where=sa.text("operation_role <> 'target'"),
        sqlite_where=sa.text("operation_role <> 'target'"),
    )


def downgrade() -> None:
    op.drop_index("ix_activities_primary_action_created_id", table_name="activities")
    op.drop_index("ix_activities_primary_created_id", table_name="activities")
    op.drop_index(
        "ix_publication_identity_keys_asset_id",
        table_name="publication_identity_keys",
    )
    op.drop_table("publication_identity_keys")
