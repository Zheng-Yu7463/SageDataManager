from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.domain.enums import AssetType
from app.domain.models import Asset, PublicationIdentityKey


class PublicationIdentityConflictError(Exception):
    pass


PUBLICATION_ASSET_TYPES = (AssetType.PAPER, AssetType.LITERATURE)


@dataclass(frozen=True)
class PublicationIdentity:
    doi: str
    source_id: str
    title_and_first_author: tuple[str, str]


@dataclass(frozen=True)
class PublicationIdentityDigest:
    kind: str
    digest: str


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


def publication_identity_digests(
    title: str, details: dict
) -> tuple[PublicationIdentityDigest, ...]:
    identity = publication_identity(title, details)
    values = (
        ("doi", identity.doi),
        ("source_id", identity.source_id),
        (
            "title_author",
            "\0".join(identity.title_and_first_author)
            if all(identity.title_and_first_author)
            else "",
        ),
    )
    return tuple(
        PublicationIdentityDigest(
            kind=kind,
            digest=sha256(value.encode("utf-8")).hexdigest(),
        )
        for kind, value in values
        if value
    )


def synchronize_publication_identity_keys(asset: Asset) -> None:
    if asset.type not in PUBLICATION_ASSET_TYPES or not asset.details.get("source_id"):
        asset.publication_identity_keys = []
        return
    desired = {
        identity.kind: identity.digest
        for identity in publication_identity_digests(asset.title, asset.details)
    }
    existing = {identity.kind: identity for identity in asset.publication_identity_keys}
    for kind, digest in desired.items():
        identity = existing.get(kind)
        if identity:
            identity.digest = digest
        else:
            asset.publication_identity_keys.append(
                PublicationIdentityKey(kind=kind, digest=digest)
            )
    asset.publication_identity_keys = [
        identity
        for identity in asset.publication_identity_keys
        if identity.kind in desired
    ]


def matching_publications(
    session: Session,
    *,
    title: str,
    details: dict,
    exclude_asset_id: UUID | None = None,
) -> list[Asset]:
    identities = publication_identity_digests(title, details)
    if not identities:
        return []
    indexed_statement = (
        select(Asset)
        .join(Asset.publication_identity_keys)
        .where(
            Asset.type.in_(PUBLICATION_ASSET_TYPES),
            or_(
                *(
                    and_(
                        PublicationIdentityKey.kind == identity.kind,
                        PublicationIdentityKey.digest == identity.digest,
                    )
                    for identity in identities
                )
            ),
        )
        .distinct()
    )
    if exclude_asset_id:
        indexed_statement = indexed_statement.where(Asset.id != exclude_asset_id)
    indexed_matches = list(session.scalars(indexed_statement))

    identity = publication_identity(title, details)
    legacy_statement = select(Asset).where(
        Asset.type.in_(PUBLICATION_ASSET_TYPES),
        ~Asset.publication_identity_keys.any(),
    )
    if exclude_asset_id:
        legacy_statement = legacy_statement.where(Asset.id != exclude_asset_id)
    legacy_matches = [
        asset
        for asset in session.scalars(legacy_statement)
        if publication_identities_match(
            identity, publication_identity(asset.title, asset.details)
        )
    ]
    return indexed_matches + legacy_matches


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
