from __future__ import annotations

import hashlib
import warnings
from datetime import UTC, datetime
from io import BytesIO

from PIL import Image, UnidentifiedImageError
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


def get_branding_record(session: Session) -> InstanceBranding | None:
    return session.get(InstanceBranding, 1)


def branding_response(session: Session) -> InstanceBrandingResponse:
    record = get_branding_record(session)
    values = DEFAULT_BRANDING if record is None else {
        key: getattr(record, key) for key in DEFAULT_BRANDING
    }
    logo_url = None
    if record and record.logo_data:
        content_digest = hashlib.sha256(record.logo_data).hexdigest()
        logo_url = f"/api/settings/branding/logo/{content_digest}"
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

    record = get_branding_record(session)
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


def remove_branding_logo(session: Session, *, actor: User) -> InstanceBrandingResponse:
    record = get_branding_record(session)
    if record is None:
        record = InstanceBranding(id=1, **DEFAULT_BRANDING)
        session.add(record)
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
