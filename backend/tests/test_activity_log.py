from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.models import Activity, User
from app.main import app
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
    session.add_all(
        [
            Activity(actor=actor, action="created", description="登记了资产 A"),
            Activity(actor=actor, action="archived", description="归档了资产 B"),
        ]
    )
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
        assert payload["items"][0]["actor_name"] == "郑宇"
    finally:
        app.dependency_overrides.clear()
        session.close()
