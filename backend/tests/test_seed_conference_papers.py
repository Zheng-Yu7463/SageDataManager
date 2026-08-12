from __future__ import annotations

import pytest

from scripts import seed_conference_papers


def test_normalized_identity_ignores_punctuation_case_and_diacritics() -> None:
    assert seed_conference_papers.normalized_identity("Abele Mălan") == "abelemalan"
    assert seed_conference_papers.normalized_identity("Vision–Language Models") == (
        seed_conference_papers.normalized_identity("vision language models")
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
