from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.domain.enums import AssetType, Visibility
from app.domain.models import Asset, User
from app.domain.schemas import PaperMetadata
from app.services.paper_identity import PaperIdentityConflictError, normalize_identity_text
from scripts import seed_conference_papers


def test_normalized_identity_ignores_punctuation_case_and_diacritics() -> None:
    assert normalize_identity_text("Abele Mălan") == "abelemalan"
    assert normalize_identity_text("Vision–Language Models") == (
        normalize_identity_text("vision language models")
    )
    assert seed_conference_papers.title_search_query(
        "VITA: Zero-Shot Value Functions via Test-Time Adaptation"
    ) == "VITA Zero Shot Value Functions via"


def test_find_iclr_proceedings_page_validates_title_and_first_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_page = b"""
        <a href="/paper_files/paper/2026/hash/paper-Abstract-Conference.html">
          VITA: Zero-Shot Value Functions via Test-Time Adaptation of Vision-Language Models
        </a>
    """
    publication_page = b"""
        <meta name="citation_title" content="VITA: Zero-Shot Value Functions
              via Test-Time Adaptation of Vision-Language Models">
        <meta name="citation_author" content="Ziakas, Christos">
        <meta name="citation_author" content="Russo, Alessandra">
        <meta name="citation_pdf_url" content="https://proceedings.iclr.cc/paper.pdf">
        <meta name="citation_publication_date" content="2026-04-20">
    """

    def fake_fetch(url: str) -> bytes:
        return search_page if "/papers/search?" in url else publication_page

    monkeypatch.setattr(seed_conference_papers, "fetch", fake_fetch)

    publication_url, parser = seed_conference_papers.find_iclr_proceedings_page(
        "VITA: Zero-Shot Value Functions via Test-Time Adaptation of Vision-Language Models",
        "Christos Ziakas",
    )

    assert publication_url.endswith("paper-Abstract-Conference.html")
    assert parser.meta["citation_pdf_url"] == ["https://proceedings.iclr.cc/paper.pdf"]


def test_find_iclr_proceedings_page_rejects_wrong_first_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_page = b"""
        <a href="/paper_files/paper/2026/hash/paper-Abstract-Conference.html">
          Example Paper
        </a>
    """
    publication_page = b"""
        <meta name="citation_title" content="Example Paper">
        <meta name="citation_author" content="Different, Author">
    """
    monkeypatch.setattr(
        seed_conference_papers,
        "fetch",
        lambda url: search_page if "/papers/search?" in url else publication_page,
    )

    with pytest.raises(ValueError, match="首位作者校验失败"):
        seed_conference_papers.find_iclr_proceedings_page("Example Paper", "Expected Author")


def test_collect_papers_uses_requested_venue_year_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acl_ids: list[str] = []
    iclr_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        seed_conference_papers,
        "parse_acl_paper",
        lambda anthology_id: acl_ids.append(anthology_id),
    )
    monkeypatch.setattr(
        seed_conference_papers,
        "parse_iclr_paper",
        lambda poster_id, year: iclr_calls.append((poster_id, year)),
    )

    seed_conference_papers.collect_papers(
        venues=("ACL", "ICLR"),
        year=2025,
        limit=2,
        iclr_poster_ids=("poster-a", "poster-b"),
    )

    assert acl_ids == ["2025.acl-long.1", "2025.acl-long.2"]
    assert iclr_calls == [("poster-a", 2025), ("poster-b", 2025)]


def test_collect_papers_requires_iclr_ids_outside_default_year() -> None:
    with pytest.raises(ValueError, match="--iclr-poster-id"):
        seed_conference_papers.collect_papers(venues=("ICLR",), year=2025, limit=1)


def test_citation_pages_combines_first_and_last_page() -> None:
    parser = seed_conference_papers.PaperPageParser()
    parser.meta = {"citation_firstpage": ["101"], "citation_lastpage": ["112"]}

    assert seed_conference_papers.citation_pages(parser) == "101--112"


def make_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def conference_paper(
    *,
    slug: str = "canonical-paper",
    title: str = "Vision-Language Models",
    first_author: str = "Abele Mălan",
    source_id: str = "Official.2026.1",
    doi: str | None = "10.1000/canonical",
) -> seed_conference_papers.ConferencePaper:
    return seed_conference_papers.ConferencePaper(
        slug=slug,
        title=title,
        summary="Official abstract.",
        metadata=PaperMetadata(
            venue="ACL",
            year=2026,
            track="Main Conference",
            authors=[first_author],
            source_id=source_id,
            source_url="https://example.org/source",
            publication_url="https://example.org/publication",
            pdf_url="https://example.org/paper.pdf",
            doi=doi,
        ),
    )


def existing_paper(session: Session, paper: seed_conference_papers.ConferencePaper) -> Asset:
    owner = User(username="zhengyu", name="zhengyu", email="zhengyu@sage.lab")
    asset = Asset(
        type=AssetType.PAPER,
        slug=paper.slug,
        title=paper.title,
        summary=paper.summary,
        status="published",
        visibility=Visibility.LAB,
        owner=owner,
        details=paper.metadata.model_dump(mode="json", exclude_none=True),
    )
    session.add(asset)
    session.commit()
    return asset


@pytest.mark.parametrize(
    "incoming",
    [
        pytest.param(
            conference_paper(
                title="Renamed Official Paper",
                first_author="Different Author",
                source_id="OFFICIAL.2026.1",
                doi="10.1000/changed",
            ),
            id="source-id-only",
        ),
        pytest.param(
            conference_paper(
                title="Renamed DOI Paper",
                first_author="Different Author",
                source_id="changed-source",
                doi="https://doi.org/10.1000/canonical",
            ),
            id="doi-only",
        ),
        pytest.param(
            conference_paper(
                title="Vision Language Models",
                first_author="Abele Malan",
                source_id="changed-source",
                doi="10.1000/changed",
            ),
            id="title-and-first-author-only",
        ),
    ],
)
def test_upsert_updates_one_canonical_paper_without_creating_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    incoming: seed_conference_papers.ConferencePaper,
) -> None:
    session = make_session()
    original = existing_paper(session, conference_paper())
    monkeypatch.setattr(seed_conference_papers, "SessionLocal", sessionmaker(bind=session.bind))

    seed_conference_papers.upsert_metadata([incoming])
    seed_conference_papers.upsert_metadata([incoming])

    with Session(session.bind) as verification:
        papers = verification.scalars(select(Asset).where(Asset.type == AssetType.PAPER)).all()
        assert len(papers) == 1
        assert papers[0].id == original.id
        assert papers[0].details["source_id"] == incoming.metadata.source_id


def test_upsert_allows_same_title_with_a_different_first_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    existing_paper(session, conference_paper())
    monkeypatch.setattr(seed_conference_papers, "SessionLocal", sessionmaker(bind=session.bind))

    seed_conference_papers.upsert_metadata(
        [
            conference_paper(
                slug="different-author",
                first_author="Different Author",
                source_id="different-source",
                doi="10.1000/different",
            )
        ]
    )

    with Session(session.bind) as verification:
        assert verification.scalar(select(func.count()).select_from(Asset)) == 2


def test_upsert_rolls_back_when_identifiers_point_to_different_papers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    first = conference_paper(slug="first", source_id="source-one", doi="10.1000/one")
    second = conference_paper(
        slug="second",
        title="Different Paper",
        first_author="Second Author",
        source_id="source-two",
        doi="10.1000/two",
    )
    existing_paper(session, first)
    second_owner = session.scalar(select(User).where(User.username == "zhengyu"))
    assert second_owner is not None
    session.add(
        Asset(
            type=AssetType.PAPER,
            slug=second.slug,
            title=second.title,
            summary=second.summary,
            status="published",
            visibility=Visibility.LAB,
            owner=second_owner,
            details=second.metadata.model_dump(mode="json", exclude_none=True),
        )
    )
    session.commit()
    monkeypatch.setattr(seed_conference_papers, "SessionLocal", sessionmaker(bind=session.bind))
    before = session.scalar(select(func.count()).select_from(Asset))
    conflicting = conference_paper(
        slug="conflicting",
        source_id=first.metadata.source_id,
        doi=second.metadata.doi,
    )

    with pytest.raises(PaperIdentityConflictError, match="指向了不同"):
        seed_conference_papers.upsert_metadata(
            [
                conference_paper(
                    slug="would-roll-back",
                    title="New Paper Before Conflict",
                    first_author="New Author",
                    source_id="new",
                    doi="10.1000/new",
                ),
                conflicting,
            ]
        )

    with Session(session.bind) as verification:
        assert verification.scalar(select(func.count()).select_from(Asset)) == before
        assert verification.scalar(
            select(Asset).where(Asset.slug == "would-roll-back")
        ) is None
