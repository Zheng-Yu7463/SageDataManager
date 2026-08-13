from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import AssetType
from app.domain.models import Asset


class PublicationIdentityConflictError(Exception):
    pass


PUBLICATION_ASSET_TYPES = (AssetType.PAPER, AssetType.LITERATURE)


@dataclass(frozen=True)
class PublicationIdentity:
    doi: str
    source_id: str
    title_and_first_author: tuple[str, str]


def normalize_identity_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character.casefold() for character in decomposed if character.isalnum())


def _normalize_doi(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    normalized = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", normalized)
    return normalized


def publication_identity(title: str, details: dict) -> PublicationIdentity:
    authors = details.get("authors") or []
    first_author = str(authors[0]) if authors else ""
    return PublicationIdentity(
        doi=_normalize_doi(details.get("doi")),
        source_id=str(details.get("source_id", "")).strip().casefold(),
        title_and_first_author=(
            normalize_identity_text(title),
            normalize_identity_text(first_author),
        ),
    )


def publication_identities_match(
    first: PublicationIdentity, second: PublicationIdentity
) -> bool:
    return bool(
        (first.doi and first.doi == second.doi)
        or (first.source_id and first.source_id == second.source_id)
        or (
            all(first.title_and_first_author)
            and first.title_and_first_author == second.title_and_first_author
        )
    )


def matching_publications(
    session: Session,
    *,
    title: str,
    details: dict,
    exclude_asset_id: UUID | None = None,
) -> list[Asset]:
    identity = publication_identity(title, details)
    statement = select(Asset).where(Asset.type.in_(PUBLICATION_ASSET_TYPES))
    if exclude_asset_id:
        statement = statement.where(Asset.id != exclude_asset_id)
    return [
        asset
        for asset in session.scalars(statement)
        if publication_identities_match(
            identity, publication_identity(asset.title, asset.details)
        )
    ]


def resolve_publication(
    session: Session,
    *,
    title: str,
    details: dict,
    exclude_asset_id: UUID | None = None,
) -> Asset | None:
    matches = matching_publications(
        session,
        title=title,
        details=details,
        exclude_asset_id=exclude_asset_id,
    )
    if len(matches) > 1:
        raise PublicationIdentityConflictError(
            "出版物 DOI、官方来源 ID 或题名与首位作者指向了不同的已有记录。"
        )
    return matches[0] if matches else None
