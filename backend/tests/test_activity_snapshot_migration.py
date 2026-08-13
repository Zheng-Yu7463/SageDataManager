from importlib import util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic/versions/20260814_0013_add_activity_snapshots.py"
)


def load_migration():
    spec = util.spec_from_file_location("activity_snapshot_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_pre_migration_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
    )
    sa.Table(
        "assets",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
    )
    sa.Table(
        "activities",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36)),
        sa.Column("asset_id", sa.String(36)),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
    )
    metadata.create_all(connection)


def test_activity_snapshot_migration_backfills_and_enforces_actor_name() -> None:
    engine = sa.create_engine("sqlite://")
    migration = load_migration()
    with engine.begin() as connection:
        create_pre_migration_schema(connection)
        connection.execute(
            sa.text("INSERT INTO users (id, name) VALUES ('user-1', 'Original User')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO assets (id, title, type) "
                "VALUES ('asset-1', 'Original Asset', 'PAPER')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO activities (id, actor_id, asset_id, action, description) VALUES "
                "('normal', 'user-1', 'asset-1', 'created', 'normal'), "
                "('system', NULL, NULL, 'updated_branding', 'system'), "
                "('deleted-actor', 'missing-user', 'asset-1', 'created', 'deleted actor'), "
                "('deleted-asset', 'user-1', 'missing-asset', 'created', 'deleted asset')"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        rows = connection.execute(
            sa.text(
                "SELECT id, actor_display_name, asset_title_snapshot, "
                "asset_type_snapshot FROM activities ORDER BY id"
            )
        ).mappings()
        snapshots = {row.id: row for row in rows}
        assert snapshots["normal"].actor_display_name == "Original User"
        assert snapshots["normal"].asset_title_snapshot == "Original Asset"
        assert snapshots["normal"].asset_type_snapshot == "paper"
        assert snapshots["system"].actor_display_name == "系统"
        assert snapshots["system"].asset_title_snapshot is None
        assert snapshots["deleted-actor"].actor_display_name == "系统"
        assert snapshots["deleted-asset"].asset_title_snapshot is None

        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO activities "
                    "(id, action, description) VALUES ('invalid', 'created', 'invalid')"
                )
            )


def test_activity_snapshot_migration_downgrade_removes_snapshot_columns() -> None:
    engine = sa.create_engine("sqlite://")
    migration = load_migration()
    with engine.begin() as connection:
        create_pre_migration_schema(connection)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.downgrade()

        columns = {column["name"] for column in sa.inspect(connection).get_columns("activities")}
        assert "actor_display_name" not in columns
        assert "asset_title_snapshot" not in columns
        assert "asset_type_snapshot" not in columns
