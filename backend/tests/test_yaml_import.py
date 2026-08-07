from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.models import Asset, User
from app.main import app
from app.services.security import create_session_token


def make_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_yaml_import_requires_admin_and_is_atomic(monkeypatch) -> None:
    session = make_session()
    session.add(User(username="zhengyu", name="郑宇", email="zhengyu@sage.lab", role="admin"))
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    app.dependency_overrides[get_session] = lambda: session
    yaml_content = """
assets:
  - type: dataset
    slug: yaml-soil-samples
    title: YAML 土壤样本
    status: draft
    visibility: lab
    tags: [生态, YAML]
    details:
      format: CSV
  - type: model
    slug: yaml-soil-model
    title: YAML 土壤模型
    status: active
    visibility: project
    details: {}
"""
    invalid_content = """
assets:
  - type: dataset
    slug: yaml-valid
    title: 有效资产
  - type: unknown
    slug: yaml-invalid
    title: 无效资产
"""
    try:
        client = TestClient(app)
        unauthenticated = client.post(
            "/api/assets/import/yaml", json={"content": yaml_content}
        )
        assert unauthenticated.status_code == 401

        headers = {"X-Sage-Session": create_session_token("zhengyu")}
        response = client.post(
            "/api/assets/import/yaml", json={"content": yaml_content}, headers=headers
        )
        assert response.status_code == 201
        assert [item["slug"] for item in response.json()["created"]] == [
            "yaml-soil-samples",
            "yaml-soil-model",
        ]

        invalid = client.post(
            "/api/assets/import/yaml", json={"content": invalid_content}, headers=headers
        )
        assert invalid.status_code == 422
        assert [asset.slug for asset in session.scalars(select(Asset).order_by(Asset.slug))] == [
            "yaml-soil-model",
            "yaml-soil-samples",
        ]
    finally:
        app.dependency_overrides.clear()
        session.close()
