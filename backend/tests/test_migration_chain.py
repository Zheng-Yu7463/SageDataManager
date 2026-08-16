import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

BACKEND_ROOT = Path(__file__).parents[1]


def run_alembic_process(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["SAGE_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def run_alembic(database_url: str, *arguments: str) -> None:
    result = run_alembic_process(database_url, *arguments)
    assert result.returncode == 0, result.stderr


def test_empty_sqlite_database_upgrades_to_head(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-chain.db"
    database_url = f"sqlite:///{database_path}"

    run_alembic(database_url, "upgrade", "head")

    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        inspector = sa.inspect(connection)
        activity_columns = {column["name"] for column in inspector.get_columns("activities")}
        unclaimed_foreign_keys = inspector.get_foreign_keys("unclaimed_files")
        file_unique_constraints = inspector.get_unique_constraints("asset_files")
        relation_unique_constraints = inspector.get_unique_constraints("asset_relations")
        file_access_checks = inspector.get_check_constraints("file_access_grants")
        file_access_columns = {
            column["name"] for column in inspector.get_columns("file_access_grants")
        }
        publication_identity_constraints = inspector.get_unique_constraints(
            "publication_identity_keys"
        )
        activity_indexes = {index["name"] for index in inspector.get_indexes("activities")}
        asset_indexes = {index["name"] for index in inspector.get_indexes("assets")}
        tag_indexes = {index["name"] for index in inspector.get_indexes("tags")}
        unclaimed_indexes = {
            index["name"] for index in inspector.get_indexes("unclaimed_files")
        }
        asset_unique_constraints = inspector.get_unique_constraints("assets")
        tag_unique_constraints = inspector.get_unique_constraints("tags")
        unclaimed_unique_constraints = inspector.get_unique_constraints("unclaimed_files")
        upload_task_checks = inspector.get_check_constraints("upload_tasks")
        upload_task_foreign_keys = inspector.get_foreign_keys("upload_tasks")
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        user_indexes = {index["name"]: index for index in inspector.get_indexes("users")}
        invitation_columns = {
            column["name"] for column in inspector.get_columns("account_invitations")
        }
        invitation_checks = inspector.get_check_constraints("account_invitations")
        invitation_foreign_keys = inspector.get_foreign_keys("account_invitations")

    assert revision == "20260816_0023"
    assert {"operation_id", "operation_role"} <= activity_columns
    assert any(
        key["constrained_columns"] == ["claimed_asset_id"] and key["referred_table"] == "assets"
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
    assert any(
        constraint["name"] == "ck_file_access_grants_mode" for constraint in file_access_checks
    )
    assert "first_accessed_at" in file_access_columns
    assert any(
        constraint["name"] == "uq_publication_identity_keys_identity"
        for constraint in publication_identity_constraints
    )
    assert {
        "ix_activities_primary_created_id",
        "ix_activities_primary_action_created_id",
    } <= activity_indexes
    assert "ix_assets_archived_at_id" in asset_indexes
    assert "ix_assets_slug" not in asset_indexes
    assert "ix_tags_name" not in tag_indexes
    assert "ix_unclaimed_files_relative_path" not in unclaimed_indexes
    assert any(
        constraint["column_names"] == ["slug"]
        for constraint in asset_unique_constraints
    )
    assert any(
        constraint["column_names"] == ["name"]
        for constraint in tag_unique_constraints
    )
    assert any(
        constraint["column_names"] == ["relative_path"]
        for constraint in unclaimed_unique_constraints
    )
    assert any(constraint["name"] == "ck_upload_tasks_status" for constraint in upload_task_checks)
    assert {key["referred_table"] for key in upload_task_foreign_keys} == {
        "assets",
        "personal_access_tokens",
        "users",
    }
    assert {
        "password_hash",
        "is_registered",
        "is_instance_owner",
        "session_generation",
    } <= user_columns
    assert user_indexes["uq_users_single_instance_owner"]["unique"] == 1
    assert {
        "user_id",
        "created_by_id",
        "token_hash",
        "purpose",
        "expires_at",
        "accepted_at",
        "revoked_at",
    } <= invitation_columns
    assert any(
        constraint["name"] == "ck_account_invitations_purpose" for constraint in invitation_checks
    )
    assert {key["referred_table"] for key in invitation_foreign_keys} == {"users"}


def test_publication_identity_migration_backfills_existing_records(tmp_path: Path) -> None:
    database_path = tmp_path / "publication-identities.db"
    database_url = f"sqlite:///{database_path}"
    run_alembic(database_url, "upgrade", "20260814_0015")

    engine = sa.create_engine(database_url)
    owner_id = uuid4()
    metadata_owner_id = uuid4()
    asset_id = uuid4()
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users "
                "(id, name, email, username, role, is_active) "
                "VALUES (:id, 'Admin', 'admin@example.org', 'admin', 'admin', 1)"
            ),
            {"id": owner_id.hex},
        )
        connection.execute(
            sa.text(
                "INSERT INTO users "
                "(id, name, email, username, role, is_active) "
                "VALUES (:id, 'Metadata Owner', 'owner@example.org', NULL, 'admin', 1)"
            ),
            {"id": metadata_owner_id.hex},
        )
        connection.execute(
            sa.text(
                "INSERT INTO assets "
                "(id, type, slug, title, summary, status, visibility, owner_id, "
                "details, created_at, updated_at) VALUES "
                "(:id, 'LITERATURE', 'existing-paper', 'Vision-Language Models', "
                "'', 'published', 'LAB', :owner_id, :details, :now, :now)"
            ),
            {
                "id": asset_id.hex,
                "owner_id": owner_id.hex,
                "details": (
                    '{"authors":["Abele Mălan"],"source_id":"Official.2026.1",'
                    '"doi":"https://doi.org/10.1000/Canonical"}'
                ),
                "now": now.isoformat(),
            },
        )

    run_alembic(database_url, "upgrade", "head")

    with engine.connect() as connection:
        identities = connection.execute(
            sa.text(
                "SELECT kind, digest FROM publication_identity_keys "
                "WHERE asset_id = :asset_id ORDER BY kind"
            ),
            {"asset_id": asset_id.hex},
        ).all()
        instance_owner_id = connection.scalar(
            sa.text("SELECT id FROM users WHERE is_instance_owner = 1")
        )

    assert [kind for kind, _ in identities] == ["doi", "source_id", "title_author"]
    assert all(len(digest) == 64 for _, digest in identities)
    assert instance_owner_id == owner_id.hex


def test_publication_identity_migration_can_retry_after_duplicate_preflight(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "duplicate-publications.db"
    database_url = f"sqlite:///{database_path}"
    run_alembic(database_url, "upgrade", "20260814_0015")

    engine = sa.create_engine(database_url)
    owner_id = uuid4()
    duplicate_ids = (uuid4(), uuid4())
    now = datetime.now(UTC).isoformat()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users "
                "(id, name, email, username, role, is_active) "
                "VALUES (:id, 'Admin', 'admin@example.org', 'admin', 'admin', 1)"
            ),
            {"id": owner_id.hex},
        )
        for index, asset_id in enumerate(duplicate_ids):
            connection.execute(
                sa.text(
                    "INSERT INTO assets "
                    "(id, type, slug, title, summary, status, visibility, owner_id, "
                    "details, created_at, updated_at) VALUES "
                    "(:id, 'LITERATURE', :slug, 'Duplicate Publication', '', "
                    "'published', 'LAB', :owner_id, :details, :now, :now)"
                ),
                {
                    "id": asset_id.hex,
                    "slug": f"duplicate-publication-{index}",
                    "owner_id": owner_id.hex,
                    "details": '{"authors":["Same Author"],"source_id":"same-source"}',
                    "now": now,
                },
            )

    failed = run_alembic_process(database_url, "upgrade", "head")
    assert failed.returncode != 0
    assert "检测到重复出版物身份" in failed.stderr
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260814_0015"
        )
        assert not sa.inspect(connection).has_table("publication_identity_keys")

    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM assets WHERE id = :id"),
            {"id": duplicate_ids[1].hex},
        )
    run_alembic(database_url, "upgrade", "head")

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260816_0023"
        )
        assert sa.inspect(connection).has_table("publication_identity_keys")
