import type {
  ArchiveHealthSummary,
  ActivityListResponse,
  AccountCreateInput,
  AccountLoginResponse,
  AccountSummary,
  AccountUpdateInput,
  AssetDetail,
  AssetChoiceSummary,
  AssetCreateInput,
  BatchAssetImportResult,
  AssetUpdateInput,
  AssetRelationInput,
  AssetSummary,
  AssetVersionCreateInput,
  RelatedAssetSummary,
  AssetVersionSummary,
  AssetListResponse,
  FileAccessMode,
  FileAccessTicket,
  InstanceBranding,
  InstanceBrandingInput,
  AssetType,
  DashboardSummary,
  FileClaimResult,
  PaperCitation,
  PaperCitationExport,
  UploadCommandInput,
  UploadCommandResult,
  ScanRunSummary,
  UnclaimedFileSummary,
} from '@/types'
import { expireSession, getSessionToken } from '@/session'

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

type RequestAuthentication = 'session' | 'none'

async function request<T>(
  path: string,
  signal?: AbortSignal,
  method = 'GET',
  body?: unknown,
  authentication: RequestAuthentication = 'session',
): Promise<T> {
  const sessionToken = authentication === 'session' ? getSessionToken() : null
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
    await raiseApiError(response, Boolean(sessionToken))
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

async function raiseApiError(response: Response, authenticatedRequest: boolean): Promise<never> {
  if (response.status === 401 && authenticatedRequest) expireSession()
  let message = `请求失败（${response.status}）`
  try {
    const payload = await response.json()
    if (typeof payload.detail === 'string') message = payload.detail
  } catch {
    // Keep the HTTP status message when the response has no JSON body.
  }
  throw new ApiError(message, response.status)
}

export function getDashboard(signal?: AbortSignal) {
  return request<DashboardSummary>('/api/dashboard', signal)
}

export function getActivities(page = 1, action?: string) {
  const params = new URLSearchParams({ page: String(page), page_size: '30' })
  if (action) params.set('action', action)
  return request<ActivityListResponse>(`/api/dashboard/activities?${params}`)
}

export function getAssets(
  assetType: AssetType | undefined,
  options: { query?: string; status?: string; visibility?: string; hasFiles?: boolean; venue?: string; year?: number; page?: number; pageSize?: number },
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 20),
  })
  if (assetType) params.set('asset_type', assetType)
  if (options.query) params.set('query', options.query)
  if (options.status) params.set('status', options.status)
  if (options.visibility) params.set('visibility', options.visibility)
  if (options.hasFiles !== undefined) params.set('has_files', String(options.hasFiles))
  if (options.venue) params.set('venue', options.venue)
  if (options.year !== undefined) params.set('year', String(options.year))
  return request<AssetListResponse>(`/api/assets?${params}`, signal)
}

export function getAssetChoices(query: string, signal?: AbortSignal) {
  const params = new URLSearchParams({ query, limit: '20' })
  return request<AssetChoiceSummary[]>(`/api/assets/choices?${params}`, signal)
}

export function getAsset(assetId: string, signal?: AbortSignal) {
  return request<AssetDetail>(`/api/assets/${assetId}`, signal)
}

export function getPaperCitation(assetId: string, signal?: AbortSignal) {
  return request<PaperCitation>(`/api/assets/${assetId}/citation/bibtex`, signal)
}

export function exportPaperCitations(options: {
  query?: string
  status?: string
  visibility?: string
  hasFiles?: boolean
  venue?: string
  year?: number
}) {
  const params = new URLSearchParams()
  if (options.query) params.set('query', options.query)
  if (options.status) params.set('status', options.status)
  if (options.visibility) params.set('visibility', options.visibility)
  if (options.hasFiles !== undefined) params.set('has_files', String(options.hasFiles))
  if (options.venue) params.set('venue', options.venue)
  if (options.year !== undefined) params.set('year', String(options.year))
  return request<PaperCitationExport>(`/api/assets/citations/bibtex?${params}`)
}

export function createAsset(input: AssetCreateInput) {
  return request<AssetSummary>('/api/assets', undefined, 'POST', input)
}

export function importAssetsYaml(content: string) {
  return request<BatchAssetImportResult>('/api/assets/import/yaml', undefined, 'POST', { content })
}

export function updateAsset(assetId: string, input: AssetUpdateInput) {
  return request<AssetSummary>(`/api/assets/${assetId}`, undefined, 'PATCH', input)
}
export function importAssets(assets: AssetCreateInput[]) {
  return request<BatchAssetImportResult>('/api/assets/import', undefined, 'POST', { assets })
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

export function addAssetVersion(assetId: string, input: AssetVersionCreateInput) {
  return request<AssetVersionSummary>(`/api/assets/${assetId}/versions`, undefined, 'POST', input)
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
  return request<AccountLoginResponse>(
    '/api/auth/login',
    undefined,
    'POST',
    { username, password },
    'none',
  )
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

export function getInstanceBranding(signal?: AbortSignal) {
  return request<InstanceBranding>('/api/settings/branding', signal)
}

export function updateInstanceBranding(input: InstanceBrandingInput) {
  return request<InstanceBranding>('/api/settings/branding', undefined, 'PATCH', input)
}

export async function uploadInstanceLogo(file: File) {
  const sessionToken = getSessionToken()
  const response = await fetch('/api/settings/branding/logo', {
    method: 'PUT',
    headers: {
      Accept: 'application/json',
      'Content-Type': file.type,
      ...(sessionToken ? { 'X-Sage-Session': sessionToken } : {}),
    },
    body: file,
  })
  if (!response.ok) {
    await raiseApiError(response, Boolean(sessionToken))
  }
  return response.json() as Promise<InstanceBranding>
}

export function removeInstanceLogo() {
  return request<InstanceBranding>('/api/settings/branding/logo', undefined, 'DELETE')
}
