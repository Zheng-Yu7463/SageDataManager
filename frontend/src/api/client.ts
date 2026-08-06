import type { AssetListResponse, AssetType, DashboardSummary } from '@/types'

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: 'application/json' },
    signal,
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
