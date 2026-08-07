import type {
  ArchiveHealthSummary,
  AssetDetail,
  AssetCreateInput,
  AssetSummary,
  AssetListResponse,
  AssetType,
  DashboardSummary,
  FileClaimResult,
  UploadCommandInput,
  UploadCommandResult,
  ScanRunSummary,
  UnclaimedFileSummary,
} from '@/types'

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, signal?: AbortSignal, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(path, {
    headers: body ? { Accept: 'application/json', 'Content-Type': 'application/json' } : { Accept: 'application/json' },
    method,
    signal,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    throw new ApiError(`请求失败（${response.status}）`, response.status)
  }
  return response.json() as Promise<T>
}

export function getDashboard(signal?: AbortSignal) {
  return request<DashboardSummary>('/api/dashboard', signal)
}

export function getAssets(
  assetType: AssetType | undefined,
  options: { query?: string; page?: number; pageSize?: number },
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 20),
  })
  if (assetType) params.set('asset_type', assetType)
  if (options.query) params.set('query', options.query)
  return request<AssetListResponse>(`/api/assets?${params}`, signal)
}

export function getAsset(assetId: string, signal?: AbortSignal) {
  return request<AssetDetail>(`/api/assets/${assetId}`, signal)
}

export function createAsset(input: AssetCreateInput) {
  return request<AssetSummary>('/api/assets', undefined, 'POST', input)
}


export function getArchiveHealth(signal?: AbortSignal) {
  return request<ArchiveHealthSummary>('/api/archive/health', signal)
}

export function runArchiveScan() {
  return request<ScanRunSummary>('/api/archive/scans', undefined, 'POST')
}

export function getUnclaimedFiles(signal?: AbortSignal) {
  return request<UnclaimedFileSummary[]>('/api/archive/unclaimed', signal)
}

export function claimUnclaimedFile(unclaimedFileId: string, assetId: string) {
  return request<FileClaimResult>(`/api/archive/unclaimed/${unclaimedFileId}/claim`, undefined, 'POST', { asset_id: assetId })
}

export function getUploadCommand(input: UploadCommandInput) {
  return request<UploadCommandResult>('/api/archive/upload-command', undefined, 'POST', input)
}
