from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.enums import AssetType, Visibility
from app.domain.schemas import AssetCreateRequest
from app.main import app
from app.services.accounts import ensure_fixed_accounts
from app.services.assets import create_asset
from app.services.security import create_session_token


def make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def create_publication(
    session: Session,
    *,
    venue: str,
    year: int,
    slug: str,
    asset_type: AssetType = AssetType.PAPER,
) -> str:
    owner = ensure_fixed_accounts(session)[1]
    paper = create_asset(
        session,
        AssetCreateRequest(
            type=asset_type,
            slug=slug,
            title=f"{venue} Citation Paper",
            status="published",
            visibility=Visibility.LAB,
            details={
                "venue": venue,
                "year": year,
                "track": "Main Conference",
                "authors": ["Ada Lovelace", "Grace Hopper"],
                "source_id": slug,
                "source_url": f"https://example.com/{slug}",
                "pdf_url": f"https://example.com/{slug}.pdf",
            },
        ),
        actor=owner,
    )
    return str(paper.id)


def test_bibtex_endpoints_require_admin_and_preserve_filters(
    monkeypatch,
) -> None:
    session = make_session()
    acl_id = create_publication(
        session, venue="ACL", year=2026, slug="acl-citation-paper"
    )
    create_publication(
        session,
        venue="ICLR",
        year=2025,
        slug="iclr-citation-paper",
        asset_type=AssetType.LITERATURE,
    )
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        unauthenticated = client.get("/api/assets/citations/bibtex?venue=ACL&year=2026")
        assert unauthenticated.status_code == 401

        headers = {"X-Sage-Session": create_session_token("zhengyu")}
        filtered = client.get(
            "/api/assets/citations/bibtex?venue=ACL&year=2026",
            headers=headers,
        )
        literature = client.get(
            "/api/assets/citations/bibtex?asset_type=literature",
            headers=headers,
        )
        single = client.get(
            f"/api/assets/{acl_id}/citation/bibtex",
            headers=headers,
        )

        assert filtered.status_code == 200
        assert filtered.json()["count"] == 1
        assert "ACL Citation Paper" in filtered.json()["bibtex"]
        assert "ICLR Citation Paper" not in filtered.json()["bibtex"]
        assert literature.status_code == 200
        assert literature.json()["count"] == 1
        assert literature.json()["filename"] == "sage-literature.bib"
        assert "ICLR Citation Paper" in literature.json()["bibtex"]
        assert single.status_code == 200
        assert single.json()["filename"] == "acl-citation-paper.bib"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_article_bibtex_uses_journal_instead_of_booktitle() -> None:
    session = make_session()
    owner = ensure_fixed_accounts(session)[1]
    paper = create_asset(
        session,
        AssetCreateRequest(
            type=AssetType.LITERATURE,
            slug="journal-article",
            title="Journal Article",
            status="published",
            visibility=Visibility.LAB,
            details={
                "venue": "PLOS ONE",
                "year": 2026,
                "track": "Research Article",
                "authors": ["Ada Lovelace"],
                "source_id": "crossref:10.1371/example",
                "source_url": "https://doi.org/10.1371/example",
                "pdf_url": "https://example.com/article.pdf",
                "entry_type": "article",
                "journal": "PLOS One",
                "issue": "8",
            },
        ),
        actor=owner,
    )
    session.commit()

    from app.services.citations import build_publication_citation

    citation = build_publication_citation(paper)

    assert "@article{" in citation.bibtex
    assert "journal = {PLOS One}" in citation.bibtex
    assert "number = {8}" in citation.bibtex
    assert "booktitle" not in citation.bibtex
    session.close()
