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


class ActivitySummary(BaseModel):
    id: UUID
    asset_id: UUID | None
    asset_title: str | None
    asset_type: AssetType | None
    actor_name: str | None
    action: str
    description: str
    created_at: datetime


class DashboardSummary(BaseModel):
    counts: dict[AssetType, int]
    total_storage_bytes: int
    healthy_files: int
    missing_files: int
    recent_assets: list[AssetSummary]
    recent_activities: list[ActivitySummary]
    popular_tags: list[tuple[str, int]]
