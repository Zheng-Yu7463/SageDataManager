import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

BACKEND_ROOT = Path(__file__).parents[1]


def run_alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["SAGE_DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_empty_sqlite_database_upgrades_to_head(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-chain.db"
    database_url = f"sqlite:///{database_path}"

    run_alembic(database_url, "upgrade", "head")

    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        inspector = sa.inspect(connection)
        activity_columns = {
            column["name"] for column in inspector.get_columns("activities")
        }
        unclaimed_foreign_keys = inspector.get_foreign_keys("unclaimed_files")
        file_unique_constraints = inspector.get_unique_constraints("asset_files")
        relation_unique_constraints = inspector.get_unique_constraints("asset_relations")

    assert revision == "20260814_0014"
    assert {"operation_id", "operation_role"} <= activity_columns
    assert any(
        key["constrained_columns"] == ["claimed_asset_id"]
        and key["referred_table"] == "assets"
        for key in unclaimed_foreign_keys
    )
    assert any(
        constraint["name"] == "uq_asset_files_relative_path"
        for constraint in file_unique_constraints
    )
    assert any(
        constraint["name"] == "uq_asset_relations_identity"
        for constraint in relation_unique_constraints
    )
