<script setup lang="ts">
import { Archive, ArchiveRestore, ArrowLeft, ArrowRight, CircleAlert, RefreshCw } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getArchivedAssets, restoreAsset } from '@/api/client'
import { assetMeta } from '@/catalogue'
import AssetIcon from '@/components/AssetIcon.vue'
import { useBranding } from '@/composables/useBranding'
import type { ArchivedAssetListResponse, AssetSummary } from '@/types'

const route = useRoute()
const router = useRouter()
const data = ref<ArchivedAssetListResponse | null>(null)
const loading = ref(true)
const loadError = ref('')
const restoreErrors = ref<Record<string, string>>({})
const restoringIds = ref(new Set<string>())
const pageSize = 20
const { pageEyebrow } = useBranding()
let loadController: AbortController | undefined

const pageCount = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize)))

function pageLocation(page: number) {
  return { name: 'archived-assets', query: page > 1 ? { page: String(page) } : {} }
}

async function load(page: number) {
  loadController?.abort()
  const requestController = new AbortController()
  loadController = requestController
  loading.value = true
  loadError.value = ''
  try {
    const result = await getArchivedAssets(page, pageSize, requestController.signal)
    if (loadController !== requestController) return
    const lastPage = Math.max(1, Math.ceil(result.total / pageSize))
    if (page > lastPage) {
      await router.replace(pageLocation(lastPage))
      return
    }
    data.value = result
  } catch (reason) {
    if (loadController !== requestController) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    loadError.value = reason instanceof Error ? reason.message : '无法读取已归档资产'
  } finally {
    if (loadController === requestController) loading.value = false
  }
}

async function restore(asset: AssetSummary) {
  if (!data.value || restoringIds.value.has(asset.id)) return
  restoringIds.value = new Set(restoringIds.value).add(asset.id)
  const nextErrors = { ...restoreErrors.value }
  delete nextErrors[asset.id]
  restoreErrors.value = nextErrors
  const requestedPage = data.value.page
  const requestedRoute = route.fullPath
  try {
    await restoreAsset(asset.id)
  } catch (reason) {
    if (route.fullPath !== requestedRoute) return
    restoreErrors.value = {
      ...restoreErrors.value,
      [asset.id]: reason instanceof Error ? reason.message : '无法恢复资产',
    }
    return
  } finally {
    const nextRestoringIds = new Set(restoringIds.value)
    nextRestoringIds.delete(asset.id)
    restoringIds.value = nextRestoringIds
  }
  if (!data.value || route.fullPath !== requestedRoute) return
  const remainingItems = data.value.items.filter((item) => item.id !== asset.id)
  const remainingTotal = Math.max(0, data.value.total - 1)
  data.value = { ...data.value, items: remainingItems, total: remainingTotal }
  const lastPage = Math.max(1, Math.ceil(remainingTotal / pageSize))
  if (requestedPage > lastPage) {
    try {
      await router.replace(pageLocation(lastPage))
    } catch {
      loadError.value = '资产已恢复，但归档页码暂时无法同步。'
    }
  }
}

function goToPage(page: number) {
  void router.push(pageLocation(page))
}

watch(
  () => route.query.page,
  (pageValue) => {
    const validPage = typeof pageValue === 'string' && /^[1-9]\d*$/.test(pageValue)
    const parsedPage = validPage ? Number(pageValue) : 1
    if (pageValue !== undefined && !validPage) {
      void router.replace(pageLocation(1))
      return
    }
    void load(parsedPage)
  },
  { immediate: true },
)
onBeforeUnmount(() => loadController?.abort())
</script>

<template>
  <div class="page archived-page">
    <header class="page-heading">
      <div><p class="eyebrow">{{ pageEyebrow('RETENTION') }}</p><h1>已归档资产</h1><p>归档资产不会出现在普通目录中，原始文件和元数据均被保留，可随时恢复。</p></div>
      <button class="button button--outline" :disabled="loading" @click="load(data?.page ?? 1)"><RefreshCw :size="16" />刷新</button>
    </header>
    <div v-if="loading && !data" class="state-panel" role="status" aria-live="polite"><span class="loader-ring"></span><p>正在读取归档资产…</p></div>
    <div v-else-if="loadError && !data" class="state-panel state-panel--error" role="alert"><CircleAlert :size="28" /><strong>无法读取归档资产</strong><p>{{ loadError }}</p><button class="button button--outline" @click="load(1)">重试</button></div>
    <div v-else-if="data && !data.total" class="archived-empty"><Archive :size="34" /><h2>暂无已归档资产</h2><p>从资产详情页归档的记录会保留在这里，方便恢复。</p></div>
    <template v-else-if="data">
      <div class="archived-summary"><strong>{{ data.total }}</strong><span>项归档记录</span><span v-if="loading" class="tiny-spinner"></span></div>
      <p v-if="loadError" class="archived-error" role="alert">{{ loadError }}</p>
      <section class="archived-list">
        <article v-for="asset in data.items" :key="asset.id" class="archived-row">
          <span class="archived-icon"><AssetIcon :type="asset.type" :size="21" /></span>
          <div><p class="eyebrow">{{ assetMeta[asset.type].english.toUpperCase() }}</p><h2>{{ asset.title }}</h2><p>{{ asset.summary || '未附摘要' }}</p><p v-if="restoreErrors[asset.id]" class="archived-row-error" role="alert">{{ restoreErrors[asset.id] }}</p></div>
          <span class="archived-data">{{ asset.file_count }} 个文件 · {{ asset.tags.join('、') || '无标签' }}</span>
          <button class="button button--primary" :disabled="restoringIds.has(asset.id)" @click="restore(asset)"><ArchiveRestore :size="16" />{{ restoringIds.has(asset.id) ? '正在恢复' : '恢复资产' }}</button>
        </article>
      </section>
      <nav v-if="pageCount > 1" class="pagination" aria-label="已归档资产分页">
        <button :disabled="data.page <= 1 || loading" @click="goToPage(data.page - 1)"><ArrowLeft :size="14" />上一页</button>
        <span>第 {{ data.page }} / {{ pageCount }} 页</span>
        <button :disabled="data.page >= pageCount || loading" @click="goToPage(data.page + 1)">下一页<ArrowRight :size="14" /></button>
      </nav>
    </template>
  </div>
</template>

<style scoped>
.archived-summary { display: flex; min-height: 42px; margin-bottom: 10px; padding: 0 14px; align-items: center; color: var(--muted); background: rgba(252,253,249,.72); border: 1px solid var(--line); border-radius: 6px; gap: 6px; font-size: 10px; }.archived-summary strong { color: var(--ink); font-family: "Iowan Old Style", "Songti SC", serif; font-size: 18px; font-weight: 500; }.archived-summary .tiny-spinner { margin-left: auto; }.archived-list { display: grid; overflow: hidden; background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 8px; }.archived-row { display: grid; min-height: 82px; padding: 15px 18px; align-items: center; grid-template-columns: 42px minmax(0, 1fr) auto auto; gap: 14px; border-bottom: 1px solid var(--line); }.archived-row:last-child { border-bottom: 0; }.archived-icon { display: grid; width: 40px; height: 40px; color: #7c887f; place-items: center; background: #eef1ed; border-radius: 8px; }.archived-row h2 { margin: 3px 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 16px; font-weight: 500; }.archived-row p:not(.eyebrow) { display: -webkit-box; max-width: 520px; margin: 0; overflow: hidden; color: var(--muted); font-size: 11px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 1; }.archived-row .archived-row-error { display: block; margin-top: 5px; color: #a6633b; -webkit-line-clamp: unset; }.archived-data { color: #7c887f; font-size: 10px; white-space: nowrap; }.archived-empty { display: grid; min-height: 330px; place-items: center; align-content: center; text-align: center; background: rgba(252,253,249,.8); border: 1px dashed #cbd4ca; border-radius: 8px; }.archived-empty svg { color: #829087; }.archived-empty h2 { margin: 12px 0 4px; font-family: "Iowan Old Style", "Songti SC", serif; }.archived-empty p { margin: 0; color: #7c887f; font-size: 12px; }.archived-error { margin: 0 0 10px; padding: 12px 14px; color: #a6633b; background: #fff6ef; border-left: 3px solid #bd7750; font-size: 12px; } @media (max-width: 720px) { .archived-row { grid-template-columns: 42px minmax(0, 1fr); }.archived-data { grid-column: 2; }.archived-row button { grid-column: 2; width: fit-content; } }
</style>
