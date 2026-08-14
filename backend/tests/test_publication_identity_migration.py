from importlib import util
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic/versions/20260814_0016_add_publication_identity_keys.py"
)


def load_migration():
    spec = util.spec_from_file_location("publication_identity_migration", MIGRATION_PATH)
    assert spec and spec.loader
    migration = util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_publication_query_casts_native_postgres_enum_before_comparison() -> None:
    migration = load_migration()
    assets = sa.table(
        "assets",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("type", postgresql.ENUM(name="asset_type")),
        sa.column("title", sa.String()),
        sa.column("details", sa.JSON()),
    )

    compiled = str(
        migration._publication_assets_statement(assets).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "CAST(assets.type AS VARCHAR)" in compiled
