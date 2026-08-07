export type AssetType = 'paper' | 'dataset' | 'literature' | 'project' | 'model'
export type Visibility = 'lab' | 'project' | 'restricted'

export interface OwnerSummary {
  id: string
  name: string
  avatar_url: string | null
}

export interface AssetSummary {
  id: string
  type: AssetType
  slug: string
  title: string
  summary: string
  status: string
  visibility: Visibility
  owner: OwnerSummary
  details: Record<string, unknown>
  tags: string[]
  current_version: string | null
  total_size: number
  updated_at: string
}

export interface AssetListResponse {
  items: AssetSummary[]
  total: number
  page: number
  page_size: number
}

export interface AssetCreateInput {
  type: AssetType
  slug: string
  title: string
  summary: string
  status: string
  visibility: Visibility
  version: string | null
  tags: string[]
  details: Record<string, unknown>
}

export interface AssetVersionSummary {
  id: string
  version: string
  release_notes: string
  is_current: boolean
  created_at: string
}

export interface FileSummary {
  id: string
  file_name: string
  file_kind: string
  mime_type: string | null
  file_size: number
  health_status: 'healthy' | 'missing' | 'unverified'
}

export interface RelatedAssetSummary {
  id: string
  type: AssetType
  slug: string
  title: string
  relation_type: string
}

export interface AssetDetail extends AssetSummary {
  versions: AssetVersionSummary[]
  files: FileSummary[]
  related_assets: RelatedAssetSummary[]
  recent_activities: ActivitySummary[]
}
export interface ActivitySummary {
  id: string
  asset_id: string | null
  asset_title: string | null
  asset_type: AssetType | null
  actor_name: string | null
  action: string
  description: string
  created_at: string
}

export interface DashboardSummary {
  counts: Record<AssetType, number>
  total_storage_bytes: number
  healthy_files: number
  missing_files: number
  recent_assets: AssetSummary[]
  recent_activities: ActivitySummary[]
  popular_tags: [string, number][]
}

export interface ScanRunSummary {
  id: string
  status: string
  source: string
  files_discovered: number
  files_indexed: number
  files_missing: number
  files_unclaimed: number
  files_skipped: number
  message: string | null
  started_at: string
  completed_at: string | null
}

export interface ArchiveHealthSummary {
  storage_available: boolean
  latest_scan: ScanRunSummary | null
  recent_scans: ScanRunSummary[]
  indexed_files: number
  healthy_files: number
  missing_files: number
  unclaimed_files: number
}

export interface UnclaimedFileSummary {
  id: string
  relative_path: string
  file_name: string
  file_kind: string
  mime_type: string | null
  file_size: number
  modified_at: string | null
  first_seen_at: string
  last_seen_at: string
}

export interface FileClaimResult {
  asset_id: string
  file: FileSummary

}

export interface UploadCommandInput {
  asset_id: string
  source_path: string
  target_subdirectory: string
  recursive: boolean
}

export interface UploadCommandResult {
  asset_id: string
  asset_title: string
  archive_relative_path: string
  command: string
}

export interface AccountSummary {
  id: string
  username: string
  name: string
  role: string
  upload_username: string
}

export interface AccountLoginResponse extends AccountSummary {
  session_token: string
}

export interface RegistrationStatus {
  enabled: boolean
}
