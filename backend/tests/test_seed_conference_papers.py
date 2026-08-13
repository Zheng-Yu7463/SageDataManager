from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.domain.enums import AssetType, Visibility
from app.domain.models import Asset, FileRecord, User
from app.domain.schemas import PublicationMetadata
from app.services.paper_identity import PublicationIdentityConflictError, normalize_identity_text
from scripts import seed_conference_papers


def test_normalized_identity_ignores_punctuation_case_and_diacritics() -> None:
    assert normalize_identity_text("Abele Mălan") == "abelemalan"
    assert normalize_identity_text("Vision–Language Models") == (
        normalize_identity_text("vision language models")
    )
    assert (
        seed_conference_papers.title_search_query(
            "VITA: Zero-Shot Value Functions via Test-Time Adaptation"
        )
        == "VITA Zero Shot Value Functions via"
    )


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


def test_parse_arxiv_feed_builds_preprint_metadata() -> None:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2608.12308v1</id>
        <title>Example AI Paper</title>
        <published>2026-08-12T17:54:33Z</published>
        <summary>Official abstract.</summary>
        <link href="https://arxiv.org/pdf/2608.12308v1" type="application/pdf" />
        <author><name>Ada Lovelace</name></author>
      </entry>
    </feed>"""

    paper = seed_conference_papers.parse_arxiv_feed(payload, 1)[0]

    assert paper.slug == "arxiv-2608-12308"
    assert paper.metadata.source_id == "arxiv:2608.12308"
    assert paper.metadata.entry_type == "misc"
    assert str(paper.metadata.pdf_url) == "https://arxiv.org/pdf/2608.12308"


def test_parse_biorxiv_response_deduplicates_versions() -> None:
    item = {
        "title": "Biology Preprint",
        "authors": "Lovelace, A.; Hopper, G.",
        "doi": "10.64898/2026.01.01.123456",
        "date": "2026-08-01",
        "version": "2",
        "category": "bioinformatics",
        "abstract": "Official abstract.",
    }
    payload = json.dumps({"collection": [item, {**item, "version": "1"}]}).encode()

    paper = seed_conference_papers.parse_biorxiv_response(payload, 1)[0]

    assert paper.metadata.authors == ["A. Lovelace", "G. Hopper"]
    assert paper.metadata.doi == "10.64898/2026.01.01.123456"
    assert str(paper.metadata.pdf_url).endswith("v2.full.pdf")


def test_parse_plos_response_builds_journal_metadata() -> None:
    payload = json.dumps(
        {
            "message": {
                "items": [
                    {
                        "DOI": "10.1371/journal.pone.0353232",
                        "title": ["PLOS Article"],
                        "author": [{"given": "Ada", "family": "Lovelace"}],
                        "abstract": "<jats:p>Official abstract.</jats:p>",
                        "published": {"date-parts": [[2026, 8, 12]]},
                        "URL": "https://doi.org/10.1371/journal.pone.0353232",
                        "volume": "21",
                        "issue": "8",
                        "page": "e0353232",
                        "publisher": "Public Library of Science",
                        "container-title": ["PLOS One"],
                    }
                ]
            }
        }
    ).encode()

    paper = seed_conference_papers.parse_plos_response(payload, 1)[0]

    assert paper.summary == "Official abstract."
    assert paper.metadata.entry_type == "article"
    assert paper.metadata.journal == "PLOS One"
    assert paper.metadata.issue == "8"
    assert str(paper.metadata.pdf_url).endswith("id=10.1371/journal.pone.0353232&type=printable")


def test_collect_papers_routes_open_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(
        seed_conference_papers,
        "collect_arxiv_papers",
        lambda year, limit: calls.append(("ARXIV", year, limit)) or [],
    )
    monkeypatch.setattr(
        seed_conference_papers,
        "collect_biorxiv_papers",
        lambda year, limit: calls.append(("BIORXIV", year, limit)) or [],
    )
    monkeypatch.setattr(
        seed_conference_papers,
        "collect_plos_papers",
        lambda year, limit: calls.append(("PLOS", year, limit)) or [],
    )

    seed_conference_papers.collect_papers(venues=("ARXIV", "BIORXIV", "PLOS"), year=2026, limit=10)

    assert calls == [
        ("ARXIV", 2026, 10),
        ("BIORXIV", 2026, 10),
        ("PLOS", 2026, 10),
    ]


def test_download_papers_skips_an_existing_valid_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    paper = conference_paper()
    destination = tmp_path / "literature" / paper.slug / "original" / "paper.pdf"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"%PDF existing")
    monkeypatch.setattr(seed_conference_papers, "validate_pdf", lambda path: None)
    monkeypatch.setattr(
        seed_conference_papers,
        "fetch",
        lambda url: pytest.fail("不应重复下载已经验证的 PDF"),
    )

    result = seed_conference_papers.download_papers([paper], tmp_path)

    assert result.downloaded == 0
    assert result.skipped == 1
    assert result.failures == ()


def test_migrate_publications_moves_pdf_and_updates_file_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    session = make_session()
    publication = conference_paper()
    asset = existing_paper(session, publication)
    source = seed_conference_papers.legacy_paper_pdf_path(tmp_path, publication)
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF fixture")
    session.add(
        FileRecord(
            asset_id=asset.id,
            relative_path=source.relative_to(tmp_path).as_posix(),
            file_name="paper.pdf",
            file_kind="document",
            file_size=12,
        )
    )
    session.commit()
    monkeypatch.setattr(seed_conference_papers, "validate_pdf", lambda path: None)
    monkeypatch.setattr(seed_conference_papers, "SessionLocal", sessionmaker(bind=session.bind))

    result = seed_conference_papers.migrate_publications([publication], tmp_path)

    destination = seed_conference_papers.publication_pdf_path(tmp_path, publication)
    assert result.updated == 1
    assert destination.is_file()
    assert not source.exists()
    with Session(session.bind) as verification:
        migrated = verification.get(Asset, asset.id)
        assert migrated is not None
        assert migrated.type == AssetType.LITERATURE
        assert migrated.files[0].relative_path == destination.relative_to(tmp_path).as_posix()


def test_migrate_publications_restores_pdf_when_database_update_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    publication = conference_paper()
    source = seed_conference_papers.legacy_paper_pdf_path(tmp_path, publication)
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF fixture")
    monkeypatch.setattr(seed_conference_papers, "validate_pdf", lambda path: None)
    monkeypatch.setattr(
        seed_conference_papers,
        "upsert_metadata",
        lambda publications, relocations: (_ for _ in ()).throw(RuntimeError("database failed")),
    )

    with pytest.raises(RuntimeError, match="database failed"):
        seed_conference_papers.migrate_publications([publication], tmp_path)

    destination = seed_conference_papers.publication_pdf_path(tmp_path, publication)
    assert source.is_file()
    assert not destination.exists()


def test_migrate_publications_removes_empty_legacy_directory_without_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    publication = conference_paper()
    legacy_directory = seed_conference_papers.legacy_paper_pdf_path(
        tmp_path, publication
    ).parent
    legacy_directory.mkdir(parents=True)
    monkeypatch.setattr(
        seed_conference_papers,
        "upsert_metadata",
        lambda publications, relocations: seed_conference_papers.MetadataSyncResult(
            created=0, updated=1, skipped=0
        ),
    )

    seed_conference_papers.migrate_publications([publication], tmp_path)

    assert not legacy_directory.exists()


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
) -> seed_conference_papers.PublicationRecord:
    return seed_conference_papers.PublicationRecord(
        slug=slug,
        title=title,
        summary="Official abstract.",
        metadata=PublicationMetadata(
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


def existing_paper(session: Session, paper: seed_conference_papers.PublicationRecord) -> Asset:
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
    incoming: seed_conference_papers.PublicationRecord,
) -> None:
    session = make_session()
    original = existing_paper(session, conference_paper())
    monkeypatch.setattr(seed_conference_papers, "SessionLocal", sessionmaker(bind=session.bind))

    first_result = seed_conference_papers.upsert_metadata([incoming])
    second_result = seed_conference_papers.upsert_metadata([incoming])

    with Session(session.bind) as verification:
        publications = verification.scalars(
            select(Asset).where(Asset.type == AssetType.LITERATURE)
        ).all()
        assert len(publications) == 1
        assert publications[0].id == original.id
        assert publications[0].details["source_id"] == incoming.metadata.source_id
        assert first_result.updated == 1
        assert second_result.skipped == 1


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

    with pytest.raises(PublicationIdentityConflictError, match="指向了不同"):
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
        assert verification.scalar(select(Asset).where(Asset.slug == "would-roll-back")) is None
