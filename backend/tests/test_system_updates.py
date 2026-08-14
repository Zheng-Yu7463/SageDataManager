from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import require_admin
from app.api.routes import settings as settings_routes
from app.domain.models import User
from app.main import app
from app.services.security import hash_password
from app.services.system_updates import UpdateAgentUnavailableError


def update_status(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "enabled": True,
        "state": "available",
        "phase": None,
        "message": "发现 2 个可用提交。",
        "branch": "main",
        "current_commit": "a" * 40,
        "latest_commit": "b" * 40,
        "update_available": True,
        "behind_count": 2,
        "ahead_count": 0,
        "worktree_clean": True,
        "remote_url": "https://github.com/Zheng-Yu7463/SageDataManager.git",
        "commits": [],
        "logs": [],
    }
    value.update(overrides)
    return value


def account(*, owner: bool) -> User:
    return User(
        username="owner" if owner else "admin",
        name="Owner" if owner else "Admin",
        email="owner@example.org" if owner else "admin@example.org",
        role="admin",
        password_hash=hash_password("secure-password"),
        is_active=True,
        is_registered=True,
        is_instance_owner=owner,
    )


def test_update_status_is_visible_to_an_administrator(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        settings_routes,
        "request_update_agent",
        lambda method, path: calls.append((method, path)) or update_status(),
    )
    app.dependency_overrides[require_admin] = lambda: account(owner=False)
    try:
        response = TestClient(app).get("/api/settings/system-update")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["update_available"] is True
    assert calls == [("GET", "/v1/status")]


def test_unconfigured_update_agent_is_reported_without_exposing_an_error(monkeypatch) -> None:
    def unavailable(method: str, path: str) -> dict[str, object]:
        raise UpdateAgentUnavailableError("宿主机更新服务尚未配置。")

    monkeypatch.setattr(settings_routes, "request_update_agent", unavailable)
    app.dependency_overrides[require_admin] = lambda: account(owner=False)
    try:
        response = TestClient(app).get("/api/settings/system-update")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["state"] == "unavailable"


def test_only_instance_owner_can_apply_an_update(monkeypatch) -> None:
    called = False

    def request_agent(method: str, path: str) -> dict[str, object]:
        nonlocal called
        called = True
        return update_status(state="backing_up")

    monkeypatch.setattr(settings_routes, "request_update_agent", request_agent)
    app.dependency_overrides[require_admin] = lambda: account(owner=False)
    try:
        response = TestClient(app).post(
            "/api/settings/system-update/apply",
            json={"password": "secure-password"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert called is False


def test_owner_password_is_rechecked_before_update(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        settings_routes,
        "request_update_agent",
        lambda method, path: calls.append((method, path))
        or update_status(state="backing_up", phase="database_backup"),
    )
    app.dependency_overrides[require_admin] = lambda: account(owner=True)
    client = TestClient(app)
    try:
        denied = client.post(
            "/api/settings/system-update/apply",
            json={"password": "wrong-password"},
        )
        accepted = client.post(
            "/api/settings/system-update/apply",
            json={"password": "secure-password"},
        )
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 403
    assert accepted.status_code == 202
    assert accepted.json()["state"] == "backing_up"
    assert calls == [("POST", "/v1/update")]


def test_mutating_endpoint_returns_service_unavailable_when_agent_is_offline(monkeypatch) -> None:
    def unavailable(method: str, path: str) -> dict[str, object]:
        raise UpdateAgentUnavailableError("无法连接宿主机更新服务。")

    monkeypatch.setattr(settings_routes, "request_update_agent", unavailable)
    app.dependency_overrides[require_admin] = lambda: account(owner=True)
    try:
        response = TestClient(app).post(
            "/api/settings/system-update/apply",
            json={"password": "secure-password"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
