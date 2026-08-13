"""Enforce one asset owner for each archive path.

Revision ID: 20260813_0010
Revises: 20260813_0009
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260813_0010"
down_revision: str | None = "20260813_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "uq_asset_files_relative_path"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
        DO $$
        DECLARE
            duplicate_count BIGINT;
            duplicate_paths TEXT;
        BEGIN
            SELECT count(*) INTO duplicate_count
            FROM (
                SELECT relative_path
                FROM asset_files
                GROUP BY relative_path
                HAVING count(*) > 1
            ) AS conflicts;

            IF duplicate_count > 0 THEN
                SELECT string_agg(relative_path, ', ' ORDER BY relative_path)
                INTO duplicate_paths
                FROM (
                    SELECT relative_path
                    FROM asset_files
                    GROUP BY relative_path
                    HAVING count(*) > 1
                    ORDER BY relative_path
                    LIMIT 20
                ) AS examples;

                RAISE EXCEPTION
                    'Cannot enforce unique archive paths: % duplicate paths. Examples: %',
                    duplicate_count,
                    duplicate_paths;
            END IF;
        END
        $$
        """
        )
    with op.batch_alter_table("asset_files") as batch_op:
        batch_op.create_unique_constraint(CONSTRAINT_NAME, ["relative_path"])


def downgrade() -> None:
    with op.batch_alter_table("asset_files") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
