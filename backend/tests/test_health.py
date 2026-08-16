import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.api.routes import health as health_routes
from app.core.config import Settings
from app.main import app


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "sage-data-manager-api"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("upload_ssh_port", 0),
        ("upload_ssh_port", 65_536),
        ("auth_session_ttl_seconds", 0),
        ("account_invitation_ttl_seconds", -1),
        ("file_access_ttl_seconds", 0),
        ("upload_ticket_ttl_seconds", 0),
        ("agent_upload_max_bytes", 0),
        ("agent_upload_max_bytes", 500_000_001),
        ("agent_token_last_used_interval_seconds", -1),
        ("update_agent_timeout_seconds", 0),
    ],
)
def test_settings_reject_invalid_runtime_limits(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_settings_builds_database_url_from_individual_components() -> None:
    password = "p@ss:/?#%word"
    configured = Settings(
        _env_file=None,
        database_host="postgres",
        database_port=5432,
        database_name="sage/archive",
        database_user="sage+admin",
        database_password=password,
    )

    parsed = make_url(configured.database_url)

    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.host == "postgres"
    assert parsed.port == 5432
    assert parsed.database == "sage/archive"
    assert parsed.username == "sage+admin"
    assert parsed.password == password


def test_settings_rejects_partial_database_components() -> None:
    with pytest.raises(ValidationError, match="configured together"):
        Settings(_env_file=None, database_host="postgres")


class ScalarResult:
    def __init__(self, value: str) -> None:
        self.value = value

    def scalar_one_or_none(self) -> str:
        return self.value


class ReadySession:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    def execute(self, statement):
        return ScalarResult(self.revision)


class ScriptHead:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    def get_current_head(self) -> str:
        return self.revision


def test_readiness_checks_database_revision_and_release_commit(monkeypatch, tmp_path) -> None:
    revision = "20260814_0020"
    monkeypatch.setattr(
        health_routes.ScriptDirectory,
        "from_config",
        lambda config: ScriptHead(revision),
    )
    monkeypatch.setattr(health_routes.settings, "release_commit", "f" * 40)
    monkeypatch.setattr(health_routes.settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(health_routes.settings, "storage_root", tmp_path)

    response = health_routes.readiness(ReadySession(revision))

    assert response["status"] == "ready"
    assert response["release_commit"] == "f" * 40
    assert response["database_revision"] == revision


def test_readiness_rejects_a_database_at_the_wrong_revision(monkeypatch) -> None:
    monkeypatch.setattr(
        health_routes.ScriptDirectory,
        "from_config",
        lambda config: ScriptHead("expected"),
    )

    with pytest.raises(HTTPException) as captured:
        health_routes.readiness(ReadySession("old"))

    assert captured.value.status_code == 503


def test_readiness_reports_migration_configuration_errors(monkeypatch) -> None:
    def fail_to_load_migrations(config):
        raise health_routes.CommandError("Multiple heads are present")

    monkeypatch.setattr(health_routes.ScriptDirectory, "from_config", fail_to_load_migrations)

    with pytest.raises(HTTPException) as captured:
        health_routes.readiness(ReadySession("revision"))

    assert captured.value.status_code == 503
    assert captured.value.detail == "数据库或迁移状态不可用。"


def test_readiness_rejects_missing_authentication_secret(monkeypatch, tmp_path) -> None:
    revision = "expected"
    monkeypatch.setattr(
        health_routes.ScriptDirectory,
        "from_config",
        lambda config: ScriptHead(revision),
    )
    monkeypatch.setattr(health_routes.settings, "auth_session_secret", "")
    monkeypatch.setattr(health_routes.settings, "fixed_account_password", "")
    monkeypatch.setattr(health_routes.settings, "storage_root", tmp_path)

    with pytest.raises(HTTPException) as captured:
        health_routes.readiness(ReadySession(revision))

    assert captured.value.status_code == 503
    assert "认证签名密钥" in captured.value.detail


def test_readiness_rejects_an_unavailable_storage_root(monkeypatch, tmp_path) -> None:
    revision = "expected"
    missing_root = tmp_path / "missing"
    monkeypatch.setattr(
        health_routes.ScriptDirectory,
        "from_config",
        lambda config: ScriptHead(revision),
    )
    monkeypatch.setattr(health_routes.settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(health_routes.settings, "fixed_account_password", "")
    monkeypatch.setattr(health_routes.settings, "storage_root", missing_root)

    with pytest.raises(HTTPException) as captured:
        health_routes.readiness(ReadySession(revision))

    assert captured.value.status_code == 503
    assert "归档存储根" in captured.value.detail


@pytest.mark.parametrize(
    "storage_statistics",
    [
        SimpleNamespace(f_flag=os.ST_RDONLY, f_bavail=1),
        SimpleNamespace(f_flag=0, f_bavail=0),
    ],
)
def test_readiness_rejects_unwritable_storage(
    monkeypatch,
    tmp_path,
    storage_statistics,
) -> None:
    revision = "expected"
    monkeypatch.setattr(
        health_routes.ScriptDirectory,
        "from_config",
        lambda config: ScriptHead(revision),
    )
    monkeypatch.setattr(health_routes.settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(health_routes.settings, "fixed_account_password", "")
    monkeypatch.setattr(health_routes.settings, "storage_root", tmp_path)
    monkeypatch.setattr(health_routes.os, "statvfs", lambda path: storage_statistics)

    with pytest.raises(HTTPException) as captured:
        health_routes.readiness(ReadySession(revision))

    assert captured.value.status_code == 503
    assert "归档存储根" in captured.value.detail


def test_readiness_handles_storage_resolution_loops(monkeypatch) -> None:
    revision = "expected"

    class LoopingStorageRoot:
        def resolve(self, *, strict: bool) -> None:
            raise RuntimeError("Symlink loop")

    monkeypatch.setattr(
        health_routes.ScriptDirectory,
        "from_config",
        lambda config: ScriptHead(revision),
    )
    monkeypatch.setattr(health_routes.settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(health_routes.settings, "fixed_account_password", "")
    monkeypatch.setattr(health_routes.settings, "storage_root", LoopingStorageRoot())

    with pytest.raises(HTTPException) as captured:
        health_routes.readiness(ReadySession(revision))

    assert captured.value.status_code == 503
    assert "归档存储根" in captured.value.detail
