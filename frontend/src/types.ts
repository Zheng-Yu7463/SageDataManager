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

