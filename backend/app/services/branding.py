from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.domain.models import Activity, InstanceBranding, User
from app.domain.schemas import InstanceBrandingResponse, InstanceBrandingUpdateRequest

DEFAULT_BRANDING = {
    "product_name": "SAGE",
    "product_subtitle": "RESEARCH ARCHIVE",
    "organization_name": "SAGE Lab",
    "slogan": "科学 · 归档 · 成长 · 演进",
    "slogan_secondary": "Science · Archive · Growth · Evolution",
    "primary_color": "#2E7351",
}
LOGO_MIME_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}
MAX_LOGO_BYTES = 1_000_000


class BrandingLogoError(ValueError):
    pass


def get_branding_record(session: Session) -> InstanceBranding | None:
    return session.get(InstanceBranding, 1)


def branding_response(session: Session) -> InstanceBrandingResponse:
    record = get_branding_record(session)
    values = DEFAULT_BRANDING if record is None else {
        key: getattr(record, key) for key in DEFAULT_BRANDING
    }
    logo_url = None
    if record and record.logo_data:
        updated_at = record.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        version = int(updated_at.timestamp())
        logo_url = f"/api/settings/branding/logo?v={version}"
    return InstanceBrandingResponse(**values, logo_url=logo_url)


def update_branding(
    session: Session,
    payload: InstanceBrandingUpdateRequest,
    *,
    actor: User,
) -> InstanceBrandingResponse:
    record = get_branding_record(session)
    if record is None:
        record = InstanceBranding(id=1, **DEFAULT_BRANDING)
        session.add(record)
    for field, value in payload.model_dump().items():
        setattr(record, field, value)
    record.updated_at = datetime.now(UTC)
    session.add(
        Activity(actor=actor, action="updated_branding", description="更新了系统品牌设置")
    )
    session.flush()
    return branding_response(session)


def update_branding_logo(
    session: Session,
    content: bytes,
    mime_type: str,
    *,
    actor: User,
) -> InstanceBrandingResponse:
    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    signatures = LOGO_MIME_SIGNATURES.get(normalized_mime)
    valid_webp = normalized_mime != "image/webp" or (
        len(content) >= 12 and content[8:12] == b"WEBP"
    )
    if not content or len(content) > MAX_LOGO_BYTES:
        raise BrandingLogoError("Logo 文件必须小于 1 MB。")
    has_valid_signature = signatures and any(
        content.startswith(signature) for signature in signatures
    )
    if not has_valid_signature or not valid_webp:
        raise BrandingLogoError("仅支持有效的 PNG、JPEG 或 WebP 图片。")

    record = get_branding_record(session)
    if record is None:
        record = InstanceBranding(id=1, **DEFAULT_BRANDING)
        session.add(record)
    record.logo_data = content
    record.logo_mime_type = normalized_mime
    record.updated_at = datetime.now(UTC)
    session.add(Activity(actor=actor, action="updated_branding", description="更新了系统 Logo"))
    session.flush()
    return branding_response(session)


def remove_branding_logo(session: Session, *, actor: User) -> InstanceBrandingResponse:
    record = get_branding_record(session)
    if record is None:
        record = InstanceBranding(id=1, **DEFAULT_BRANDING)
        session.add(record)
    record.logo_data = None
    record.logo_mime_type = None
    record.updated_at = datetime.now(UTC)
    session.add(Activity(actor=actor, action="updated_branding", description="恢复了默认系统标志"))
    session.flush()
    return branding_response(session)
