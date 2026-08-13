from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.schemas import AssetSummary, PaperCitationExportResponse, PaperCitationResponse


class PaperCitationError(Exception):
    pass


def _clean(value: object) -> str:
    return " ".join(str(value).split())


def _bibtex_value(value: object) -> str:
    return _clean(value).replace("%", r"\%").replace("&", r"\&").replace("#", r"\#")


def _citation_key(asset: AssetSummary) -> str:
    configured = _clean(asset.details.get("citation_key", ""))
    if configured:
        return configured
    key = re.sub(r"[^A-Za-z0-9_:+.-]+", "-", asset.slug).strip("-.")
    if not key or not key[0].isalpha():
        key = f"paper-{key}"
    return key


def _citation_fields(asset: AssetSummary) -> list[tuple[str, str]]:
    details = asset.details
    authors = details.get("authors")
    if not isinstance(authors, list) or not authors:
        raise PaperCitationError("论文缺少作者，无法生成 BibTeX。")

    entry_type = _clean(details.get("entry_type", "inproceedings"))
    fields = [
        ("title", asset.title),
        ("author", " and ".join(_clean(author) for author in authors)),
    ]
    if entry_type in {"inproceedings", "proceedings"}:
        booktitle = _clean(details.get("booktitle", ""))
        fields.append(
            (
                "booktitle",
                booktitle or f"Proceedings of {_clean(details['venue'])} {_clean(details['year'])}",
            )
        )
    elif entry_type == "article":
        journal = _clean(details.get("journal", ""))
        if not journal:
            raise PaperCitationError("期刊论文缺少期刊名称，无法生成 BibTeX。")
        fields.append(("journal", journal))
    fields.append(("year", _clean(details["year"])))
    optional_fields = (
        "pages",
        "publisher",
        "month",
        "volume",
        "doi",
    )
    fields.extend(
        (name, _clean(details[name])) for name in optional_fields if _clean(details.get(name, ""))
    )
    if _clean(details.get("issue", "")):
        fields.append(("number", _clean(details["issue"])))
    url = details.get("publication_url") or details.get("source_url")
    if url:
        fields.append(("url", _clean(url)))
    return fields


def build_paper_citation(asset: AssetSummary) -> PaperCitationResponse:
    if asset.type.value != "paper":
        raise PaperCitationError("只有论文资产可以生成 BibTeX。")
    citation_key = _citation_key(asset)
    entry_type = _clean(asset.details.get("entry_type", "inproceedings"))
    fields = _citation_fields(asset)
    lines = [f"@{entry_type}{{{citation_key},"]
    lines.extend(
        f"  {name} = {{{_bibtex_value(value)}}}{',' if index < len(fields) - 1 else ''}"
        for index, (name, value) in enumerate(fields)
    )
    lines.append("}")
    return PaperCitationResponse(
        citation_key=citation_key,
        filename=f"{citation_key}.bib",
        bibtex="\n".join(lines) + "\n",
    )


def build_paper_citation_export(
    assets: Iterable[AssetSummary], *, filename: str = "sage-papers.bib"
) -> PaperCitationExportResponse:
    citations = [build_paper_citation(asset) for asset in assets]
    keys = [citation.citation_key for citation in citations]
    duplicate_keys = sorted({key for key in keys if keys.count(key) > 1})
    if duplicate_keys:
        raise PaperCitationError(f"引用键重复：{', '.join(duplicate_keys)}。请先修正论文元数据。")
    return PaperCitationExportResponse(
        count=len(citations),
        filename=filename,
        bibtex="\n".join(citation.bibtex.rstrip() for citation in citations)
        + ("\n" if citations else ""),
    )
