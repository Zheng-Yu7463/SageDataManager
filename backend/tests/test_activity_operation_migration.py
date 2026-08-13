from importlib import util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic/versions/20260814_0014_add_activity_operations.py"
)


def load_migration():
    spec = util.spec_from_file_location("activity_operation_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_pre_migration_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "activities",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
    )
    metadata.create_all(connection)


def test_activity_operation_migration_backfills_and_enforces_role() -> None:
    engine = sa.create_engine("sqlite://")
    migration = load_migration()
    with engine.begin() as connection:
        create_pre_migration_schema(connection)
        connection.execute(
            sa.text(
                "INSERT INTO activities (id, action, description) "
                "VALUES ('legacy', 'created', 'legacy activity')"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        row = connection.execute(
            sa.text(
                "SELECT operation_id, operation_role FROM activities WHERE id = 'legacy'"
            )
        ).one()
        assert row.operation_id is None
        assert row.operation_role == "single"
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO activities "
                    "(id, action, description, operation_role) "
                    "VALUES ('invalid', 'created', 'invalid', NULL)"
                )
            )


def test_activity_operation_migration_downgrade_removes_operation_columns() -> None:
    engine = sa.create_engine("sqlite://")
    migration = load_migration()
    with engine.begin() as connection:
        create_pre_migration_schema(connection)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        migration.downgrade()

        columns = {column["name"] for column in sa.inspect(connection).get_columns("activities")}
        assert "operation_id" not in columns
        assert "operation_role" not in columns
