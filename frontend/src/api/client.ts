import type {
  ArchiveHealthSummary,
  AccountCreateInput,
  AccountLoginResponse,
  AccountSummary,
  AccountUpdateInput,
  AssetDetail,
  AssetCreateInput,
  AssetUpdateInput,
  AssetRelationInput,
  AssetSummary,
  RelatedAssetSummary,
  AssetListResponse,
  FileAccessMode,
  FileAccessTicket,
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
  const sessionToken = window.localStorage.getItem('sage-session-token')
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body) headers['Content-Type'] = 'application/json'
  if (sessionToken) headers['X-Sage-Session'] = sessionToken
  const response = await fetch(path, {
    headers,
    method,
    signal,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const payload = await response.json()
      if (typeof payload.detail === 'string') message = payload.detail
    } catch {
      // Keep the HTTP status message when the response has no JSON body.
    }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return undefined as T
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

export function updateAsset(assetId: string, input: AssetUpdateInput) {
  return request<AssetSummary>(`/api/assets/${assetId}`, undefined, 'PATCH', input)
}

export function archiveAsset(assetId: string) {
  return request<AssetSummary>(`/api/assets/${assetId}/archive`, undefined, 'POST')
}

export function restoreAsset(assetId: string) {
  return request<AssetSummary>(`/api/assets/${assetId}/restore`, undefined, 'POST')
}

export function getArchivedAssets() {
  return request<AssetSummary[]>('/api/assets/archived')
}

export function addAssetRelation(assetId: string, input: AssetRelationInput) {
  return request<RelatedAssetSummary>(`/api/assets/${assetId}/relations`, undefined, 'POST', input)
}

export function removeAssetRelation(assetId: string, relationId: string) {
  return request<void>(`/api/assets/${assetId}/relations/${relationId}`, undefined, 'DELETE')
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

export function getFileAccessTicket(fileId: string, mode: FileAccessMode) {
  return request<FileAccessTicket>(`/api/files/${fileId}/tickets`, undefined, 'POST', { mode })
}

export function loginAccount(username: string, password: string) {
  return request<AccountLoginResponse>('/api/auth/login', undefined, 'POST', { username, password })
}

export function getCurrentAccount() {
  return request<AccountSummary>('/api/auth/me')
}

export function getAdminAccounts() {
  return request<AccountSummary[]>('/api/auth/admin-accounts')
}

export function createAdminAccount(input: AccountCreateInput) {
  return request<AccountSummary>('/api/auth/admin-accounts', undefined, 'POST', input)
}

export function updateAdminAccount(username: string, input: AccountUpdateInput) {
  return request<AccountSummary>(`/api/auth/admin-accounts/${username}`, undefined, 'PATCH', input)
}
