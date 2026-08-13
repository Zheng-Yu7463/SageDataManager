from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin

from sqlalchemy import select

from app.db.session import SessionLocal
from app.domain.activity import ActivityAction
from app.domain.enums import AssetType, Visibility
from app.domain.models import Activity, Asset, AssetVersion, FileRecord, Tag
from app.domain.schemas import PublicationMetadata
from app.services.accounts import ensure_fixed_accounts
from app.services.paper_identity import normalize_identity_text, resolve_publication

DEFAULT_ICLR_POSTER_IDS = (
    "10006831",
    "10009179",
    "10010095",
    "10008049",
    "10006742",
    "10007238",
    "10011153",
    "10007821",
    "10008342",
    "10011722",
)
USER_AGENT = "SageDataManager/0.1 (paper importer)"
ICLR_PROCEEDINGS_ROOT = "https://proceedings.iclr.cc"
ARXIV_API_ROOT = "https://export.arxiv.org/api/query"
BIORXIV_API_ROOT = "https://api.biorxiv.org/details/biorxiv"
CROSSREF_API_ROOT = "https://api.crossref.org"
SUPPORTED_VENUES = ("ACL", "ICLR", "ARXIV", "BIORXIV", "PLOS")
VENUE_LABELS = {
    "ACL": "ACL",
    "ICLR": "ICLR",
    "ARXIV": "arXiv",
    "BIORXIV": "bioRxiv",
    "PLOS": "PLOS ONE",
}


@dataclass(frozen=True)
class PublicationRecord:
    slug: str
    title: str
    summary: str
    metadata: PublicationMetadata


@dataclass(frozen=True)
class RelocatedPdf:
    slug: str
    source: Path
    destination: Path
    source_relative_path: str
    destination_relative_path: str
    moved: bool


@dataclass(frozen=True)
class MetadataSyncResult:
    created: int
    updated: int
    skipped: int


@dataclass(frozen=True)
class PdfDownloadResult:
    downloaded: int
    skipped: int
    failures: tuple[str, ...]


class PaperPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, list[str]] = {}
        self.json_ld: list[str] = []
        self.abstract_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._json_depth = 0
        self._abstract_depth = 0
        self._link_href: str | None = None
        self._link_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name") and attributes.get("content"):
            self.meta.setdefault(attributes["name"], []).append(attributes["content"])
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._json_depth = 1
        elif self._json_depth:
            self._json_depth += 1
        classes = set((attributes.get("class") or "").split())
        if tag == "div" and ({"abstract-text-inner", "acl-abstract"} & classes):
            self._abstract_depth = 1
        elif self._abstract_depth:
            self._abstract_depth += 1
        if tag == "a" and attributes.get("href"):
            self._link_href = attributes["href"]
            self._link_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self._json_depth:
            self._json_depth -= 1
        if self._abstract_depth:
            self._abstract_depth -= 1
        if tag == "a" and self._link_href:
            text = " ".join(" ".join(self._link_parts).split())
            self.links.append((self._link_href, text))
            self._link_href = None
            self._link_parts = []

    def handle_data(self, data: str) -> None:
        if self._json_depth:
            self.json_ld.append(data)
        if self._abstract_depth:
            self.abstract_parts.append(data)
        if self._link_href:
            self._link_parts.append(data)

    @property
    def abstract(self) -> str:
        text = " ".join(" ".join(self.abstract_parts).split())
        return text.removeprefix("Abstract ")


def fetch(url: str) -> bytes:
    with tempfile.NamedTemporaryFile() as destination:
        result = subprocess.run(
            [
                "curl",
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--retry",
                "5",
                "--retry-all-errors",
                "--connect-timeout",
                "20",
                "--max-time",
                "180",
                "--user-agent",
                USER_AGENT,
                "--output",
                destination.name,
                url,
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"无法读取官方来源 {url}：{message}")
        destination.seek(0)
        return destination.read()


def citation_author_name(value: str) -> str:
    family_name, separator, given_name = value.partition(",")
    if not separator:
        return value.strip()
    return f"{given_name.strip()} {family_name.strip()}"


def title_search_query(title: str) -> str:
    terms = re.findall(r"\w+", title, flags=re.UNICODE)
    return " ".join(terms[:6])


def find_iclr_proceedings_page(title: str, first_author: str) -> tuple[str, PaperPageParser]:
    query = urlencode({"q": title_search_query(title)})
    search_url = f"{ICLR_PROCEEDINGS_ROOT}/papers/search?{query}"
    search_parser = PaperPageParser()
    search_parser.feed(fetch(search_url).decode("utf-8"))
    candidates = [
        urljoin(ICLR_PROCEEDINGS_ROOT, href)
        for href, link_text in search_parser.links
        if "-Abstract-Conference.html" in href
        and normalize_identity_text(link_text) == normalize_identity_text(title)
    ]
    if len(candidates) != 1:
        raise ValueError(f"ICLR proceedings 未找到唯一同题名论文：{title}")

    publication_url = candidates[0]
    publication_parser = PaperPageParser()
    publication_parser.feed(fetch(publication_url).decode("utf-8"))
    publication_title = publication_parser.meta.get("citation_title", [""])[0]
    publication_authors = publication_parser.meta.get("citation_author", [])
    if normalize_identity_text(publication_title) != normalize_identity_text(title):
        raise ValueError(f"ICLR proceedings 题名校验失败：{title}")
    if not publication_authors or normalize_identity_text(
        citation_author_name(publication_authors[0])
    ) != normalize_identity_text(first_author):
        raise ValueError(f"ICLR proceedings 首位作者校验失败：{title}")
    return publication_url, publication_parser


def citation_key(venue: str, year: int, source_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9]+", "", source_id)
    return f"{venue.lower()}{year}{suffix.removeprefix(str(year))}"


def strip_markup(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", value).split())


def date_parts(value: dict[str, object]) -> tuple[int, int | None, int | None]:
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
        raise ValueError("官方元数据缺少发布日期。")
    values = parts[0]
    if not values or not isinstance(values[0], int):
        raise ValueError("官方元数据发布日期格式无效。")
    return (
        values[0],
        values[1] if len(values) > 1 and isinstance(values[1], int) else None,
        values[2] if len(values) > 2 and isinstance(values[2], int) else None,
    )


def iso_publication_date(year: int, month: int | None, day: int | None) -> str:
    return date(year, month or 1, day or 1).isoformat()


def first_meta(parser: PaperPageParser, *names: str) -> str | None:
    return next(
        (
            parser.meta[name][0].strip()
            for name in names
            if parser.meta.get(name) and parser.meta[name][0].strip()
        ),
        None,
    )


def citation_pages(parser: PaperPageParser) -> str | None:
    pages = first_meta(parser, "citation_pages")
    if pages:
        return pages
    first_page = first_meta(parser, "citation_firstpage")
    last_page = first_meta(parser, "citation_lastpage")
    if first_page and last_page and first_page != last_page:
        return f"{first_page}--{last_page}"
    return first_page


def parse_iclr_paper(poster_id: str, year: int = 2026) -> PublicationRecord:
    source_url = f"https://iclr.cc/virtual/{year}/poster/{poster_id}"
    page = fetch(source_url).decode("utf-8")
    parser = PaperPageParser()
    parser.feed(page)
    structured = json.loads("".join(parser.json_ld))
    forum_match = re.search(r"openreview\.net/forum\?id=([A-Za-z0-9_-]+)", page)
    if not forum_match:
        raise ValueError(f"ICLR 页面缺少 OpenReview 标识：{source_url}")
    forum_id = forum_match.group(1)
    title = structured["name"]
    virtual_authors = [author["name"] for author in structured["author"]]
    publication_url, publication_parser = find_iclr_proceedings_page(title, virtual_authors[0])
    publication_authors = [
        citation_author_name(author) for author in publication_parser.meta["citation_author"]
    ]
    metadata = PublicationMetadata(
        venue="ICLR",
        year=year,
        track="Conference Poster",
        authors=publication_authors,
        source_id=f"iclr-{year}-{forum_id}",
        source_url=source_url,
        publication_url=publication_url,
        pdf_url=publication_parser.meta["citation_pdf_url"][0],
        abstract=parser.abstract,
        published_at=publication_parser.meta["citation_publication_date"][0],
        citation_key=citation_key("ICLR", year, forum_id),
        booktitle=first_meta(publication_parser, "citation_conference_title", "citation_book_title")
        or f"Proceedings of the International Conference on Learning Representations {year}",
        publisher=first_meta(publication_parser, "citation_publisher"),
    )
    return PublicationRecord(
        slug=f"iclr-{year}-{poster_id}", title=title, summary=parser.abstract, metadata=metadata
    )


def parse_acl_paper(anthology_id: str) -> PublicationRecord:
    source_url = f"https://aclanthology.org/{anthology_id}/"
    parser = PaperPageParser()
    parser.feed(fetch(source_url).decode("utf-8"))
    title = parser.meta["citation_title"][0]
    year = int(anthology_id.split(".", 1)[0])
    metadata = PublicationMetadata(
        venue="ACL",
        year=year,
        track="Main Conference - Long Papers",
        authors=parser.meta["citation_author"],
        source_id=anthology_id,
        source_url=source_url,
        pdf_url=parser.meta["citation_pdf_url"][0],
        abstract=parser.abstract,
        doi=parser.meta["citation_doi"][0],
        published_at=parser.meta["citation_publication_date"][0],
        citation_key=citation_key("ACL", year, anthology_id),
        booktitle=first_meta(parser, "citation_conference_title", "citation_book_title")
        or (
            "Proceedings of the Annual Meeting of the Association for "
            f"Computational Linguistics {year}"
        ),
        pages=citation_pages(parser),
        publisher=first_meta(parser, "citation_publisher")
        or "Association for Computational Linguistics",
    )
    return PublicationRecord(
        slug=anthology_id.replace(".", "-"), title=title, summary=parser.abstract, metadata=metadata
    )


def parse_arxiv_feed(payload: bytes, limit: int) -> list[PublicationRecord]:
    root = ET.fromstring(payload)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    papers: list[PublicationRecord] = []
    for entry in root.findall("atom:entry", namespace)[:limit]:
        source_url = (entry.findtext("atom:id", default="", namespaces=namespace)).strip()
        source_id = source_url.rsplit("/", 1)[-1]
        canonical_id = source_id.split("v", 1)[0]
        title = " ".join(entry.findtext("atom:title", default="", namespaces=namespace).split())
        summary = " ".join(entry.findtext("atom:summary", default="", namespaces=namespace).split())
        published_at = entry.findtext("atom:published", default="", namespaces=namespace)
        authors = [
            " ".join(author.findtext("atom:name", default="", namespaces=namespace).split())
            for author in entry.findall("atom:author", namespace)
        ]
        pdf_url = next(
            (
                link.attrib["href"]
                for link in entry.findall("atom:link", namespace)
                if link.attrib.get("type") == "application/pdf"
            ),
            "",
        )
        if not all((canonical_id, title, summary, published_at, authors, pdf_url)):
            raise ValueError("arXiv 官方响应缺少必要论文元数据。")
        year = int(published_at[:4])
        papers.append(
            PublicationRecord(
                slug=f"arxiv-{canonical_id.replace('.', '-')}",
                title=title,
                summary=summary,
                metadata=PublicationMetadata(
                    venue="arXiv",
                    year=year,
                    track="Computer Science Preprint",
                    authors=authors,
                    source_id=f"arxiv:{canonical_id}",
                    source_url=f"https://arxiv.org/abs/{canonical_id}",
                    pdf_url=f"https://arxiv.org/pdf/{canonical_id}",
                    abstract=summary,
                    published_at=published_at,
                    citation_key=citation_key("arxiv", year, canonical_id),
                    entry_type="misc",
                    publisher="arXiv",
                ),
            )
        )
    if len(papers) < limit:
        raise ValueError(f"arXiv 只返回 {len(papers)} 篇，无法收录 {limit} 篇。")
    return papers


def collect_arxiv_papers(year: int, limit: int) -> list[PublicationRecord]:
    query = urlencode(
        {
            "search_query": f"cat:cs.AI AND submittedDate:[{year}01010000 TO {year}12312359]",
            "start": 0,
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    return parse_arxiv_feed(fetch(f"{ARXIV_API_ROOT}?{query}"), limit)


def biorxiv_author_name(value: str) -> str:
    family, separator, given = value.partition(",")
    return f"{given.strip()} {family.strip()}" if separator else value.strip()


def parse_biorxiv_response(payload: bytes, limit: int) -> list[PublicationRecord]:
    collection = json.loads(payload)["collection"]
    papers: list[PublicationRecord] = []
    seen_dois: set[str] = set()
    for item in collection:
        doi = str(item["doi"]).lower()
        if doi in seen_dois:
            continue
        seen_dois.add(doi)
        published_at = str(item["date"])
        year = int(published_at[:4])
        title = " ".join(str(item["title"]).split())
        summary = " ".join(str(item["abstract"]).split())
        authors = [
            biorxiv_author_name(author)
            for author in str(item["authors"]).split(";")
            if author.strip()
        ]
        suffix = doi.split("/", 1)[1]
        source_url = f"https://www.biorxiv.org/content/{doi}v{item['version']}"
        papers.append(
            PublicationRecord(
                slug=f"biorxiv-{re.sub(r'[^a-z0-9]+', '-', suffix.lower()).strip('-')}",
                title=title,
                summary=summary,
                metadata=PublicationMetadata(
                    venue="bioRxiv",
                    year=year,
                    track=str(item["category"]).title(),
                    authors=authors,
                    source_id=f"biorxiv:{doi}",
                    source_url=source_url,
                    pdf_url=f"{source_url}.full.pdf",
                    abstract=summary,
                    doi=doi,
                    published_at=published_at,
                    citation_key=citation_key("biorxiv", year, suffix),
                    entry_type="misc",
                    publisher="Cold Spring Harbor Laboratory",
                ),
            )
        )
        if len(papers) == limit:
            break
    if len(papers) < limit:
        raise ValueError(f"bioRxiv 只返回 {len(papers)} 篇，无法收录 {limit} 篇。")
    return papers


def collect_biorxiv_papers(year: int, limit: int) -> list[PublicationRecord]:
    end = min(date.today(), date(year, 12, 31))
    start = max(date(year, 1, 1), end - timedelta(days=45))
    url = f"{BIORXIV_API_ROOT}/{start.isoformat()}/{end.isoformat()}/0/json"
    return parse_biorxiv_response(fetch(url), limit)


def crossref_authors(item: dict[str, object]) -> list[str]:
    authors = item.get("author")
    if not isinstance(authors, list):
        return []
    return [
        " ".join(
            part for part in (str(author.get("given", "")), str(author.get("family", ""))) if part
        )
        for author in authors
        if isinstance(author, dict)
    ]


def parse_plos_response(payload: bytes, limit: int) -> list[PublicationRecord]:
    items = json.loads(payload)["message"]["items"]
    papers: list[PublicationRecord] = []
    for item in items[:limit]:
        title_values = item.get("title")
        journals = item.get("container-title")
        if not isinstance(title_values, list) or not title_values:
            raise ValueError("Crossref 官方响应缺少 PLOS 论文标题。")
        if not isinstance(journals, list) or not journals:
            raise ValueError("Crossref 官方响应缺少 PLOS 期刊名称。")
        doi = str(item["DOI"]).lower()
        year, month, day = date_parts(item["published"])
        title = strip_markup(str(title_values[0]))
        summary = strip_markup(str(item.get("abstract", "")))
        if not summary:
            raise ValueError(f"PLOS 论文缺少摘要：{doi}")
        authors = crossref_authors(item)
        if not authors:
            raise ValueError(f"PLOS 论文缺少作者：{doi}")
        source_url = f"https://journals.plos.org/plosone/article?id={doi}"
        suffix = doi.split("/", 1)[1]
        papers.append(
            PublicationRecord(
                slug=f"plos-{re.sub(r'[^a-z0-9]+', '-', suffix.lower()).strip('-')}",
                title=title,
                summary=summary,
                metadata=PublicationMetadata(
                    venue="PLOS ONE",
                    year=year,
                    track="Research Article",
                    authors=authors,
                    source_id=f"crossref:{doi}",
                    source_url=source_url,
                    publication_url=str(item.get("URL") or source_url),
                    pdf_url=(
                        f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable"
                    ),
                    abstract=summary,
                    doi=doi,
                    published_at=iso_publication_date(year, month, day),
                    citation_key=citation_key("plos", year, suffix),
                    entry_type="article",
                    journal=str(journals[0]),
                    pages=str(item["page"]) if item.get("page") else None,
                    publisher=str(item["publisher"]) if item.get("publisher") else None,
                    month=str(month) if month else None,
                    volume=str(item["volume"]) if item.get("volume") else None,
                    issue=str(item["issue"]) if item.get("issue") else None,
                ),
            )
        )
    if len(papers) < limit:
        raise ValueError(f"PLOS 只返回 {len(papers)} 篇，无法收录 {limit} 篇。")
    return papers


def collect_plos_papers(year: int, limit: int) -> list[PublicationRecord]:
    end = min(date.today(), date(year, 12, 31))
    start = max(date(year, 1, 1), end - timedelta(days=45))
    query = urlencode(
        {
            "filter": (
                f"from-pub-date:{start.isoformat()},until-pub-date:{end.isoformat()},"
                "type:journal-article,has-abstract:true"
            ),
            "sort": "published",
            "order": "desc",
            "rows": limit,
            "select": (
                "DOI,title,author,abstract,published,URL,volume,issue,page,"
                "publisher,container-title"
            ),
        }
    )
    return parse_plos_response(
        fetch(f"{CROSSREF_API_ROOT}/journals/1932-6203/works?{query}"), limit
    )


def collect_papers(
    *,
    venues: tuple[str, ...] = SUPPORTED_VENUES,
    year: int = 2026,
    limit: int = 10,
    iclr_poster_ids: tuple[str, ...] | None = None,
) -> list[PublicationRecord]:
    papers: list[PublicationRecord] = []
    if "ICLR" in venues:
        poster_ids = iclr_poster_ids
        if poster_ids is None:
            if year != 2026:
                raise ValueError(
                    "非 2026 年 ICLR 同步需要通过 --iclr-poster-id 提供官方 poster ID。"
                )
            poster_ids = DEFAULT_ICLR_POSTER_IDS
        if len(poster_ids) < limit:
            raise ValueError(f"ICLR poster ID 只有 {len(poster_ids)} 个，无法收录 {limit} 篇。")
        papers.extend(parse_iclr_paper(poster_id, year) for poster_id in poster_ids[:limit])
    if "ACL" in venues:
        papers.extend(parse_acl_paper(f"{year}.acl-long.{index}") for index in range(1, limit + 1))
    if "ARXIV" in venues:
        papers.extend(collect_arxiv_papers(year, limit))
    if "BIORXIV" in venues:
        papers.extend(collect_biorxiv_papers(year, limit))
    if "PLOS" in venues:
        papers.extend(collect_plos_papers(year, limit))
    return papers


def validate_pdf(path: Path) -> None:
    if not path.read_bytes().startswith(b"%PDF"):
        raise ValueError(f"下载内容不是 PDF：{path}")
    result = subprocess.run(["pdfinfo", str(path)], capture_output=True, check=False, text=True)
    if result.returncode != 0 or not re.search(r"^Pages:\s+[1-9]\d*$", result.stdout, re.MULTILINE):
        raise ValueError(f"PDF 结构校验失败：{path}")


def publication_pdf_path(archive_root: Path, publication: PublicationRecord) -> Path:
    return archive_root / "literature" / publication.slug / "original" / "paper.pdf"


def legacy_paper_pdf_path(archive_root: Path, publication: PublicationRecord) -> Path:
    return archive_root / "paper" / publication.slug / "manuscript" / "paper.pdf"


def relocate_existing_pdfs(
    publications: list[PublicationRecord], archive_root: Path
) -> dict[str, RelocatedPdf]:
    relocations: dict[str, RelocatedPdf] = {}
    for publication in publications:
        source = legacy_paper_pdf_path(archive_root, publication)
        destination = publication_pdf_path(archive_root, publication)
        if source.is_file() and destination.is_file():
            raise ValueError(f"旧归档与文献归档同时存在 PDF：{publication.slug}")
        if destination.is_file():
            validate_pdf(destination)
            moved = False
        elif source.is_file():
            validate_pdf(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            moved = True
        else:
            continue
        relocations[publication.slug] = RelocatedPdf(
            slug=publication.slug,
            source=source,
            destination=destination,
            source_relative_path=source.relative_to(archive_root).as_posix(),
            destination_relative_path=destination.relative_to(archive_root).as_posix(),
            moved=moved,
        )
    return relocations


def restore_relocated_pdfs(relocations: dict[str, RelocatedPdf]) -> None:
    for relocation in reversed(tuple(relocations.values())):
        if not relocation.moved or not relocation.destination.is_file():
            continue
        relocation.source.parent.mkdir(parents=True, exist_ok=True)
        relocation.destination.replace(relocation.source)


def remove_empty_legacy_directories(
    publications: list[PublicationRecord], archive_root: Path
) -> None:
    for publication in publications:
        legacy_path = legacy_paper_pdf_path(archive_root, publication)
        for directory in (legacy_path.parent, legacy_path.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break


def migrate_publications(
    publications: list[PublicationRecord], archive_root: Path
) -> MetadataSyncResult:
    relocations = relocate_existing_pdfs(publications, archive_root)
    try:
        result = upsert_metadata(publications, relocations)
    except Exception:
        restore_relocated_pdfs(relocations)
        raise
    remove_empty_legacy_directories(publications, archive_root)
    return result


def download_papers(
    publications: list[PublicationRecord], archive_root: Path
) -> PdfDownloadResult:
    failures: list[str] = []
    downloaded = 0
    skipped = 0
    for index, publication in enumerate(publications, start=1):
        destination = publication_pdf_path(archive_root, publication)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".download")
        try:
            if destination.is_file():
                validate_pdf(destination)
                skipped += 1
                print(
                    f"[{index:02d}/{len(publications)}] "
                    f"{publication.metadata.venue} 已验证，跳过下载",
                    flush=True,
                )
                continue
            if publication.metadata.venue == "bioRxiv":
                time.sleep(3)
            content = fetch(str(publication.metadata.pdf_url))
            temporary.write_bytes(content)
            validate_pdf(temporary)
            temporary.replace(destination)
            downloaded += 1
            checksum = hashlib.sha256(content).hexdigest()[:12]
            print(
                f"[{index:02d}/{len(publications)}] "
                f"{publication.metadata.venue} {destination.name} {checksum}",
                flush=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            temporary.unlink(missing_ok=True)
            failures.append(f"{publication.metadata.source_id}: {error}")
    return PdfDownloadResult(
        downloaded=downloaded,
        skipped=skipped,
        failures=tuple(failures),
    )


def upsert_metadata(
    publications: list[PublicationRecord],
    relocations: dict[str, RelocatedPdf] | None = None,
) -> MetadataSyncResult:
    created = 0
    updated = 0
    skipped = 0
    with SessionLocal.begin() as session:
        owners = {user.username: user for user in ensure_fixed_accounts(session)}
        owner = owners["zhengyu"]
        tags = {item.name: item for item in session.scalars(select(Tag)).all()}
        tag_names = {
            str(value)
            for publication in publications
            for value in (publication.metadata.venue, publication.metadata.year)
        }
        for name in tag_names:
            tags.setdefault(name, Tag(name=name))
        for publication in publications:
            details = publication.metadata.model_dump(mode="json", exclude_none=True)
            asset = resolve_publication(session, title=publication.title, details=details)
            is_new = asset is None
            if asset is None:
                asset = Asset(
                    type=AssetType.LITERATURE,
                    slug=publication.slug,
                    owner=owner,
                    visibility=Visibility.LAB,
                )
                asset.versions.append(AssetVersion(version="published", is_current=True))
                session.add(asset)
                created += 1
            else:
                desired_tags = {
                    str(publication.metadata.venue),
                    str(publication.metadata.year),
                }
                changed = any(
                    (
                        asset.title != publication.title,
                        asset.type != AssetType.LITERATURE,
                        asset.summary != publication.summary,
                        asset.status != "published",
                        asset.visibility != Visibility.LAB,
                        asset.details != details,
                        {tag.name for tag in asset.tags} != desired_tags,
                    )
                )
                if changed:
                    updated += 1
                else:
                    skipped += 1
            asset.title = publication.title
            asset.type = AssetType.LITERATURE
            asset.summary = publication.summary
            asset.status = "published"
            asset.details = details
            asset.tags = [
                tags[str(publication.metadata.venue)],
                tags[str(publication.metadata.year)],
            ]
            if relocations and publication.slug in relocations:
                relocation = relocations[publication.slug]
                file_record = session.scalar(
                    select(FileRecord).where(
                        FileRecord.relative_path == relocation.source_relative_path
                    )
                )
                if file_record:
                    file_record.relative_path = relocation.destination_relative_path
                    file_record.asset = asset
            activity_exists = False
            if not is_new:
                activity_exists = bool(
                    session.scalar(
                        select(Activity.id).where(
                            Activity.asset_id == asset.id,
                            Activity.action == ActivityAction.IMPORTED_PUBLICATION,
                        )
                    )
                )
            if not activity_exists:
                session.add(
                    Activity(
                        asset=asset,
                        actor=owner,
                        action=ActivityAction.IMPORTED_PUBLICATION,
                        description=f"从 {publication.metadata.venue} 官方来源同步文献元数据",
                        created_at=datetime.now(UTC),
                    )
                )
    return MetadataSyncResult(created=created, updated=updated, skipped=skipped)


def load_imported_publications() -> list[PublicationRecord]:
    with SessionLocal() as session:
        assets = session.scalars(
            select(Asset)
            .join(Activity)
            .where(
                Asset.type.in_((AssetType.PAPER, AssetType.LITERATURE)),
                Activity.action == ActivityAction.IMPORTED_PUBLICATION,
            )
            .order_by(Asset.slug)
        ).unique()
        return [
            PublicationRecord(
                slug=asset.slug,
                title=asset.title,
                summary=asset.summary,
                metadata=PublicationMetadata.model_validate(asset.details),
            )
            for asset in assets
        ]


def write_manifest(publications: list[PublicationRecord], destination: Path) -> None:
    payload = [
        {
            "slug": publication.slug,
            "title": publication.title,
            "summary": publication.summary,
            "details": publication.metadata.model_dump(mode="json", exclude_none=True),
        }
        for publication in publications
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    destination.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="从官方学术来源同步外部文献元数据与 PDF。")
    parser.add_argument(
        "--venue",
        nargs="+",
        choices=SUPPORTED_VENUES,
        default=list(SUPPORTED_VENUES),
        help="需要同步的学术来源，可同时指定多个。",
    )
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--iclr-poster-id",
        action="append",
        default=None,
        help="ICLR 官方虚拟会议 poster ID；非 2026 年同步时必须提供。",
    )
    parser.add_argument(
        "--archive-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "sample-archive" / "real-fixtures",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--download-pdf",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="下载并校验正式 PDF（默认启用）。",
    )
    parser.add_argument("--skip-database", action="store_true")
    parser.add_argument(
        "--migrate-existing",
        action="store_true",
        help="将历史官方收录记录从论文目录迁入文献目录，不访问外部来源。",
    )
    arguments = parser.parse_args()

    if arguments.limit < 1:
        parser.error("--limit 必须大于 0。")
    if not 1900 <= arguments.year <= 2200:
        parser.error("--year 必须在 1900 到 2200 之间。")
    if arguments.skip_database and arguments.download_pdf:
        parser.error("--skip-database 只能与 --no-download-pdf 一起使用。")
    if arguments.migrate_existing and arguments.skip_database:
        parser.error("--migrate-existing 需要更新数据库，不能与 --skip-database 一起使用。")

    publications = (
        load_imported_publications()
        if arguments.migrate_existing
        else collect_papers(
            venues=tuple(arguments.venue),
            year=arguments.year,
            limit=arguments.limit,
            iclr_poster_ids=(
                tuple(arguments.iclr_poster_id) if arguments.iclr_poster_id else None
            ),
        )
    )
    archive_root = arguments.archive_root.resolve()
    if arguments.manifest:
        write_manifest(publications, arguments.manifest)
    download_result = PdfDownloadResult(downloaded=0, skipped=0, failures=())
    sync_result = None
    if not arguments.skip_database:
        sync_result = migrate_publications(publications, archive_root)
    if arguments.download_pdf:
        download_result = download_papers(publications, archive_root)
    venue_counts = {
        venue: sum(
            publication.metadata.venue == VENUE_LABELS[venue]
            for publication in publications
        )
        for venue in arguments.venue
    }
    summary = (
        f"历史官方收录文献 {len(publications)} 篇"
        if arguments.migrate_existing
        else "，".join(
            f"{VENUE_LABELS[venue]} {count} 篇" for venue, count in venue_counts.items()
        )
    )
    print(f"同步完成：{summary}。")
    if sync_result:
        print(
            "数据库："
            f"新增 {sync_result.created}，更新 {sync_result.updated}，"
            f"跳过 {sync_result.skipped}。"
        )
    if arguments.download_pdf:
        print(
            "PDF："
            f"新增下载 {download_result.downloaded}，"
            f"已验证跳过 {download_result.skipped}，"
            f"失败 {len(download_result.failures)}。"
        )
    if download_result.failures:
        raise RuntimeError("以下文献下载失败：\n" + "\n".join(download_result.failures))


if __name__ == "__main__":
    main()
