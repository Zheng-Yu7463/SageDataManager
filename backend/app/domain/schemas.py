from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import AssetType, HealthStatus, Visibility


class OwnerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    avatar_url: str | None = None


class FileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    relative_path: str
    file_name: str
    file_kind: str
    mime_type: str | None
    file_size: int
    health_status: HealthStatus
    modified_at: datetime | None = None


class AssetVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: str
    release_notes: str
    is_current: bool
    created_at: datetime


class UploadDirectoryOption(BaseModel):
    name: str
    label: str


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
    file_count: int = 0
    upload_directories: list[UploadDirectoryOption] = Field(default_factory=list)
    default_upload_directory: str
    updated_at: datetime


class PublicationCatalogueFacets(BaseModel):
    venues: list[str] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)


class AssetListResponse(BaseModel):
    items: list[AssetSummary]
    total: int
    page: int
    page_size: int
    publication_facets: PublicationCatalogueFacets | None = None


class AssetChoiceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: AssetType
    slug: str
    title: str


class RelatedAssetSummary(BaseModel):
    relation_id: UUID
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
    credential_name: str | None = None
    action: str
    action_label: str
    description: str
    created_at: datetime
    occurrence_count: int = 1


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


class ActivityFacet(BaseModel):
    value: str
    label: str
    count: int


class ActivityListResponse(BaseModel):
    items: list[ActivitySummary]
    facets: list[ActivityFacet]
    total: int
    page: int
    page_size: int


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


class PublicationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue: str = Field(min_length=2, max_length=80)
    year: int = Field(ge=1900, le=2200)
    track: str = Field(min_length=2, max_length=120)
    authors: list[str] = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=2, max_length=200)
    source_url: AnyHttpUrl
    publication_url: AnyHttpUrl | None = None
    pdf_url: AnyHttpUrl
    abstract: str | None = Field(default=None, max_length=20_000)
    doi: str | None = Field(default=None, max_length=300)
    published_at: str | None = Field(default=None, max_length=40)
    citation_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z][A-Za-z0-9_:+.-]*$",
    )
    entry_type: Literal["article", "inproceedings", "misc", "proceedings"] = "inproceedings"
    booktitle: str | None = Field(default=None, max_length=500)
    journal: str | None = Field(default=None, max_length=500)
    pages: str | None = Field(default=None, max_length=80)
    publisher: str | None = Field(default=None, max_length=300)
    month: str | None = Field(default=None, max_length=40)
    volume: str | None = Field(default=None, max_length=80)
    issue: str | None = Field(default=None, max_length=80)

    @field_validator("venue", "track", "source_id")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("出版物元数据字段不能为空。")
        return normalized

    @field_validator("authors")
    @classmethod
    def normalize_authors(cls, authors: list[str]) -> list[str]:
        normalized = [author.strip() for author in authors if author.strip()]
        if not normalized:
            raise ValueError("出版物至少需要一位作者。")
        return normalized

    @field_validator("doi")
    @classmethod
    def normalize_doi(cls, doi: str | None) -> str | None:
        if doi is None:
            return None
        normalized = doi.removeprefix("https://doi.org/").strip().lower()
        return normalized or None

    @field_validator(
        "citation_key",
        "booktitle",
        "journal",
        "pages",
        "publisher",
        "month",
        "volume",
        "issue",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class PublicationCitationResponse(BaseModel):
    citation_key: str
    filename: str
    bibtex: str


class PublicationCitationExportResponse(BaseModel):
    count: int
    filename: str
    bibtex: str


def normalized_asset_details(asset_type: AssetType, details: dict[str, Any]) -> dict[str, Any]:
    if asset_type == AssetType.LITERATURE and not details.get("source_id"):
        return details
    if asset_type not in {AssetType.PAPER, AssetType.LITERATURE}:
        return details
    return PublicationMetadata.model_validate(details).model_dump(mode="json", exclude_none=True)


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

    @model_validator(mode="after")
    def validate_details_for_asset_type(self) -> "AssetCreateRequest":
        self.details = normalized_asset_details(self.type, self.details)
        return self


class AssetUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    summary: str | None = Field(default=None, max_length=5000)
    status: str | None = Field(default=None, min_length=1, max_length=40)
    visibility: Visibility | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    details: dict[str, Any] | None = None


class BatchAssetImportRequest(BaseModel):
    assets: list[AssetCreateRequest] = Field(min_length=1, max_length=100)


class BatchAssetImportResponse(BaseModel):
    created: list[AssetSummary]


class AssetYamlImportRequest(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)


class AssetRelationCreateRequest(BaseModel):
    target_asset_id: UUID
    relation_type: str = Field(min_length=1, max_length=60)


class AssetVersionCreateRequest(BaseModel):
    version: str = Field(min_length=1, max_length=80)
    release_notes: str = Field(default="", max_length=5000)
    make_current: bool = True


class UploadCommandRequest(BaseModel):
    asset_id: UUID
    source_path: str = Field(min_length=1, max_length=4000)
    target_subdirectory: str = Field(default="incoming", min_length=1, max_length=400)
    recursive: bool = False


class UploadCommandResponse(BaseModel):
    upload_id: UUID
    asset_id: UUID
    asset_title: str
    archive_relative_path: str
    staging_relative_path: str
    upload_token: str
    expires_at: datetime
    command: str


class UploadFinalizeRequest(BaseModel):
    upload_token: str = Field(min_length=1, max_length=4000)


class UploadFinalizeResponse(BaseModel):
    asset_id: UUID
    imported_file_count: int
    total_size: int
    relative_paths: list[str]


AgentScope = Literal[
    "assets:read",
    "metadata:write",
    "files:upload",
    "archive:finalize",
    "citations:export",
]


class AccessTokenCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    scopes: list[AgentScope] = Field(min_length=1, max_length=5)
    expires_in_days: int = Field(default=90, ge=1, le=365)

    @field_validator("name", mode="before")
    @classmethod
    def strip_token_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value: list[AgentScope]) -> list[AgentScope]:
        return list(dict.fromkeys(value))


class AccessTokenSummary(BaseModel):
    id: UUID
    name: str
    token_prefix: str
    scopes: list[AgentScope]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class AccessTokenCreatedResponse(AccessTokenSummary):
    token: str


class AgentIdentityResponse(BaseModel):
    username: str
    account_name: str
    credential_name: str
    scopes: list[AgentScope]
    expires_at: datetime


class AgentUploadCreateRequest(BaseModel):
    asset_id: UUID
    target_subdirectory: str = Field(min_length=1, max_length=400)


class AgentUploadCreateResponse(BaseModel):
    upload_id: UUID
    asset_id: UUID
    asset_title: str
    archive_relative_path: str
    upload_token: str
    expires_at: datetime
    file_upload_url_template: str
    finalize_url: str


class AgentUploadedFileResponse(BaseModel):
    upload_id: UUID
    relative_path: str
    file_size: int


class FileAccessTicketRequest(BaseModel):
    mode: Literal["download", "preview"]


class FileAccessTicketResponse(BaseModel):
    content_url: str
    expires_at: datetime


class AccountSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    name: str
    email: str
    role: str
    upload_username: str
    is_active: bool


class AccountCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+$")
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=255)


class AccountUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    is_active: bool | None = None


class AccountLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+$")

    password: str = Field(min_length=1, max_length=256)


class RegistrationStatus(BaseModel):
    enabled: bool


class AccountLoginResponse(AccountSummary):
    session_token: str


class InstanceBrandingResponse(BaseModel):
    product_name: str
    product_subtitle: str
    organization_name: str
    slogan: str
    slogan_secondary: str
    primary_color: str
    logo_url: str | None


class InstanceBrandingUpdateRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=80)
    product_subtitle: str = Field(min_length=1, max_length=120)
    organization_name: str = Field(min_length=1, max_length=120)
    slogan: str = Field(min_length=1, max_length=160)
    slogan_secondary: str = Field(min_length=1, max_length=160)
    primary_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator(
        "product_name",
        "product_subtitle",
        "organization_name",
        "slogan",
        "slogan_secondary",
    )
    @classmethod
    def strip_brand_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("primary_color")
    @classmethod
    def require_accessible_primary_color(cls, value: str) -> str:
        color = value.upper()
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
        if 1.05 / (luminance + 0.05) < 3:
            raise ValueError("品牌主色与白色文字的对比度不足。")
        return color
