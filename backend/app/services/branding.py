from __future__ import annotations

import hashlib
import warnings
from datetime import UTC, datetime
from io import BytesIO

from PIL import Image, UnidentifiedImageError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.activity import ActivityAction
from app.domain.models import InstanceBranding, User
from app.domain.schemas import InstanceBrandingResponse, InstanceBrandingUpdateRequest
from app.services.activities import record_activity

DEFAULT_BRANDING = {
    "product_name": "SAGE",
    "product_subtitle": "RESEARCH ARCHIVE",
    "organization_name": "SAGE Lab",
    "slogan": "科学 · 数据 · 成长 · 卓越",
    "slogan_secondary": "Science · Archive · Growth · Excellence",
    "primary_color": "#2E7351",
}
LOGO_FORMAT_MIME_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}
MAX_LOGO_BYTES = 1_000_000
MAX_LOGO_SIDE = 4096
MAX_LOGO_PIXELS = 4_194_304


class BrandingLogoError(ValueError):
    pass


class BrandingConflictError(ValueError):
    pass


def lock_branding_mutations(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": 1_396_786_757},
        )


def get_branding_record(
    session: Session,
    *,
    lock: bool = False,
) -> InstanceBranding | None:
    return session.get(InstanceBranding, 1, with_for_update=lock)


def branding_revision(record: InstanceBranding | None) -> str:
    if record is None:
        return "default"
    updated_at = record.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at.astimezone(UTC).isoformat()


def require_branding_revision(record: InstanceBranding | None, expected_revision: str) -> None:
    if expected_revision != branding_revision(record):
        raise BrandingConflictError("品牌设置已被其他管理员更新，请刷新后重试。")


def branding_response(session: Session) -> InstanceBrandingResponse:
    record = get_branding_record(session)
    values = DEFAULT_BRANDING if record is None else {
        key: getattr(record, key) for key in DEFAULT_BRANDING
    }
    logo_url = None
    if record and record.logo_data:
        content_digest = hashlib.sha256(record.logo_data).hexdigest()
        logo_url = f"/api/settings/branding/logo/{content_digest}"
    return InstanceBrandingResponse(
        **values,
        logo_url=logo_url,
        revision=branding_revision(record),
    )


def update_branding(
    session: Session,
    payload: InstanceBrandingUpdateRequest,
    *,
    actor: User,
) -> InstanceBrandingResponse:
    lock_branding_mutations(session)
    record = get_branding_record(session, lock=True)
    require_branding_revision(record, payload.expected_revision)
    next_values = payload.model_dump()
    next_values.pop("expected_revision")
    current_values = (
        DEFAULT_BRANDING
        if record is None
        else {key: getattr(record, key) for key in DEFAULT_BRANDING}
    )
    if next_values == current_values:
        return branding_response(session)
    if record is None:
        record = InstanceBranding(id=1, **DEFAULT_BRANDING)
        session.add(record)
    for field, value in next_values.items():
        setattr(record, field, value)
    record.updated_at = datetime.now(UTC)
    record_activity(
        session,
        actor=actor,
        action=ActivityAction.UPDATED_BRANDING,
        description="更新了系统品牌设置",
    )
    session.flush()
    return branding_response(session)


def update_branding_logo(
    session: Session,
    content: bytes,
    mime_type: str,
    expected_revision: str,
    *,
    actor: User,
) -> InstanceBrandingResponse:
    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    if not content or len(content) > MAX_LOGO_BYTES:
        raise BrandingLogoError("Logo 文件必须小于 1 MB。")
    if normalized_mime not in LOGO_FORMAT_MIME_TYPES.values():
        raise BrandingLogoError("仅支持有效的 PNG、JPEG 或 WebP 图片。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                actual_mime = LOGO_FORMAT_MIME_TYPES.get(image.format or "")
                width, height = image.size
                if actual_mime != normalized_mime:
                    raise BrandingLogoError("Logo 文件内容与声明的图片格式不一致。")
                if getattr(image, "n_frames", 1) != 1:
                    raise BrandingLogoError("Logo 仅支持静态 PNG、JPEG 或 WebP 图片。")
                if (
                    width > MAX_LOGO_SIDE
                    or height > MAX_LOGO_SIDE
                    or width * height > MAX_LOGO_PIXELS
                ):
                    raise BrandingLogoError("Logo 尺寸不能超过 4096 像素或 419 万总像素。")
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
    except BrandingLogoError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise BrandingLogoError("仅支持可完整解码的静态 PNG、JPEG 或 WebP 图片。") from None

    lock_branding_mutations(session)
    record = get_branding_record(session, lock=True)
    require_branding_revision(record, expected_revision)
    if (
        record is not None
        and record.logo_data == content
        and record.logo_mime_type == normalized_mime
    ):
        return branding_response(session)
    if record is None:
        record = InstanceBranding(id=1, **DEFAULT_BRANDING)
        session.add(record)
    record.logo_data = content
    record.logo_mime_type = normalized_mime
    record.updated_at = datetime.now(UTC)
    record_activity(
        session,
        actor=actor,
        action=ActivityAction.UPDATED_BRANDING,
        description="更新了系统 Logo",
    )
    session.flush()
    return branding_response(session)


def remove_branding_logo(
    session: Session,
    expected_revision: str,
    *,
    actor: User,
) -> InstanceBrandingResponse:
    lock_branding_mutations(session)
    record = get_branding_record(session, lock=True)
    require_branding_revision(record, expected_revision)
    if record is None or (
        record.logo_data is None and record.logo_mime_type is None
    ):
        return branding_response(session)
    record.logo_data = None
    record.logo_mime_type = None
    record.updated_at = datetime.now(UTC)
    record_activity(
        session,
        actor=actor,
        action=ActivityAction.UPDATED_BRANDING,
        description="恢复了默认系统标志",
    )
    session.flush()
    return branding_response(session)
