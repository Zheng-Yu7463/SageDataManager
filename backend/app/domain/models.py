from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import AssetType, HealthStatus, Visibility

asset_tags = Table(
    "asset_tags",
    Base.metadata,
    Column(
        "asset_id",
        PGUUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id", PGUUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    ),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(30), default="admin", nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    type: Mapped[AssetType] = mapped_column(Enum(AssetType, name="asset_type"), index=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), index=True)
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, name="asset_visibility"), default=Visibility.LAB
    )
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[User] = relationship()
    versions: Mapped[list[AssetVersion]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="AssetVersion.created_at.desc()",
    )
    files: Mapped[list[FileRecord]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(secondary=asset_tags, back_populates="assets")


class AssetVersion(Base):
    __tablename__ = "asset_versions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(80))
    release_notes: Mapped[str] = mapped_column(Text, default="")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    asset: Mapped[Asset] = relationship(back_populates="versions")


class FileRecord(Base):
    __tablename__ = "asset_files"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="SET NULL")
    )
    relative_path: Mapped[str] = mapped_column(String(1000))
    file_name: Mapped[str] = mapped_column(String(500), index=True)
    file_kind: Mapped[str] = mapped_column(String(80))
    mime_type: Mapped[str | None] = mapped_column(String(160))
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    checksum: Mapped[str | None] = mapped_column(String(128))
    health_status: Mapped[HealthStatus] = mapped_column(
        Enum(HealthStatus, name="file_health_status"), default=HealthStatus.UNVERIFIED
    )
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    asset: Mapped[Asset] = relationship(back_populates="files")


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    source: Mapped[str] = mapped_column(String(80), default="storage-root")
    files_discovered: Mapped[int] = mapped_column(BigInteger, default=0)
    files_indexed: Mapped[int] = mapped_column(BigInteger, default=0)
    files_missing: Mapped[int] = mapped_column(BigInteger, default=0)
    files_unclaimed: Mapped[int] = mapped_column(BigInteger, default=0)
    files_skipped: Mapped[int] = mapped_column(BigInteger, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    assets: Mapped[list[Asset]] = relationship(secondary=asset_tags, back_populates="tags")


class AssetRelation(Base):
    __tablename__ = "asset_relations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    target_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(60), index=True)


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True
    )

    asset: Mapped[Asset | None] = relationship()
    actor: Mapped[User | None] = relationship()


class UnclaimedFile(Base):
    __tablename__ = "unclaimed_files"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    relative_path: Mapped[str] = mapped_column(String(1000), unique=True, index=True)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(160))
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    claimed_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), index=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
