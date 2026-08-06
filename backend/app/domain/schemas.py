from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import AssetType, HealthStatus, Visibility


class OwnerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    avatar_url: str | None = None


class FileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    file_kind: str
    mime_type: str | None
    file_size: int
    health_status: HealthStatus


class AssetVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: str
    release_notes: str
    is_current: bool
    created_at: datetime


class AssetSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: AssetType
    slug: str
    title: str
    summary: str
    status: str
    visibility: Visibility
    owner: OwnerSummary
    details: dict[str, Any]
    tags: list[str] = Field(default_factory=list)
    current_version: str | None = None
    total_size: int = 0
    updated_at: datetime


class AssetListResponse(BaseModel):
    items: list[AssetSummary]
    total: int
    page: int
    page_size: int


class RelatedAssetSummary(BaseModel):
    id: UUID
    type: AssetType
    slug: str
    title: str
    relation_type: str


class ActivitySummary(BaseModel):
    id: UUID
    asset_id: UUID | None
    asset_title: str | None
    asset_type: AssetType | None
    actor_name: str | None
    action: str
    description: str
    created_at: datetime


class AssetDetail(AssetSummary):
    versions: list[AssetVersionSummary] = Field(default_factory=list)
    files: list[FileSummary] = Field(default_factory=list)
    related_assets: list[RelatedAssetSummary] = Field(default_factory=list)
    recent_activities: list[ActivitySummary] = Field(default_factory=list)


class ScanRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    source: str
    files_discovered: int
    files_indexed: int
    files_missing: int
    files_unclaimed: int
    files_skipped: int
    message: str | None
    started_at: datetime
    completed_at: datetime | None


class ArchiveHealthSummary(BaseModel):
    storage_available: bool
    latest_scan: ScanRunSummary | None
    recent_scans: list[ScanRunSummary]
    indexed_files: int
    healthy_files: int
    missing_files: int
    unclaimed_files: int = 0


class DashboardSummary(BaseModel):
    counts: dict[AssetType, int]
    total_storage_bytes: int
    healthy_files: int
    missing_files: int
    recent_assets: list[AssetSummary]
    recent_activities: list[ActivitySummary]
    popular_tags: list[tuple[str, int]]


class UnclaimedFileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    relative_path: str
    file_name: str
    file_kind: str
    mime_type: str | None
    file_size: int
    modified_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime


class ClaimUnclaimedFileRequest(BaseModel):
    asset_id: UUID


class FileClaimResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_id: UUID
    file: FileSummary


class AssetCreateRequest(BaseModel):
    type: AssetType
    slug: str = Field(min_length=3, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(default="", max_length=5000)
    status: str = Field(default="draft", min_length=1, max_length=40)
    visibility: Visibility = Visibility.LAB
    version: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=20)
    details: dict[str, Any] = Field(default_factory=dict)
    owner_name: str | None = Field(default=None, min_length=1, max_length=80)
    owner_email: str | None = Field(default=None, min_length=3, max_length=255)
