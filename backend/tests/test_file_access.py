from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.enums import AssetType, HealthStatus, Visibility
from app.domain.models import Activity, Asset, FileRecord, User
from app.main import app
from app.services.file_access import (
    FilePreviewUnavailableError,
    FileUnavailableError,
    prepare_file_delivery,
    verify_file_access,
)
from app.services.security import (
    create_file_access_token,
    create_session_token,
    read_file_access_token,
)


def make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def create_file_record(
    session: Session, root: Path, *, mime_type: str = "text/plain"
) -> tuple[User, FileRecord]:
    relative_path = "dataset/soil-samples/notes.txt"
    destination = root / relative_path
    destination.parent.mkdir(parents=True)
    destination.write_text("controlled archive content", encoding="utf-8")
    owner = User(username="zhengyu", name="郑宇", email="zhengyu@sage.lab", role="admin")
    asset = Asset(
        type=AssetType.DATASET,
        slug="soil-samples",
        title="土壤样本",
        summary="测试资产",
        status="active",
        visibility=Visibility.LAB,
        owner=owner,
    )
    record = FileRecord(
        asset=asset,
        relative_path=relative_path,
        file_name="notes.txt",
        file_kind="document",
        mime_type=mime_type,
        file_size=26,
        health_status=HealthStatus.HEALTHY,
    )
    session.add(record)
    session.flush()
    return owner, record


def test_prepare_delivery_uses_internal_uri_and_audits_access(tmp_path: Path) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)

    delivery = prepare_file_delivery(session, tmp_path, record.id, "download", actor=actor)
    session.commit()

    assert delivery.internal_uri == "/_protected-files/dataset/soil-samples/notes.txt"
    assert delivery.content_disposition.startswith("attachment;")
    assert session.scalar(select(Activity.action)) == "downloaded_file"


def test_preview_rejects_unsupported_file_type(tmp_path: Path) -> None:
    session = make_session()
    _, record = create_file_record(session, tmp_path, mime_type="application/octet-stream")

    with pytest.raises(FilePreviewUnavailableError):
        verify_file_access(session, record.id, "preview")


def test_delivery_rejects_symlink_that_escapes_storage_root(tmp_path: Path) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    outside = tmp_path.parent / "outside-archive.txt"
    outside.write_text("must not be served", encoding="utf-8")
    target = tmp_path / record.relative_path
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(FileUnavailableError):
        prepare_file_delivery(session, tmp_path, record.id, "download", actor=actor)


def test_file_ticket_is_bound_to_file_mode_and_account(tmp_path: Path) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    token, _ = create_file_access_token(record.id, "preview", actor.username or "")

    claims = read_file_access_token(token)

    assert claims is not None
    assert claims.file_id == record.id
    assert claims.mode == "preview"
    assert claims.username == "zhengyu"


def test_file_content_endpoint_uses_ticket_and_accel_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    session.commit()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        session_token = create_session_token(actor.username or "")
        ticket_response = client.post(
            f"/api/files/{record.id}/tickets",
            json={"mode": "download"},
            headers={"X-Sage-Session": session_token},
        )

        assert ticket_response.status_code == 201
        content_response = client.get(ticket_response.json()["content_url"])

        assert content_response.status_code == 200
        assert content_response.headers["x-accel-redirect"].endswith("/notes.txt")
        assert content_response.headers["content-disposition"].startswith("attachment;")
        assert session.scalar(select(Activity.action)) == "downloaded_file"
    finally:
        app.dependency_overrides.clear()
        session.close()
