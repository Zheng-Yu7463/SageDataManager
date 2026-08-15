import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.routes import health as health_routes
from app.main import app


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "sage-data-manager-api"}


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


def test_readiness_checks_database_revision_and_release_commit(monkeypatch) -> None:
    revision = "20260814_0020"
    monkeypatch.setattr(
        health_routes.ScriptDirectory,
        "from_config",
        lambda config: ScriptHead(revision),
    )
    monkeypatch.setattr(health_routes.settings, "release_commit", "f" * 40)

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
