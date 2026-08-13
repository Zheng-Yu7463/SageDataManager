"""Enforce unique directed asset relations.

Revision ID: 20260813_0011
Revises: 20260813_0010
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260813_0011"
down_revision: str | None = "20260813_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "uq_asset_relations_identity"


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            duplicate_count BIGINT;
        BEGIN
            SELECT count(*) INTO duplicate_count
            FROM (
                SELECT source_asset_id, target_asset_id, relation_type
                FROM asset_relations
                GROUP BY source_asset_id, target_asset_id, relation_type
                HAVING count(*) > 1
            ) AS conflicts;

            IF duplicate_count > 0 THEN
                RAISE EXCEPTION
                    'Cannot enforce unique asset relations: % duplicate identities.',
                    duplicate_count;
            END IF;
        END
        $$
        """
    )
    op.create_unique_constraint(
        CONSTRAINT_NAME,
        "asset_relations",
        ["source_asset_id", "target_asset_id", "relation_type"],
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "asset_relations", type_="unique")
