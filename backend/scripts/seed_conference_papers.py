from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin

from sqlalchemy import select

from app.db.session import SessionLocal
from app.domain.activity import ActivityAction
from app.domain.enums import AssetType, Visibility
from app.domain.models import Activity, Asset, AssetVersion, Tag
from app.domain.schemas import PaperMetadata
from app.services.accounts import ensure_fixed_accounts

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
USER_AGENT = "SageDataManager/0.1 (conference fixture importer)"
ICLR_PROCEEDINGS_ROOT = "https://proceedings.iclr.cc"
SUPPORTED_VENUES = ("ACL", "ICLR")


@dataclass(frozen=True)
class ConferencePaper:
    slug: str
    title: str
    summary: str
    metadata: PaperMetadata


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


def normalized_identity(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character.casefold() for character in decomposed if character.isalnum())


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
        and normalized_identity(link_text) == normalized_identity(title)
    ]
    if len(candidates) != 1:
        raise ValueError(f"ICLR proceedings 未找到唯一同题名论文：{title}")

    publication_url = candidates[0]
    publication_parser = PaperPageParser()
    publication_parser.feed(fetch(publication_url).decode("utf-8"))
    publication_title = publication_parser.meta.get("citation_title", [""])[0]
    publication_authors = publication_parser.meta.get("citation_author", [])
    if normalized_identity(publication_title) != normalized_identity(title):
        raise ValueError(f"ICLR proceedings 题名校验失败：{title}")
    if not publication_authors or normalized_identity(
        citation_author_name(publication_authors[0])
    ) != normalized_identity(first_author):
        raise ValueError(f"ICLR proceedings 首位作者校验失败：{title}")
    return publication_url, publication_parser


def citation_key(venue: str, year: int, source_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9]+", "", source_id)
    return f"{venue.lower()}{year}{suffix.removeprefix(str(year))}"


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


def parse_iclr_paper(poster_id: str, year: int = 2026) -> ConferencePaper:
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
    publication_url, publication_parser = find_iclr_proceedings_page(
        title, virtual_authors[0]
    )
    publication_authors = [
        citation_author_name(author)
        for author in publication_parser.meta["citation_author"]
    ]
    metadata = PaperMetadata(
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
        booktitle=first_meta(
            publication_parser, "citation_conference_title", "citation_book_title"
        )
        or f"Proceedings of the International Conference on Learning Representations {year}",
        publisher=first_meta(publication_parser, "citation_publisher"),
    )
    return ConferencePaper(
        slug=f"iclr-{year}-{poster_id}", title=title, summary=parser.abstract, metadata=metadata
    )


def parse_acl_paper(anthology_id: str) -> ConferencePaper:
    source_url = f"https://aclanthology.org/{anthology_id}/"
    parser = PaperPageParser()
    parser.feed(fetch(source_url).decode("utf-8"))
    title = parser.meta["citation_title"][0]
    year = int(anthology_id.split(".", 1)[0])
    metadata = PaperMetadata(
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
    return ConferencePaper(
        slug=anthology_id.replace(".", "-"), title=title, summary=parser.abstract, metadata=metadata
    )


def collect_papers(
    *,
    venues: tuple[str, ...] = SUPPORTED_VENUES,
    year: int = 2026,
    limit: int = 10,
    iclr_poster_ids: tuple[str, ...] | None = None,
) -> list[ConferencePaper]:
    papers: list[ConferencePaper] = []
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
        papers.extend(
            parse_acl_paper(f"{year}.acl-long.{index}") for index in range(1, limit + 1)
        )
    return papers


def validate_pdf(path: Path) -> None:
    if not path.read_bytes().startswith(b"%PDF"):
        raise ValueError(f"下载内容不是 PDF：{path}")
    result = subprocess.run(
        ["pdfinfo", str(path)], capture_output=True, check=False, text=True
    )
    if result.returncode != 0 or not re.search(r"^Pages:\s+[1-9]\d*$", result.stdout, re.MULTILINE):
        raise ValueError(f"PDF 结构校验失败：{path}")


def download_papers(papers: list[ConferencePaper], archive_root: Path) -> list[str]:
    failures: list[str] = []
    for index, paper in enumerate(papers, start=1):
        destination = archive_root / "paper" / paper.slug / "manuscript" / "paper.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".download")
        try:
            content = fetch(str(paper.metadata.pdf_url))
            temporary.write_bytes(content)
            validate_pdf(temporary)
            temporary.replace(destination)
            checksum = hashlib.sha256(content).hexdigest()[:12]
            print(
                f"[{index:02d}/{len(papers)}] "
                f"{paper.metadata.venue} {destination.name} {checksum}"
            )
        except (OSError, RuntimeError, ValueError) as error:
            temporary.unlink(missing_ok=True)
            failures.append(f"{paper.metadata.source_id}: {error}")
    return failures


def upsert_metadata(papers: list[ConferencePaper]) -> None:
    with SessionLocal.begin() as session:
        owners = {user.username: user for user in ensure_fixed_accounts(session)}
        owner = owners["zhengyu"]
        tags = {item.name: item for item in session.scalars(select(Tag)).all()}
        tag_names = {
            str(value)
            for paper in papers
            for value in (paper.metadata.venue, paper.metadata.year)
        }
        for name in tag_names:
            tags.setdefault(name, Tag(name=name))
        existing = {
            asset.details.get("source_id"): asset
            for asset in session.scalars(select(Asset).where(Asset.type == AssetType.PAPER))
            if asset.details.get("source_id")
        }
        for paper in papers:
            details = paper.metadata.model_dump(mode="json", exclude_none=True)
            asset = existing.get(paper.metadata.source_id)
            is_new = asset is None
            if asset is None:
                asset = Asset(
                    type=AssetType.PAPER,
                    slug=paper.slug,
                    owner=owner,
                    visibility=Visibility.LAB,
                )
                asset.versions.append(AssetVersion(version="published", is_current=True))
                session.add(asset)
            asset.title = paper.title
            asset.summary = paper.summary
            asset.status = "published"
            asset.details = details
            asset.tags = [
                tags[str(paper.metadata.venue)],
                tags[str(paper.metadata.year)],
            ]
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
                        description=f"从 {paper.metadata.venue} 官方来源同步论文元数据",
                        created_at=datetime.now(UTC),
                    )
                )


def write_manifest(papers: list[ConferencePaper], destination: Path) -> None:
    payload = [
        {
            "slug": paper.slug,
            "title": paper.title,
            "summary": paper.summary,
            "details": paper.metadata.model_dump(mode="json", exclude_none=True),
        }
        for paper in papers
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    destination.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="从官方来源同步会议论文元数据与 PDF。")
    parser.add_argument(
        "--venue",
        nargs="+",
        choices=SUPPORTED_VENUES,
        default=list(SUPPORTED_VENUES),
        help="需要同步的会议，可同时指定多个。",
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
    arguments = parser.parse_args()

    if arguments.limit < 1:
        parser.error("--limit 必须大于 0。")
    if not 1900 <= arguments.year <= 2200:
        parser.error("--year 必须在 1900 到 2200 之间。")
    papers = collect_papers(
        venues=tuple(arguments.venue),
        year=arguments.year,
        limit=arguments.limit,
        iclr_poster_ids=(
            tuple(arguments.iclr_poster_id) if arguments.iclr_poster_id else None
        ),
    )
    if arguments.manifest:
        write_manifest(papers, arguments.manifest)
    failures = []
    if arguments.download_pdf:
        failures = download_papers(papers, arguments.archive_root.resolve())
    if not arguments.skip_database:
        upsert_metadata(papers)
    venue_counts = {
        venue: sum(paper.metadata.venue == venue for paper in papers)
        for venue in arguments.venue
    }
    summary = "，".join(f"{venue} {count} 篇" for venue, count in venue_counts.items())
    print(f"同步完成：{summary}。")
    if failures:
        raise RuntimeError("以下论文下载失败：\n" + "\n".join(failures))


if __name__ == "__main__":
    main()
