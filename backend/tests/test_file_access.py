import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import files as file_routes
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.enums import AssetType, HealthStatus, Visibility
from app.domain.models import Activity, Asset, FileAccessGrant, FileRecord, User
from app.main import app
from app.services.archive import scan_storage
from app.services.file_access import (
    FilePreviewUnavailableError,
    FileUnavailableError,
    open_file_delivery,
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
    session: Session,
    root: Path,
    *,
    mime_type: str = "text/plain",
    file_name: str = "notes.txt",
    content: bytes = b"controlled archive content",
) -> tuple[User, FileRecord]:
    relative_path = f"dataset/soil-samples/{file_name}"
    destination = root / relative_path
    destination.parent.mkdir(parents=True)
    destination.write_bytes(content)
    stat = destination.stat()
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
        file_name=file_name,
        file_kind="document",
        mime_type=mime_type,
        file_size=len(content),
        health_status=HealthStatus.HEALTHY,
        modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
    )
    session.add(record)
    session.flush()
    return owner, record


def test_prepare_delivery_resolves_file_and_audits_access(tmp_path: Path) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)

    delivery = open_file_delivery(session, tmp_path, record.id, "download", actor=actor)
    session.commit()

    try:
        assert os.pread(delivery.descriptor, delivery.stat.st_size, 0) == (
            b"controlled archive content"
        )
        assert delivery.file_name == "notes.txt"
        assert delivery.content_disposition == "attachment"
        assert session.scalar(select(Activity.action)) == "downloaded_file"
    finally:
        delivery.close()


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
        open_file_delivery(session, tmp_path, record.id, "download", actor=actor)


def test_delivery_rejects_an_intermediate_directory_symlink(tmp_path: Path) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    dataset_directory = tmp_path / "dataset"
    real_directory = tmp_path / "real-dataset"
    dataset_directory.rename(real_directory)
    dataset_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(FileUnavailableError):
        open_file_delivery(session, tmp_path, record.id, "download", actor=actor)

    assert session.scalars(select(Activity)).all() == []


@pytest.mark.parametrize("change", ["size", "modified-time"])
def test_delivery_rejects_a_file_changed_since_indexing_without_auditing(
    tmp_path: Path, change: str
) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    destination = tmp_path / record.relative_path
    if change == "size":
        destination.write_bytes(b"changed archive content with a different size")
    else:
        original = destination.read_bytes()
        destination.write_bytes(original)
        stat = destination.stat()
        os.utime(destination, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    with pytest.raises(FileUnavailableError):
        open_file_delivery(session, tmp_path, record.id, "download", actor=actor)

    assert session.scalars(select(Activity)).all() == []


def test_rescan_refreshes_the_snapshot_and_restores_delivery(tmp_path: Path) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    destination = tmp_path / record.relative_path
    destination.write_bytes(b"updated archive content")

    with pytest.raises(FileUnavailableError):
        open_file_delivery(session, tmp_path, record.id, "download", actor=actor)

    scan_storage(session, tmp_path)
    delivery = open_file_delivery(session, tmp_path, record.id, "download", actor=actor)

    try:
        assert os.pread(delivery.descriptor, delivery.stat.st_size, 0) == (
            b"updated archive content"
        )
        assert session.scalar(select(Activity.action)) == "downloaded_file"
    finally:
        delivery.close()


def test_delivery_keeps_the_validated_file_open_when_path_is_replaced(tmp_path: Path) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    destination = tmp_path / record.relative_path
    delivery = open_file_delivery(session, tmp_path, record.id, "download", actor=actor)

    destination.rename(destination.with_suffix(".original"))
    destination.write_bytes(b"replacement archive content")

    try:
        assert os.pread(delivery.descriptor, delivery.stat.st_size, 0) == (
            b"controlled archive content"
        )
    finally:
        delivery.close()


def test_delivery_close_releases_the_validated_file_descriptor(tmp_path: Path) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    delivery = open_file_delivery(session, tmp_path, record.id, "download", actor=actor)
    descriptor = delivery.descriptor

    delivery.close()

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_file_access_token_contains_only_the_grant_identity() -> None:
    grant_id = uuid4()
    token = create_file_access_token(grant_id, datetime.now(UTC) + timedelta(minutes=2))

    claims = read_file_access_token(token)

    assert claims is not None
    assert claims.grant_id == grant_id


def test_file_content_endpoint_streams_original_pdf_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    pdf_content = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
    actor, record = create_file_record(
        session,
        tmp_path,
        mime_type="application/pdf",
        file_name="paper.pdf",
        content=pdf_content,
    )
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
        assert content_response.content == pdf_content
        assert content_response.headers["content-length"] == str(len(pdf_content))
        assert content_response.headers["content-type"] == "application/pdf"
        assert content_response.headers["content-disposition"].startswith("attachment;")
        assert "paper.pdf" in content_response.headers["content-disposition"]
        replay_response = client.get(ticket_response.json()["content_url"])
        assert replay_response.status_code == 200
        assert replay_response.content == pdf_content
        range_response = client.get(
            ticket_response.json()["content_url"], headers={"Range": "bytes=0-9"}
        )
        assert range_response.status_code == 206
        assert range_response.content == pdf_content[:10]
        assert range_response.headers["content-range"] == f"bytes 0-9/{len(pdf_content)}"
        assert session.scalars(select(Activity.action)).all() == ["downloaded_file"]
        grant = session.scalar(select(FileAccessGrant))
        assert grant is not None
        assert grant.first_accessed_at is not None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_file_content_endpoint_streams_the_open_file_after_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    original_content = b"validated file content"
    replacement_content = b"replacement file bytes"
    actor, record = create_file_record(session, tmp_path, content=original_content)
    session.commit()
    destination = tmp_path / record.relative_path
    original_commit = session.commit
    commit_count = 0

    def commit_and_replace_path() -> None:
        nonlocal commit_count
        commit_count += 1
        original_commit()
        if commit_count == 2:
            destination.rename(destination.with_suffix(".original"))
            destination.write_bytes(replacement_content)

    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(session, "commit", commit_and_replace_path)
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        ticket_response = client.post(
            f"/api/files/{record.id}/tickets",
            json={"mode": "download"},
            headers={"X-Sage-Session": create_session_token(actor.username or "")},
        )

        response = client.get(ticket_response.json()["content_url"])

        assert response.status_code == 200
        assert response.content == original_content
        assert response.headers["content-length"] == str(len(original_content))
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_file_content_endpoint_closes_the_open_file_when_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    session.commit()
    original_open_delivery = file_routes.open_file_delivery
    original_commit = session.commit
    opened_descriptor: int | None = None
    commit_count = 0

    def capture_opened_descriptor(*args, **kwargs):
        nonlocal opened_descriptor
        delivery = original_open_delivery(*args, **kwargs)
        opened_descriptor = delivery.descriptor
        return delivery

    def fail_content_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise RuntimeError("commit failed")
        original_commit()

    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(file_routes, "open_file_delivery", capture_opened_descriptor)
    monkeypatch.setattr(session, "commit", fail_content_commit)
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        ticket_response = client.post(
            f"/api/files/{record.id}/tickets",
            json={"mode": "download"},
            headers={"X-Sage-Session": create_session_token(actor.username or "")},
        )

        with pytest.raises(RuntimeError, match="commit failed"):
            client.get(ticket_response.json()["content_url"])

        assert opened_descriptor is not None
        with pytest.raises(OSError):
            os.fstat(opened_descriptor)
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_file_access_grant_cannot_be_used_for_another_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    actor, record = create_file_record(session, tmp_path)
    other_path = tmp_path / "dataset/soil-samples/other.txt"
    other_path.write_bytes(b"other controlled content")
    other_stat = other_path.stat()
    other_record = FileRecord(
        asset_id=record.asset_id,
        relative_path="dataset/soil-samples/other.txt",
        file_name="other.txt",
        file_kind="document",
        mime_type="text/plain",
        file_size=other_stat.st_size,
        health_status=HealthStatus.HEALTHY,
        modified_at=datetime.fromtimestamp(other_stat.st_mtime, UTC),
    )
    session.add(other_record)
    session.commit()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        ticket_response = client.post(
            f"/api/files/{record.id}/tickets",
            json={"mode": "preview"},
            headers={"X-Sage-Session": create_session_token(actor.username or "")},
        )
        ticket = ticket_response.json()["content_url"].split("ticket=", 1)[1]

        response = client.get(f"/api/files/{other_record.id}/content?ticket={ticket}")

        assert response.status_code == 403
        assert session.scalars(select(Activity)).all() == []
        grant = session.scalar(select(FileAccessGrant))
        assert grant is not None
        assert grant.first_accessed_at is None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_file_content_endpoint_rejects_changes_after_ticket_creation(
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
        destination = tmp_path / record.relative_path
        ticket_response = client.post(
            f"/api/files/{record.id}/tickets",
            json={"mode": "download"},
            headers={"X-Sage-Session": create_session_token(actor.username or "")},
        )
        destination.write_bytes(b"changed after ticket creation")

        content_response = client.get(ticket_response.json()["content_url"])

        assert content_response.status_code == 409
        assert content_response.json()["detail"] == "文件当前不可用，请先重新扫描归档。"
        assert session.scalars(select(Activity)).all() == []
        assert session.scalar(select(FileAccessGrant.id)) is not None

        scan_storage(session, tmp_path)
        session.commit()
        retry_response = client.get(ticket_response.json()["content_url"])

        assert retry_response.status_code == 200
        assert retry_response.content == b"changed after ticket creation"
        assert session.scalars(select(Activity.action)).all() == ["downloaded_file"]
        grant = session.scalar(select(FileAccessGrant))
        assert grant is not None
        assert grant.first_accessed_at is not None
    finally:
        app.dependency_overrides.clear()
        session.close()

def test_asset_detail_endpoint_returns_file_records(
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
        response = client.get(
            f"/api/assets/{record.asset_id}",
            headers={"X-Sage-Session": create_session_token(actor.username or "")},
        )

        assert response.status_code == 200
        assert response.json()["files"][0]["relative_path"] == record.relative_path
    finally:
        app.dependency_overrides.clear()
        session.close()
