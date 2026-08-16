<script setup lang="ts">
import { useDebounceFn } from '@vueuse/core'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Copy,
  Database,
  Download,
  File,
  FolderUp,
  ExternalLink,
  Grid2X2,
  List,
  LockKeyhole,
  LoaderCircle,
  Plus,
  Save,
  X,
  Search,
  ShieldCheck,
  SlidersHorizontal,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'

import AssetIcon from '@/components/AssetIcon.vue'
import {
  createAsset,
  exportPublicationCitations,
  finalizeUpload,
  getAssets,
  getPublicationCitation,
  getUploadCommand,
  getUploadStatus,
} from '@/api/client'
import { assetMeta } from '@/catalogue'
import { useOverlayFocus } from '@/composables/useOverlayFocus'
import { useDismissiblePopover } from '@/composables/useDismissiblePopover'
import { useBranding } from '@/composables/useBranding'
import { isPublicationMetadata } from '@/types'
import type {
  AssetListResponse,
  AssetSummary,
  AssetType,
  PublicationMetadata,
  UploadCommandResult,
  UploadFinalizeResult,
  UploadStatusResult,
  Visibility,
} from '@/types'
import { copyText, downloadTextFile } from '@/utils/textFiles'

const route = useRoute()
const router = useRouter()
const { pageEyebrow } = useBranding()
const data = ref<AssetListResponse | null>(null)
const loading = ref(false)
const error = ref('')
const filtersOpen = ref(false)
const filterTrigger = ref<HTMLElement | null>(null)
const filterPopover = ref<HTMLElement | null>(null)
const filters = ref({
  status: '',
  visibility: '',
  hasFiles: '',
  venue: '',
  year: '',
})
const query = ref(typeof route.query.q === 'string' ? route.query.q : '')
const page = ref(1)
const view = ref<'list' | 'grid'>('list')
let controller: AbortController | undefined
let loadedRequestKey = ''

const assetType = computed(() => route.meta.assetType as AssetType)
const publicationCatalogue = computed(() => ['paper', 'literature'].includes(assetType.value))
const registrationOpen = ref(false)
const registrationDialog = ref<HTMLElement | null>(null)
const creating = ref(false)
const createError = ref('')
const registration = ref({
  title: '',
  slug: '',
  summary: '',
  status: 'draft',
  visibility: 'lab' as Visibility,
  version: '',
  tags: '',
  venue: '',
  year: String(new Date().getFullYear()),
  track: '',
  authors: '',
  sourceId: '',
  sourceUrl: '',
  pdfUrl: '',
  citationKey: '',
  entryType: 'inproceedings' as PublicationMetadata['entry_type'],
  booktitle: '',
  journal: '',
  pages: '',
  publisher: '',
})
const registrationBaseline = ref('')
const citationActionId = ref<string | null>(null)
const citationCopiedId = ref<string | null>(null)
const citationError = ref('')
let citationRequestVersion = 0
let citationCopiedTimer: number | undefined
const exportingCitations = ref(false)
const uploadAsset = ref<AssetSummary | null>(null)
const uploadDialog = ref<HTMLElement | null>(null)
const uploadGenerating = ref(false)
const uploadFinalizing = ref(false)
const uploadError = ref('')
const uploadRefreshError = ref('')
const uploadStatusError = ref('')
const uploadStatusChecking = ref(false)
const uploadCopied = ref(false)
const uploadResult = ref<UploadCommandResult | null>(null)
const uploadStatus = ref<UploadStatusResult | null>(null)
const uploadFinalizeResult = ref<UploadFinalizeResult | null>(null)
const uploadPhase = ref<'configure' | 'transfer' | 'success'>('configure')
let uploadStatusTimer: number | undefined
let uploadStatusRequestVersion = 0
let uploadOperationVersion = 0
const upload = ref({
  sourcePath: '',
  directory: '',
  nestedPath: '',
  recursive: false,
})
const uploadBaseline = ref('')

const activeFilterCount = computed(() => Object.values(filters.value).filter(Boolean).length)
const catalogueHasConstraints = computed(() => Boolean(query.value.trim() || activeFilterCount.value))
const meta = computed(() => assetMeta[assetType.value])
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / 20)))
const currentUploadFolders = computed(() => uploadAsset.value?.upload_directories ?? [])
const uploadOpen = computed(() => Boolean(uploadAsset.value))
const uploadBusy = computed(() => uploadGenerating.value || uploadFinalizing.value)
const registrationDirty = computed(() => registrationOpen.value && JSON.stringify(registration.value) !== registrationBaseline.value)
const uploadConfigurationDirty = computed(() => (
  uploadOpen.value
  && uploadPhase.value === 'configure'
  && JSON.stringify(upload.value) !== uploadBaseline.value
))
const uploadTransferActive = computed(() => uploadOpen.value && uploadPhase.value === 'transfer')
const hasProtectedWork = computed(() => registrationDirty.value || uploadConfigurationDirty.value || uploadTransferActive.value)
const uploadTargetPath = computed(() => {
  if (!uploadAsset.value || !upload.value.directory) return ''
  return [
    uploadAsset.value.type,
    uploadAsset.value.slug,
    upload.value.directory,
    upload.value.nestedPath.trim(),
  ].filter(Boolean).join('/')
})
const registrationValid = computed(() => {
  const baseValid = Boolean(
    registration.value.title.trim()
    && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(registration.value.slug.trim())
    && registration.value.slug.trim().length >= 3,
  )
  if (!baseValid || !publicationCatalogue.value) return baseValid

  const year = Number(registration.value.year)
  return Boolean(
    registration.value.venue.trim()
    && Number.isInteger(year)
    && year >= 1900
    && year <= 2200
    && registration.value.track.trim()
    && registration.value.authors.split(/[，,]/).some((author) => author.trim())
    && registration.value.sourceId.trim()
    && isHttpUrl(registration.value.sourceUrl)
    && isHttpUrl(registration.value.pdfUrl)
    && (assetType.value !== 'literature' || registration.value.summary.trim())
    && (
      !registration.value.citationKey.trim()
      || /^[A-Za-z][A-Za-z0-9_:+.-]*$/.test(registration.value.citationKey.trim())
    )
    && (registration.value.entryType !== 'article' || registration.value.journal.trim()),
  )
})

useOverlayFocus(registrationOpen, registrationDialog, closeRegistration)
useOverlayFocus(uploadOpen, uploadDialog, closeUpload)
useDismissiblePopover(filtersOpen, filterTrigger, filterPopover)

function queryValue(key: string) {
  const value = route.query[key]
  return typeof value === 'string' ? value : ''
}

function isHttpUrl(value: string) {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function readCatalogueState() {
  const nextPage = Number(queryValue('page'))
  query.value = queryValue('q')
  filters.value = {
    status: queryValue('status'),
    visibility: ['lab', 'project', 'restricted'].includes(queryValue('visibility')) ? queryValue('visibility') : '',
    hasFiles: ['present', 'missing'].includes(queryValue('files')) ? queryValue('files') : '',
    venue: publicationCatalogue.value ? queryValue('venue') : '',
    year: publicationCatalogue.value && /^\d{4}$/.test(queryValue('year')) ? queryValue('year') : '',
  }
  page.value = Number.isInteger(nextPage) && nextPage > 0 ? nextPage : 1
  view.value = queryValue('view') === 'grid' ? 'grid' : 'list'
}

function catalogueQuery() {
  const next: Record<string, string> = {}
  if (query.value.trim()) next.q = query.value.trim()
  if (filters.value.status) next.status = filters.value.status
  if (filters.value.visibility) next.visibility = filters.value.visibility
  if (filters.value.hasFiles) next.files = filters.value.hasFiles
  if (publicationCatalogue.value && filters.value.venue) next.venue = filters.value.venue
  if (publicationCatalogue.value && filters.value.year) next.year = filters.value.year
  if (page.value > 1) next.page = String(page.value)
  if (view.value === 'grid') next.view = 'grid'
  return next
}

function syncCatalogueRoute() {
  void router.replace({ query: catalogueQuery() })
}

function catalogueRequestKey() {
  return JSON.stringify([
    assetType.value,
    query.value.trim(),
    filters.value.status,
    filters.value.visibility,
    filters.value.hasFiles,
    publicationCatalogue.value ? filters.value.venue : '',
    publicationCatalogue.value ? filters.value.year : '',
    page.value,
  ])
}

async function load() {
  controller?.abort()
  const requestKey = catalogueRequestKey()
  if (loadedRequestKey !== requestKey) data.value = null
  const requestController = new AbortController()
  controller = requestController
  loading.value = true
  error.value = ''
  try {
    const result = await getAssets(
      assetType.value,
      {
        query: query.value.trim(),
        status: filters.value.status,
        visibility: filters.value.visibility,
        hasFiles: filters.value.hasFiles === '' ? undefined : filters.value.hasFiles === 'present',
        venue: publicationCatalogue.value ? filters.value.venue : undefined,
        year: publicationCatalogue.value && filters.value.year ? Number(filters.value.year) : undefined,
        page: page.value,
        pageSize: 20,
      },
      requestController.signal,
    )
    if (controller !== requestController) return
    data.value = result
    loadedRequestKey = requestKey
    const lastPage = Math.max(1, Math.ceil(result.total / 20))
    if (page.value > lastPage) {
      page.value = lastPage
      syncCatalogueRoute()
    }
    return true
  } catch (reason) {
    if (controller !== requestController) return false
    if (reason instanceof DOMException && reason.name === 'AbortError') return false
    error.value = reason instanceof Error ? reason.message : '无法读取资产目录'
    return false
  } finally {
    if (controller === requestController) loading.value = false
  }
}

const updateSearch = useDebounceFn(() => {
  page.value = 1
  syncCatalogueRoute()
}, 280)

function applyFilters() {
  page.value = 1
  syncCatalogueRoute()
}

function clearFilters() {
  filters.value = { status: '', visibility: '', hasFiles: '', venue: '', year: '' }
  applyFilters()
}

function clearCatalogueConstraints() {
  query.value = ''
  filters.value = { status: '', visibility: '', hasFiles: '', venue: '', year: '' }
  page.value = 1
  syncCatalogueRoute()
}

function changePage(nextPage: number) {
  if (nextPage < 1 || nextPage > totalPages.value) return
  page.value = nextPage
  syncCatalogueRoute()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function changeView(nextView: 'list' | 'grid') {
  view.value = nextView
  syncCatalogueRoute()
}

function formatBytes(value: number) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  if (!value) return '—'
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index >= 3 ? 1 : 0)} ${units[index]}`
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', year: 'numeric' }).format(new Date(value))
}

function detailText(details: Record<string, unknown>) {
  if (isPublicationMetadata(details)) return `${details.venue} ${details.year} · ${details.track}`
  const entries = Object.entries(details).filter(([, value]) => typeof value !== 'object').slice(0, 2)
  return entries.map(([key, value]) => `${key.replace('_', ' ')} · ${value}`).join('  /  ')
}

function publicationMetadata(asset: AssetSummary): PublicationMetadata | null {
  return ['paper', 'literature'].includes(asset.type) && isPublicationMetadata(asset.details)
    ? asset.details
    : null
}

function authorText(metadata: PublicationMetadata) {
  const visible = metadata.authors.slice(0, 3).join('、')
  return metadata.authors.length > 3 ? `${visible} 等 ${metadata.authors.length} 位作者` : visible
}

function openRegistration() {
  filtersOpen.value = false
  registration.value = {
    title: '',
    slug: '',
    summary: '',
    status: 'draft',
    visibility: 'lab',
    version: '',
    tags: '',
    venue: '',
    year: String(new Date().getFullYear()),
    track: '',
    authors: '',
    sourceId: '',
    sourceUrl: '',
    pdfUrl: '',
    citationKey: '',
    entryType: assetType.value === 'literature' ? 'article' : 'inproceedings',
    booktitle: '',
    journal: '',
    pages: '',
    publisher: '',
  }
  registrationBaseline.value = JSON.stringify(registration.value)
  createError.value = ''
  registrationOpen.value = true
}

function closeRegistration() {
  if (creating.value) return
  if (registrationDirty.value && !window.confirm('资产登记内容尚未提交，确定关闭吗？')) return
  registrationOpen.value = false
}

async function registerAsset() {
  if (!registrationValid.value) return
  const registrationAssetType = assetType.value
  creating.value = true
  createError.value = ''
  try {
    await createAsset({
      type: registrationAssetType,
      title: registration.value.title.trim(),
      slug: registration.value.slug.trim(),
      summary: registration.value.summary.trim(),
      status: registration.value.status.trim() || 'draft',
      visibility: registration.value.visibility,
      version: registration.value.version.trim() || null,
      tags: registration.value.tags.split(/[，,]/).map((tag) => tag.trim()).filter(Boolean),
      details: publicationCatalogue.value ? {
        venue: registration.value.venue.trim(),
        year: Number(registration.value.year),
        track: registration.value.track.trim(),
        authors: registration.value.authors.split(/[，,]/).map((author) => author.trim()).filter(Boolean),
        source_id: registration.value.sourceId.trim(),
        source_url: registration.value.sourceUrl.trim(),
        pdf_url: registration.value.pdfUrl.trim(),
        ...(registration.value.summary.trim() ? { abstract: registration.value.summary.trim() } : {}),
        entry_type: registration.value.entryType,
        ...(registration.value.citationKey.trim() ? { citation_key: registration.value.citationKey.trim() } : {}),
        ...(registration.value.booktitle.trim() ? { booktitle: registration.value.booktitle.trim() } : {}),
        ...(registration.value.journal.trim() ? { journal: registration.value.journal.trim() } : {}),
        ...(registration.value.pages.trim() ? { pages: registration.value.pages.trim() } : {}),
        ...(registration.value.publisher.trim() ? { publisher: registration.value.publisher.trim() } : {}),
      } : {},
    })
    if (assetType.value !== registrationAssetType) return
    registrationOpen.value = false
    query.value = ''
    page.value = 1
    if (Object.keys(route.query).length > 0) await router.replace({ query: {} })
    else await load()
  } catch (reason) {
    createError.value = reason instanceof Error ? reason.message : '登记失败，请稍后重试'
  } finally {
    creating.value = false
  }
}

function stopUploadStatusPolling() {
  uploadStatusRequestVersion += 1
  if (uploadStatusTimer) window.clearTimeout(uploadStatusTimer)
  uploadStatusTimer = undefined
  uploadStatusChecking.value = false
}

async function pollUploadStatus(requestVersion: number) {
  if (
    requestVersion !== uploadStatusRequestVersion
    || uploadPhase.value !== 'transfer'
    || !uploadResult.value
  ) return
  uploadStatusChecking.value = true
  try {
    const result = await getUploadStatus(
      uploadResult.value.upload_id,
      uploadResult.value.upload_token,
    )
    if (requestVersion !== uploadStatusRequestVersion) return
    uploadStatus.value = result
    uploadStatusError.value = ''
  } catch (reason) {
    if (requestVersion !== uploadStatusRequestVersion) return
    uploadStatusError.value = reason instanceof Error ? reason.message : '无法读取上传进度'
  } finally {
    if (requestVersion !== uploadStatusRequestVersion) return
    uploadStatusChecking.value = false
    if (uploadPhase.value === 'transfer' && uploadStatus.value?.status !== 'completed') {
      uploadStatusTimer = window.setTimeout(() => {
        void pollUploadStatus(requestVersion)
      }, 2000)
    }
  }
}

function startUploadStatusPolling() {
  stopUploadStatusPolling()
  const requestVersion = uploadStatusRequestVersion
  void pollUploadStatus(requestVersion)
}

function openUpload(asset: AssetSummary) {
  filtersOpen.value = false
  uploadOperationVersion += 1
  uploadAsset.value = asset
  upload.value = {
    sourcePath: '',
    directory: asset.default_upload_directory,
    nestedPath: '',
    recursive: false,
  }
  uploadBaseline.value = JSON.stringify(upload.value)
  stopUploadStatusPolling()
  uploadError.value = ''
  uploadRefreshError.value = ''
  uploadStatusError.value = ''
  uploadCopied.value = false
  uploadResult.value = null
  uploadStatus.value = null
  uploadFinalizeResult.value = null
  uploadPhase.value = 'configure'
}

function closeUpload() {
  if (uploadBusy.value) return
  if (uploadConfigurationDirty.value && !window.confirm('上传配置尚未生成命令，确定关闭吗？')) return
  if (uploadTransferActive.value && !window.confirm('上传任务尚未完成，关闭后将无法在此窗口继续查看进度或检测入库。确定关闭吗？')) return
  stopUploadStatusPolling()
  uploadAsset.value = null
}

async function generateUploadCommand() {
  if (uploadGenerating.value || !uploadAsset.value || !upload.value.sourcePath.trim()) return
  const operationVersion = uploadOperationVersion
  uploadGenerating.value = true
  uploadError.value = ''
  uploadRefreshError.value = ''
  uploadCopied.value = false
  try {
    const uploadCommand = await getUploadCommand({
      asset_id: uploadAsset.value.id,
      source_path: upload.value.sourcePath.trim(),
      target_subdirectory: [upload.value.directory, upload.value.nestedPath.trim()].filter(Boolean).join('/'),
      recursive: upload.value.recursive,
    })
    if (operationVersion !== uploadOperationVersion) return
    uploadResult.value = uploadCommand
    uploadPhase.value = 'transfer'
    startUploadStatusPolling()
  } catch (reason) {
    if (operationVersion !== uploadOperationVersion) return
    uploadError.value = reason instanceof Error ? reason.message : '无法生成上传命令'
  } finally {
    uploadGenerating.value = false
  }
}

function reconfigureUpload() {
  if (uploadTransferActive.value && !window.confirm('重新配置后，当前上传任务将不再显示在此窗口。确定继续吗？')) return
  stopUploadStatusPolling()
  uploadError.value = ''
  uploadStatusError.value = ''
  uploadCopied.value = false
  uploadResult.value = null
  uploadStatus.value = null
  uploadFinalizeResult.value = null
  uploadPhase.value = 'configure'
}

function confirmProtectedWorkExit() {
  if (uploadTransferActive.value) {
    return window.confirm('上传任务尚未完成。离开后将无法在此页面继续查看进度或检测入库，确定离开吗？')
  }
  return !hasProtectedWork.value || window.confirm('资产登记或上传配置尚未提交，确定离开此页面吗？')
}

function preventProtectedWorkExit(event: BeforeUnloadEvent) {
  if (!hasProtectedWork.value) return
  event.preventDefault()
  event.returnValue = ''
}

async function finalizeCurrentUpload() {
  if (uploadFinalizing.value || !uploadResult.value) return
  const operationVersion = uploadOperationVersion
  stopUploadStatusPolling()
  uploadFinalizing.value = true
  uploadError.value = ''
  uploadRefreshError.value = ''
  try {
    const finalizeResult = await finalizeUpload(
      uploadResult.value.upload_id,
      uploadResult.value.upload_token,
    )
    if (operationVersion !== uploadOperationVersion) return
    uploadFinalizeResult.value = finalizeResult
    uploadPhase.value = 'success'
  } catch (reason) {
    if (operationVersion !== uploadOperationVersion) return
    uploadError.value = reason instanceof Error ? reason.message : '无法检测并入库，请稍后重试'
    startUploadStatusPolling()
    return
  } finally {
    uploadFinalizing.value = false
  }
  const refreshed = await load()
  if (refreshed) {
    const refreshedAsset = data.value?.items.find(
      (asset) => asset.id === uploadFinalizeResult.value?.asset_id,
    )
    if (refreshedAsset) uploadAsset.value = refreshedAsset
  } else {
    uploadRefreshError.value = '文件已入库，但目录暂时无法刷新。'
  }
}

async function copyUploadCommand() {
  if (!uploadResult.value) return
  try {
    await copyText(uploadResult.value.command)
    uploadCopied.value = true
  } catch {
    uploadError.value = '浏览器无法写入剪贴板，请手动复制命令。'
  }
}

async function copyPublicationCitation(asset: AssetSummary) {
  const requestVersion = ++citationRequestVersion
  citationActionId.value = asset.id
  citationError.value = ''
  try {
    const citation = await getPublicationCitation(asset.id)
    if (requestVersion !== citationRequestVersion) return
    await copyText(citation.bibtex)
    if (requestVersion !== citationRequestVersion) return
    citationCopiedId.value = asset.id
    if (citationCopiedTimer) window.clearTimeout(citationCopiedTimer)
    citationCopiedTimer = window.setTimeout(() => {
      if (requestVersion === citationRequestVersion) citationCopiedId.value = null
      citationCopiedTimer = undefined
    }, 1800)
  } catch (reason) {
    if (requestVersion !== citationRequestVersion) return
    citationError.value = reason instanceof Error ? reason.message : '无法复制 BibTeX'
  } finally {
    if (requestVersion === citationRequestVersion) citationActionId.value = null
  }
}

async function downloadFilteredCitations() {
  exportingCitations.value = true
  citationError.value = ''
  try {
    const result = await exportPublicationCitations({
      assetType: assetType.value as 'paper' | 'literature',
      query: query.value.trim(),
      status: filters.value.status,
      visibility: filters.value.visibility,
      hasFiles: filters.value.hasFiles === '' ? undefined : filters.value.hasFiles === 'present',
      venue: filters.value.venue,
      year: filters.value.year ? Number(filters.value.year) : undefined,
    })
    downloadTextFile(result.filename, result.bibtex, 'application/x-bibtex;charset=utf-8')
  } catch (reason) {
    citationError.value = reason instanceof Error ? reason.message : '无法导出 BibTeX'
  } finally {
    exportingCitations.value = false
  }
}
function resetRouteScopedActions() {
  filtersOpen.value = false
  registrationOpen.value = false
  createError.value = ''
  uploadOperationVersion += 1
  stopUploadStatusPolling()
  uploadGenerating.value = false
  uploadFinalizing.value = false
  uploadAsset.value = null
  uploadResult.value = null
  uploadStatus.value = null
  uploadFinalizeResult.value = null
  uploadError.value = ''
  uploadRefreshError.value = ''
  uploadStatusError.value = ''
  uploadCopied.value = false
  uploadPhase.value = 'configure'
}


watch(
  () => [
    route.meta.assetType,
    route.query.q,
    route.query.status,
    route.query.visibility,
    route.query.files,
    route.query.venue,
    route.query.year,
    route.query.page,
    route.query.view,
  ],
  (nextState, previousState) => {
    if (previousState && nextState[0] !== previousState[0]) {
      resetRouteScopedActions()
    }
    citationRequestVersion += 1
    citationActionId.value = null
    citationCopiedId.value = null
    citationError.value = ''
    if (citationCopiedTimer) window.clearTimeout(citationCopiedTimer)
    citationCopiedTimer = undefined
    readCatalogueState()
    void load()
  },
  { immediate: true },
)
onBeforeRouteUpdate((to, from) => (
  to.meta.assetType === from.meta.assetType || confirmProtectedWorkExit()
))
onBeforeRouteLeave(confirmProtectedWorkExit)
onMounted(() => window.addEventListener('beforeunload', preventProtectedWorkExit))
onBeforeUnmount(() => {
  controller?.abort()
  stopUploadStatusPolling()
  if (citationCopiedTimer) window.clearTimeout(citationCopiedTimer)
  window.removeEventListener('beforeunload', preventProtectedWorkExit)
})
</script>

<template>
  <div class="page assets-page" :style="{ '--asset-accent': meta.color, '--asset-soft': meta.softColor }">
    <header class="page-heading assets-heading">
      <div class="heading-icon"><AssetIcon :type="assetType" :size="27" /></div>
      <div class="assets-heading-copy">
        <p class="eyebrow">{{ pageEyebrow(`CATALOGUE · ${meta.english.toUpperCase()}`) }}</p>
        <h1>{{ meta.label }}目录</h1>
        <p>{{ meta.description }}。所有内容均来自实验室受控存储根。</p>
      </div>
      <div class="assets-heading-actions">
        <button v-if="publicationCatalogue" class="button button--outline" :disabled="exportingCitations || !data?.total" @click="downloadFilteredCitations"><Download :size="16" />{{ exportingCitations ? '正在导出' : '导出 BibTeX' }}</button>
        <button class="button button--primary" @click="openRegistration"><Plus :size="16" />登记{{ meta.label }}</button>
      </div>
    </header>

    <section class="catalogue-toolbar">
      <label class="catalogue-search">
        <Search :size="19" />
        <input v-model="query" :aria-label="`搜索${meta.label}`" :placeholder="`搜索${meta.label}标题、摘要或关键词`" @input="updateSearch" />
        <span v-if="loading" class="tiny-spinner"></span>
      </label>
      <div class="asset-filters">
        <button
          id="catalogue-filter-trigger"
          ref="filterTrigger"
          type="button"
          class="filter-button"
          :class="{ active: filtersOpen || activeFilterCount }"
          :aria-expanded="filtersOpen"
          aria-controls="catalogue-filter-popover"
          aria-haspopup="true"
          @click="filtersOpen = !filtersOpen"
        >
          <SlidersHorizontal :size="17" /> 筛选条件 <span>{{ activeFilterCount }}</span>
        </button>
        <section
          v-if="filtersOpen"
          id="catalogue-filter-popover"
          ref="filterPopover"
          class="filter-popover"
          role="group"
          aria-labelledby="catalogue-filter-trigger"
        >
          <label>
            状态
            <select v-model="filters.status" @change="applyFilters">
              <option value="">全部状态</option>
              <option value="draft">draft</option>
              <option value="active">active</option>
              <option value="available">available</option>
              <option value="collected">collected</option>
              <option v-if="publicationCatalogue" value="published">published</option>
            </select>
          </label>
          <label v-if="publicationCatalogue">
            发表来源
            <select v-model="filters.venue" @change="applyFilters">
              <option value="">全部来源</option>
              <option v-for="venue in data?.publication_facets?.venues" :key="venue" :value="venue">{{ venue }}</option>
            </select>
          </label>
          <label v-if="publicationCatalogue">
            发表年份
            <select v-model="filters.year" @change="applyFilters">
              <option value="">全部年份</option>
              <option v-for="year in data?.publication_facets?.years" :key="year" :value="String(year)">{{ year }}</option>
            </select>
          </label>
          <label>
            可见范围
            <select v-model="filters.visibility" @change="applyFilters">
              <option value="">全部范围</option>
              <option value="lab">全实验室</option>
              <option value="project">项目成员</option>
              <option value="restricted">受限</option>
            </select>
          </label>
          <label>
            数据状态
            <select v-model="filters.hasFiles" @change="applyFilters">
              <option value="">全部</option>
              <option value="present">已有数据</option>
              <option value="missing">暂无数据</option>
            </select>
          </label>
          <button v-if="activeFilterCount" type="button" class="filter-clear" @click="clearFilters">清除筛选</button>
        </section>
      </div>
      <div class="view-switch" aria-label="视图切换">
        <button :class="{ active: view === 'list' }" :aria-pressed="view === 'list'" aria-label="列表视图" @click="changeView('list')"><List :size="18" /></button>
        <button :class="{ active: view === 'grid' }" :aria-pressed="view === 'grid'" aria-label="卡片视图" @click="changeView('grid')"><Grid2X2 :size="17" /></button>
      </div>
    </section>

    <div class="catalogue-summary">
      <p><strong>{{ data?.total ?? 0 }}</strong> 项{{ meta.label }}资产</p>
      <span>按最近更新时间排序</span>
    </div>
    <p v-if="citationError" class="catalogue-message catalogue-message--error" role="alert">{{ citationError }}</p>

    <div v-if="error" class="state-panel state-panel--error state-panel--inline" role="alert">
      <strong>目录读取失败</strong><p>{{ error }}</p><button class="button button--outline" @click="load">重试</button>
    </div>

    <div v-else-if="!loading && data?.items.length === 0" class="empty-catalogue">
      <span><AssetIcon :type="assetType" :size="32" /></span>
      <h2>尚未找到{{ meta.label }}</h2>
      <p>{{ catalogueHasConstraints ? '当前搜索和筛选条件没有匹配结果。' : `登记第一项${meta.label}，开始建立实验室共同目录。` }}</p>
      <button v-if="catalogueHasConstraints" class="button button--outline" type="button" @click="clearCatalogueConstraints">清除搜索与筛选</button>
    </div>

    <section v-else class="catalogue-results" :class="{ 'catalogue-results--grid': view === 'grid' }">
      <article v-for="asset in data?.items" :key="asset.id" class="catalogue-card">
        <div class="catalogue-card-icon"><AssetIcon :type="asset.type" :size="22" /></div>
        <div class="catalogue-card-copy">
          <div class="catalogue-title-line">
            <h2>
              <RouterLink :to="{ name: 'asset-detail', params: { assetId: asset.id }, query: { returnTo: route.fullPath } }">
                {{ asset.title }}
              </RouterLink>
            </h2>
            <span class="status-badge">{{ asset.status }}</span>
          </div>
          <p>{{ asset.summary }}</p>
          <div v-if="publicationMetadata(asset)" class="publication-byline">{{ authorText(publicationMetadata(asset)!) }}</div>
          <div class="tag-list"><span v-for="tag in asset.tags" :key="tag">{{ tag }}</span></div>
          <div class="catalogue-card-meta">
            <span>
              <ShieldCheck v-if="asset.visibility === 'lab'" :size="14" />
              <LockKeyhole v-else :size="14" />
              {{ asset.visibility === 'lab' ? '全实验室可见' : asset.visibility === 'project' ? '项目成员可见' : '受限资产' }}
            </span>
            <span v-if="detailText(asset.details)">{{ detailText(asset.details) }}</span>
          </div>
        </div>
        <dl class="catalogue-facts">
          <div><dt>{{ publicationMetadata(asset) ? '发表来源' : '当前版本' }}</dt><dd>{{ publicationMetadata(asset) ? `${publicationMetadata(asset)!.venue} ${publicationMetadata(asset)!.year}` : asset.current_version ?? '—' }}</dd></div>
          <div><dt>文件规模</dt><dd>{{ formatBytes(asset.total_size) }}</dd></div>
          <div><dt>数据状态</dt><dd><span class="data-status" :class="{ 'data-status--present': asset.file_count > 0 }"><Database :size="13" />{{ asset.file_count > 0 ? `已有数据 · ${asset.file_count} 个文件` : '暂无数据' }}</span></dd></div>
          <div><dt>负责人</dt><dd><span class="mini-avatar">{{ asset.owner.name.slice(0, 1) }}</span>{{ asset.owner.name }}</dd></div>
          <div><dt>更新日期</dt><dd>{{ formatDate(asset.updated_at) }}</dd></div>
        </dl>
        <div class="catalogue-actions">
          <a v-if="publicationMetadata(asset)" :href="publicationMetadata(asset)!.source_url" target="_blank" rel="noreferrer"><ExternalLink :size="17" /><span>官方页面</span></a>
          <button v-if="publicationMetadata(asset)" title="复制这篇出版物的 BibTeX 引用" :disabled="citationActionId === asset.id" @click="copyPublicationCitation(asset)"><Check v-if="citationCopiedId === asset.id" :size="17" /><Copy v-else :size="17" /><span>{{ citationCopiedId === asset.id ? '已复制' : 'BibTeX' }}</span></button>
          <button title="向此资产安全上传文件" @click="openUpload(asset)"><FolderUp :size="18" /><span>上传文件</span></button>
          <RouterLink class="action-primary" :to="{ name: 'asset-detail', params: { assetId: asset.id }, query: { returnTo: route.fullPath } }">查看详情 <ArrowRight :size="16" /></RouterLink>
        </div>
      </article>
    </section>

    <footer v-if="data && data.total > 20" class="pagination">
      <button :disabled="page === 1" @click="changePage(page - 1)"><ArrowLeft :size="16" /> 上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页</span>
      <button :disabled="page === totalPages" @click="changePage(page + 1)">下一页 <ArrowRight :size="16" /></button>
    </footer>
    <div v-if="registrationOpen" class="registration-backdrop" @click.self="closeRegistration">
      <form ref="registrationDialog" class="registration-dialog registration-dialog--asset" role="dialog" aria-modal="true" aria-labelledby="registration-title" @submit.prevent="registerAsset">
        <button class="registration-close" type="button" aria-label="关闭" :disabled="creating" @click="closeRegistration"><X :size="18" /></button>
        <header class="registration-header">
          <p class="eyebrow">NEW {{ meta.english.toUpperCase() }}</p>
          <h2 id="registration-title">登记{{ meta.label }}</h2>
          <p class="registration-note">登记后可继续在详情页维护版本、关联关系与归档文件。</p>
        </header>

        <div class="registration-body">
          <label>标题<input v-model="registration.title" required autofocus maxlength="500" placeholder="例如：田野样本观测数据集" /></label>
          <label>资产标识（slug）<input v-model="registration.slug" required pattern="[a-z0-9]+(-[a-z0-9]+)*" minlength="3" maxlength="160" placeholder="例如：soil-samples-2026" /></label>
          <label>{{ assetType === 'literature' ? '摘要（必填）' : '摘要' }}<textarea v-model="registration.summary" :required="assetType === 'literature'" maxlength="5000" rows="3" placeholder="简要说明研究内容、范围或用途"></textarea></label>
          <div class="registration-grid">
            <label>状态<select v-model="registration.status"><option value="draft">draft</option><option value="active">active</option><option value="available">available</option><option value="collected">collected</option><option v-if="publicationCatalogue" value="published">published</option></select></label>
            <label>可见范围<select v-model="registration.visibility"><option value="lab">全实验室</option><option value="project">项目成员</option><option value="restricted">受限</option></select></label>
          </div>
          <template v-if="publicationCatalogue">
            <div class="registration-grid">
              <label>{{ assetType === 'literature' ? '来源或期刊' : '会议' }}<input v-model="registration.venue" required maxlength="80" :placeholder="assetType === 'literature' ? '例如：Nature 或 arXiv' : '例如：ICLR'" /></label>
              <label>年份<input v-model="registration.year" required type="number" min="1900" max="2200" /></label>
            </div>
            <label>{{ assetType === 'literature' ? '文献类别' : '会议类别' }}<input v-model="registration.track" required maxlength="120" :placeholder="assetType === 'literature' ? '例如：Journal Article' : '例如：Conference Poster'" /></label>
            <label>作者（逗号分隔）<input v-model="registration.authors" required placeholder="例如：Pan Lu, Bowen Chen" /></label>
            <label>官方来源标识<input v-model="registration.sourceId" required maxlength="200" placeholder="例如：2026.acl-long.1" /></label>
            <label>官方页面 URL<input v-model="registration.sourceUrl" required type="url" placeholder="https://..." /></label>
            <label>官方 PDF URL<input v-model="registration.pdfUrl" required type="url" placeholder="https://...pdf" /></label>
            <div class="registration-grid">
              <label>引用类型<select v-model="registration.entryType"><option value="article">期刊文章（article）</option><option value="inproceedings">会议论文（inproceedings）</option><option value="proceedings">论文集（proceedings）</option><option value="misc">其他（misc）</option></select></label>
              <label>引用键（可选）<input v-model="registration.citationKey" pattern="[A-Za-z][A-Za-z0-9_:+.\-]*" maxlength="160" placeholder="例如：lu2026octotools" /></label>
            </div>
            <label v-if="registration.entryType === 'article'">期刊名称<input v-model="registration.journal" required maxlength="500" placeholder="例如：Nature Communications" /></label>
            <label v-else-if="['inproceedings', 'proceedings'].includes(registration.entryType ?? '')">论文集名称（可选）<input v-model="registration.booktitle" maxlength="500" placeholder="例如：Proceedings of ACL 2026" /></label>
            <label>页码（可选）<input v-model="registration.pages" maxlength="80" placeholder="例如：101--112" /></label>
            <label>出版社（可选）<input v-model="registration.publisher" maxlength="300" placeholder="例如：Association for Computational Linguistics" /></label>
          </template>
          <div class="registration-grid">
            <label>初始版本（可选）<input v-model="registration.version" maxlength="80" placeholder="例如：v0.1" /></label>
            <label>标签（逗号分隔）<input v-model="registration.tags" placeholder="例如：生态, 田野" /></label>
          </div>
        </div>
        <footer class="registration-footer">
          <p v-if="createError" class="registration-error" role="alert">{{ createError }}</p>
          <div class="registration-footer-actions"><button class="button button--outline" type="button" :disabled="creating" @click="closeRegistration">取消</button><button class="button button--primary" :disabled="creating || !registrationValid" type="submit"><Save :size="16" />{{ creating ? '正在登记' : '确认登记' }}</button></div>
        </footer>
      </form>
    </div>
    <div v-if="uploadAsset" class="registration-backdrop" @click.self="closeUpload">
      <form ref="uploadDialog" class="registration-dialog upload-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-title" @submit.prevent="uploadPhase === 'configure' ? generateUploadCommand() : finalizeCurrentUpload()">
        <button class="registration-close" type="button" aria-label="关闭" :disabled="uploadBusy" @click="closeUpload"><X :size="18" /></button>
        <p class="eyebrow">SECURE UPLOAD · {{ meta.english.toUpperCase() }}</p>
        <h2 id="upload-title">上传到「{{ uploadAsset.title }}」</h2>
        <ol class="upload-steps" aria-label="上传进度">
          <li :class="{ active: uploadPhase === 'configure', complete: uploadPhase !== 'configure' }"><span>1</span>配置传输</li>
          <li :class="{ active: uploadPhase === 'transfer', complete: uploadPhase === 'success' }"><span>2</span>检测入库</li>
          <li :class="{ active: uploadPhase === 'success' }"><span>3</span>完成</li>
        </ol>

        <template v-if="uploadPhase === 'configure'">
          <p class="registration-note">填写保存文件的电脑上的路径。网站只生成终端命令，不会读取这台电脑上的文件。</p>
          <label>本机待上传路径<input v-model="upload.sourcePath" required autofocus placeholder="例如：/mnt/research/soil-samples.csv" /></label>
          <fieldset class="upload-source-kind">
            <legend>上传内容</legend>
            <button type="button" :class="{ active: !upload.recursive }" @click="upload.recursive = false"><File :size="15" />单个文件</button>
            <button type="button" :class="{ active: upload.recursive }" @click="upload.recursive = true"><FolderUp :size="15" />整个目录</button>
          </fieldset>
          <div class="registration-grid">
            <label>归档一级目录<select v-model="upload.directory" required><option v-for="folder in currentUploadFolders" :key="folder.name" :value="folder.name">{{ folder.name }} · {{ folder.label }}</option></select></label>
            <label>目录内细分路径（可选）<input v-model="upload.nestedPath" placeholder="例如：2026-08 或 experiment-a" /></label>
          </div>
          <div class="upload-target-preview"><span>入库位置</span><code>{{ uploadTargetPath }}</code></div>
          <p v-if="uploadError" class="registration-error" role="alert">{{ uploadError }}</p>
          <footer><button class="button button--outline" type="button" :disabled="uploadGenerating" @click="closeUpload">取消</button><button class="button button--primary" :disabled="uploadGenerating || !upload.sourcePath.trim() || !upload.directory" type="submit"><LoaderCircle v-if="uploadGenerating" class="spin" :size="16" /><FolderUp v-else :size="16" />{{ uploadGenerating ? '正在生成' : '生成上传命令' }}</button></footer>
        </template>

        <template v-else-if="uploadPhase === 'transfer' && uploadResult">
          <p class="registration-note">复制并在保存文件的电脑终端执行命令。传输结束后回到这里检测，系统会先校验全部路径，再一次性入库并建立索引。</p>
          <section class="upload-command-result">
            <header><div><strong>终端上传命令</strong><small>最终位置：{{ uploadResult.archive_relative_path }}</small></div><button class="button button--outline" type="button" @click="copyUploadCommand"><Check v-if="uploadCopied" :size="16" /><Copy v-else :size="16" />{{ uploadCopied ? '已复制' : '复制' }}</button></header>
            <pre><code>{{ uploadResult.command }}</code></pre>
          </section>
          <div class="upload-live-status" :class="{ 'upload-live-status--ready': ['ready', 'completed'].includes(uploadStatus?.status ?? '') }" role="status">
            <Check v-if="['ready', 'completed'].includes(uploadStatus?.status ?? '')" :size="17" />
            <LoaderCircle v-else :class="{ spin: uploadStatusChecking }" :size="17" />
            <p v-if="uploadStatus?.status === 'completed'"><strong>任务已经完成入库</strong><span>点击下方按钮同步本次入库结果，无需重新上传文件。</span></p>
            <p v-else-if="uploadStatus?.status === 'ready'"><strong>终端传输已完成</strong><span>检测到 {{ uploadStatus.uploaded_file_count }} 个文件 · {{ formatBytes(uploadStatus.total_size) }}，可以执行校验入库。</span></p>
            <p v-else-if="uploadStatus?.uploaded_file_count"><strong>正在接收文件</strong><span>已检测到 {{ uploadStatus.uploaded_file_count }} 个文件 · {{ formatBytes(uploadStatus.total_size) }}，等待终端命令完成。</span></p>
            <p v-else><strong>等待终端传输</strong><span>页面每 2 秒自动检测临时区，无需反复点击入库按钮。</span></p>
          </div>
          <div class="upload-safety-note"><ShieldCheck :size="17" /><p><strong>正式归档受保护</strong><span>传输文件暂存在隔离区；系统计算 SHA-256，并在重名、重复内容、符号链接或异常内容出现时回滚整批入库。</span></p></div>
          <p v-if="uploadStatusError" class="registration-error upload-error-block" role="alert">{{ uploadStatusError }}</p>
          <p v-if="uploadError" class="registration-error upload-error-block" role="alert">{{ uploadError }}</p>
          <footer><button class="button button--outline" type="button" :disabled="uploadFinalizing" @click="reconfigureUpload">重新配置</button><button class="button button--primary" :disabled="uploadFinalizing" type="submit"><LoaderCircle v-if="uploadFinalizing" class="spin" :size="16" /><ShieldCheck v-else :size="16" />{{ uploadFinalizing ? '正在检测临时区' : '检测并入库' }}</button></footer>
        </template>

        <template v-else-if="uploadPhase === 'success' && uploadFinalizeResult">
          <section class="upload-success" role="status">
            <span><Check :size="24" /></span>
            <div><strong>文件已完成入库</strong><p>已索引 {{ uploadFinalizeResult.imported_file_count }} 个文件 · {{ formatBytes(uploadFinalizeResult.total_size) }}</p></div>
          </section>
          <div class="upload-imported-paths"><span>已写入并校验</span><div v-for="path in uploadFinalizeResult.relative_paths.slice(0, 4)" :key="path" class="upload-imported-file"><code>{{ path }}</code><small v-if="uploadFinalizeResult.checksums[path]">SHA-256 · {{ uploadFinalizeResult.checksums[path].slice(0, 16) }}…</small></div><small v-if="uploadFinalizeResult.relative_paths.length > 4">另有 {{ uploadFinalizeResult.relative_paths.length - 4 }} 个文件</small></div>
          <p v-if="uploadRefreshError" class="registration-error upload-error-block" role="alert">{{ uploadRefreshError }}</p>
          <footer><button class="button button--primary" type="button" @click="closeUpload"><Check :size="16" />完成</button></footer>
        </template>
      </form>
    </div>

  </div>
</template>

<style scoped>
.registration-backdrop { position: fixed; z-index: 40; inset: 0; display: grid; padding: 20px; place-items: center; background: rgba(23, 34, 26, .48); }
.registration-dialog { position: relative; display: grid; width: min(100%, 610px); max-height: calc(100vh - 40px); padding: 28px; overflow-y: auto; background: #fdfefb; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 20px 50px rgba(24, 37, 29, .22); gap: 11px; }.registration-dialog--asset { grid-template-rows: auto minmax(0, 1fr) auto; padding: 0; overflow: hidden; gap: 0; }.registration-header { display: grid; padding: 26px 28px 15px; gap: 5px; border-bottom: 1px solid var(--line); }.registration-body { display: grid; min-height: 0; padding: 18px 28px; overflow-y: auto; gap: 11px; }.registration-dialog h2 { margin: -5px 0 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 24px; font-weight: 500; }.registration-note { margin: -3px 0 7px; color: var(--muted); font-size: 12px; line-height: 1.55; }.registration-dialog label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.registration-dialog input, .registration-dialog textarea, .registration-dialog select { width: 100%; min-width: 0; padding: 9px 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.registration-dialog textarea { resize: vertical; }.registration-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.registration-close { position: absolute; z-index: 1; top: 13px; right: 13px; display: grid; width: 31px; height: 31px; color: #68776d; place-items: center; background: transparent; border: 0; border-radius: 50%; cursor: pointer; }.registration-close:hover { background: #eef2ed; }.registration-error { margin: 1px 0; color: #a6633b; font-size: 12px; }.registration-dialog footer { display: flex; margin-top: 9px; justify-content: flex-end; gap: 9px; }.registration-footer { display: grid !important; margin: 0 !important; padding: 12px 28px 16px; border-top: 1px solid var(--line); gap: 8px !important; }.registration-footer .registration-error { padding: 8px 10px; background: #fff6f0; border-left: 3px solid #bd7750; }.registration-footer-actions { display: flex; justify-content: flex-end; gap: 9px; } @media (max-width: 560px) { .registration-dialog { padding: 24px 20px; }.registration-dialog--asset { width: 100%; max-height: calc(100dvh - 24px); padding: 0; }.registration-header { padding: 22px 20px 13px; }.registration-body { padding: 16px 20px; }.registration-footer { padding: 11px 20px 14px; }.registration-footer-actions .button { min-width: 0; flex: 1; justify-content: center; }.registration-grid { grid-template-columns: 1fr; } }
.data-status { display: inline-flex; color: #89968e; align-items: center; gap: 4px; font-family: inherit; font-size: 11px; }.data-status--present { color: var(--asset-accent); }.upload-dialog { width: min(100%, 720px); }.upload-steps { display: grid; margin: 3px 0 7px; padding: 0; grid-template-columns: repeat(3, 1fr); list-style: none; border-top: 1px solid var(--line); }.upload-steps li { display: flex; padding-top: 10px; color: #98a29b; align-items: center; font-size: 11px; gap: 6px; }.upload-steps li + li { justify-content: center; }.upload-steps li:last-child { justify-content: flex-end; }.upload-steps span { display: grid; width: 20px; height: 20px; place-items: center; border: 1px solid #cfd7d1; border-radius: 50%; font-size: 10px; }.upload-steps .active { color: var(--ink); font-weight: 700; }.upload-steps .active span { color: #fff; background: var(--sage); border-color: var(--sage); }.upload-steps .complete { color: var(--sage); }.upload-steps .complete span { color: var(--sage); background: #e8f0e9; border-color: #aebfb1; }.upload-source-kind { display: grid; margin: 0; padding: 0; grid-template-columns: 1fr 1fr; border: 1px solid var(--line); border-radius: 5px; overflow: hidden; }.upload-source-kind legend { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); }.upload-source-kind button { display: flex; min-height: 38px; color: #657168; align-items: center; justify-content: center; background: #fff; border: 0; cursor: pointer; gap: 7px; }.upload-source-kind button + button { border-left: 1px solid var(--line); }.upload-source-kind button.active { color: #24452f; font-weight: 700; background: #edf3ed; box-shadow: inset 0 0 0 1px #aec0b1; }.upload-target-preview { display: grid; padding: 10px 12px; background: #f3f6f2; border: 1px solid #dce4dc; border-radius: 5px; gap: 4px; }.upload-target-preview span, .upload-imported-paths > span { color: var(--muted); font-size: 10px; font-weight: 700; text-transform: uppercase; }.upload-live-status { display: flex; padding: 11px 12px; color: #66746b; align-items: flex-start; background: #f5f6f3; border: 1px solid #dce2dc; border-radius: 5px; gap: 9px; }.upload-live-status--ready { color: #3f6d49; background: #edf5ec; border-color: #c9ddca; }.upload-live-status svg { margin-top: 1px; flex: 0 0 auto; }.upload-live-status p { display: grid; margin: 0; gap: 2px; }.upload-live-status strong { color: inherit; font-size: 11px; }.upload-live-status span { color: #748078; font-size: 11px; line-height: 1.5; }.upload-target-preview code { overflow-wrap: anywhere; color: #36533e; font-size: 12px; }.upload-command-result { display: grid; margin-top: 2px; gap: 10px; }.upload-command-result header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.upload-command-result header div { display: grid; gap: 3px; }.upload-command-result strong { font-size: 13px; }.upload-command-result small { color: var(--muted); font-size: 11px; line-height: 1.55; }.upload-command-result pre { margin: 0; padding: 13px; overflow-x: auto; color: #dfeade; background: #17221b; border-radius: 6px; font-size: 11px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }.upload-command-result code { color: inherit; }.upload-safety-note { display: flex; padding: 11px 12px; color: #36513e; align-items: flex-start; background: #eff4ef; border-left: 3px solid #78937c; gap: 9px; }.upload-safety-note svg { margin-top: 1px; flex: 0 0 auto; }.upload-safety-note p { display: grid; margin: 0; gap: 2px; }.upload-safety-note strong { font-size: 11px; }.upload-safety-note span { color: #68766c; font-size: 11px; line-height: 1.5; }.upload-error-block { padding: 10px 12px; white-space: pre-line; background: #fff6f0; border-left: 3px solid #bd7750; }.upload-success { display: flex; padding: 18px; align-items: center; background: #eff5ef; border: 1px solid #cddccd; border-radius: 6px; gap: 13px; }.upload-success > span { display: grid; width: 40px; height: 40px; color: #fff; place-items: center; background: #52745b; border-radius: 50%; }.upload-success div { display: grid; gap: 4px; }.upload-success strong { font-size: 15px; }.upload-success p { margin: 0; color: #627069; font-size: 12px; }.upload-imported-paths { display: grid; padding: 12px; background: #f7f8f5; border: 1px solid var(--line); border-radius: 5px; gap: 5px; }.upload-imported-paths code { overflow-wrap: anywhere; color: #4f5e54; font-size: 11px; }.upload-imported-paths small { color: var(--muted); font-size: 10px; }.upload-imported-file { display: grid; gap: 2px; }.upload-imported-file + .upload-imported-file { padding-top: 5px; border-top: 1px solid #e3e7e1; }.spin { animation: spin .8s linear infinite; } @keyframes spin { to { transform: rotate(360deg); } } @media (max-width: 560px) { .upload-dialog { max-height: calc(100dvh - 24px); }.upload-steps li { font-size: 10px; }.upload-command-result header { align-items: flex-start; }.upload-command-result header div { min-width: 0; }.upload-command-result small { overflow-wrap: anywhere; }.upload-dialog footer { position: sticky; bottom: -24px; margin-right: -20px; margin-left: -20px; padding: 12px 20px 0; background: #fdfefb; border-top: 1px solid var(--line); } }
</style>
