from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.activity import ActivityAction
from app.domain.models import Activity, User
from app.main import app
from app.services.activities import record_activity
from app.services.security import create_session_token


def make_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_activity_log_requires_admin_and_paginates(monkeypatch) -> None:
    session = make_session()
    actor = User(username="zhengyu", name="郑宇", email="zhengyu@sage.lab", role="admin")
    session.add(actor)
    session.flush()
    record_activity(session, actor=actor, action="created", description="登记了资产 A")
    record_activity(session, actor=actor, action="archived", description="归档了资产 B")
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        assert client.get("/api/dashboard/activities").status_code == 401
        response = client.get(
            "/api/dashboard/activities?action=archived&page=1&page_size=30",
            headers={"X-Sage-Session": create_session_token("zhengyu")},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["action"] == "archived"
        assert payload["items"][0]["action_label"] == "归档资产"
        assert payload["items"][0]["actor_name"] == "郑宇"
        assert payload["facets"] == [
            {"value": "archived", "label": "归档资产", "count": 1},
            {"value": "created", "label": "登记资产", "count": 1},
        ]
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_activity_facets_include_actual_actions_and_unknown_history(monkeypatch) -> None:
    session = make_session()
    actor = User(username="zhengyu", name="郑宇", email="zhengyu@sage.lab", role="admin")
    session.add(actor)
    session.flush()
    for action in ActivityAction:
        record_activity(session, actor=actor, action=action, description=action.value)
    record_activity(session, actor=actor, action="legacy_event", description="旧事件")
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).get(
            "/api/dashboard/activities?page_size=100",
            headers={"X-Sage-Session": create_session_token("zhengyu")},
        )

        assert response.status_code == 200
        payload = response.json()
        facets = {facet["value"]: facet for facet in payload["facets"]}
        assert set(facets) == {action.value for action in ActivityAction} | {"legacy_event"}
        assert facets["downloaded_file"]["label"] == "下载文件"
        assert facets["legacy_event"]["label"] == "其他操作（legacy event）"
        assert all(item["action_label"] for item in payload["items"])
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_activity_log_exposes_one_primary_projection_per_relation_operation(
    monkeypatch,
) -> None:
    session = make_session()
    actor = User(username="zhengyu", name="郑宇", email="zhengyu@sage.lab", role="admin")
    session.add(actor)
    session.flush()
    operation_id = uuid4()
    for role in ("source", "target"):
        record_activity(
            session,
            actor=actor,
            action="linked_asset",
            description=f"{role} projection",
            operation_id=operation_id,
            operation_role=role,
        )
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).get(
            "/api/dashboard/activities?page_size=100",
            headers={"X-Sage-Session": create_session_token("zhengyu")},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert [item["description"] for item in payload["items"]] == [
            "source projection"
        ]
        assert payload["facets"] == [
            {"value": "linked_asset", "label": "建立关联", "count": 1}
        ]
        assert session.scalar(select(func.count()).select_from(Activity)) == 2
    finally:
        app.dependency_overrides.clear()
        session.close()
