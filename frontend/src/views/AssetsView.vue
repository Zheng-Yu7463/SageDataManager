<script setup lang="ts">
import { useDebounceFn } from '@vueuse/core'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Copy,
  Database,
  Download,
  FolderUp,
  ExternalLink,
  Grid2X2,
  List,
  LockKeyhole,
  Plus,
  Save,
  X,
  Search,
  ShieldCheck,
  SlidersHorizontal,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AssetIcon from '@/components/AssetIcon.vue'
import { createAsset, exportPaperCitations, getAssets, getPaperCitation, getUploadCommand } from '@/api/client'
import { assetMeta } from '@/catalogue'
import { useOverlayFocus } from '@/composables/useOverlayFocus'
import { useDismissiblePopover } from '@/composables/useDismissiblePopover'
import { useBranding } from '@/composables/useBranding'
import { isPaperMetadata } from '@/types'
import type { AssetListResponse, AssetSummary, AssetType, PaperMetadata, UploadCommandResult, Visibility } from '@/types'
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

const assetType = computed(() => route.meta.assetType as AssetType)
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
  booktitle: '',
  pages: '',
  publisher: '',
})
const citationActionId = ref<string | null>(null)
const citationCopiedId = ref<string | null>(null)
const citationError = ref('')
const exportingCitations = ref(false)
const uploadAsset = ref<AssetSummary | null>(null)
const uploadDialog = ref<HTMLElement | null>(null)
const uploadGenerating = ref(false)
const uploadError = ref('')
const uploadCopied = ref(false)
const uploadResult = ref<UploadCommandResult | null>(null)
const upload = ref({
  sourcePath: '',
  directory: '',
  nestedPath: '',
  recursive: false,
})

const activeFilterCount = computed(() => Object.values(filters.value).filter(Boolean).length)
const meta = computed(() => assetMeta[assetType.value])
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / 20)))
const currentUploadFolders = computed(() => uploadAsset.value?.upload_directories ?? [])
const uploadOpen = computed(() => Boolean(uploadAsset.value))
const registrationValid = computed(() => {
  const baseValid = Boolean(
    registration.value.title.trim()
    && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(registration.value.slug.trim())
    && registration.value.slug.trim().length >= 3,
  )
  if (!baseValid || assetType.value !== 'paper') return baseValid

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
    && (
      !registration.value.citationKey.trim()
      || /^[A-Za-z][A-Za-z0-9_:+.-]*$/.test(registration.value.citationKey.trim())
    ),
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
    venue: assetType.value === 'paper' ? queryValue('venue') : '',
    year: assetType.value === 'paper' && /^\d{4}$/.test(queryValue('year')) ? queryValue('year') : '',
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
  if (assetType.value === 'paper' && filters.value.venue) next.venue = filters.value.venue
  if (assetType.value === 'paper' && filters.value.year) next.year = filters.value.year
  if (page.value > 1) next.page = String(page.value)
  if (view.value === 'grid') next.view = 'grid'
  return next
}

function syncCatalogueRoute() {
  void router.replace({ query: catalogueQuery() })
}

async function load() {
  controller?.abort()
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
        venue: assetType.value === 'paper' ? filters.value.venue : undefined,
        year: assetType.value === 'paper' && filters.value.year ? Number(filters.value.year) : undefined,
        page: page.value,
        pageSize: 20,
      },
      requestController.signal,
    )
    if (controller !== requestController) return
    data.value = result
    const lastPage = Math.max(1, Math.ceil(result.total / 20))
    if (page.value > lastPage) {
      page.value = lastPage
      syncCatalogueRoute()
    }
  } catch (reason) {
    if (controller !== requestController) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    error.value = reason instanceof Error ? reason.message : '无法读取资产目录'
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
  if (isPaperMetadata(details)) return `${details.venue} ${details.year} · ${details.track}`
  const entries = Object.entries(details).filter(([, value]) => typeof value !== 'object').slice(0, 2)
  return entries.map(([key, value]) => `${key.replace('_', ' ')} · ${value}`).join('  /  ')
}

function paperMetadata(asset: AssetSummary): PaperMetadata | null {
  return asset.type === 'paper' && isPaperMetadata(asset.details) ? asset.details : null
}

function authorText(metadata: PaperMetadata) {
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
    booktitle: '',
    pages: '',
    publisher: '',
  }
  createError.value = ''
  registrationOpen.value = true
}

function closeRegistration() {
  if (!creating.value) registrationOpen.value = false
}

async function registerAsset() {
  if (!registrationValid.value) return
  creating.value = true
  createError.value = ''
  try {
    await createAsset({
      type: assetType.value,
      title: registration.value.title.trim(),
      slug: registration.value.slug.trim(),
      summary: registration.value.summary.trim(),
      status: registration.value.status.trim() || 'draft',
      visibility: registration.value.visibility,
      version: registration.value.version.trim() || null,
      tags: registration.value.tags.split(/[，,]/).map((tag) => tag.trim()).filter(Boolean),
      details: assetType.value === 'paper' ? {
        venue: registration.value.venue.trim(),
        year: Number(registration.value.year),
        track: registration.value.track.trim(),
        authors: registration.value.authors.split(/[，,]/).map((author) => author.trim()).filter(Boolean),
        source_id: registration.value.sourceId.trim(),
        source_url: registration.value.sourceUrl.trim(),
        pdf_url: registration.value.pdfUrl.trim(),
        ...(registration.value.citationKey.trim() ? { citation_key: registration.value.citationKey.trim() } : {}),
        ...(registration.value.booktitle.trim() ? { booktitle: registration.value.booktitle.trim() } : {}),
        ...(registration.value.pages.trim() ? { pages: registration.value.pages.trim() } : {}),
        ...(registration.value.publisher.trim() ? { publisher: registration.value.publisher.trim() } : {}),
      } : {},
    })
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

function openUpload(asset: AssetSummary) {
  filtersOpen.value = false
  uploadAsset.value = asset
  upload.value = {
    sourcePath: '',
    directory: asset.default_upload_directory,
    nestedPath: '',
    recursive: false,
  }
  uploadError.value = ''
  uploadCopied.value = false
  uploadResult.value = null
}

function closeUpload() {
  if (!uploadGenerating.value) uploadAsset.value = null
}

async function generateUploadCommand() {
  if (!uploadAsset.value || !upload.value.sourcePath.trim()) return
  uploadGenerating.value = true
  uploadError.value = ''
  uploadCopied.value = false
  try {
    uploadResult.value = await getUploadCommand({
      asset_id: uploadAsset.value.id,
      source_path: upload.value.sourcePath.trim(),
      target_subdirectory: [upload.value.directory, upload.value.nestedPath.trim()].filter(Boolean).join('/'),
      recursive: upload.value.recursive,
    })
  } catch (reason) {
    uploadError.value = reason instanceof Error ? reason.message : '无法生成上传命令'
  } finally {
    uploadGenerating.value = false
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

async function copyPaperCitation(asset: AssetSummary) {
  citationActionId.value = asset.id
  citationError.value = ''
  try {
    const citation = await getPaperCitation(asset.id)
    await copyText(citation.bibtex)
    citationCopiedId.value = asset.id
    window.setTimeout(() => {
      if (citationCopiedId.value === asset.id) citationCopiedId.value = null
    }, 1800)
  } catch (reason) {
    citationError.value = reason instanceof Error ? reason.message : '无法复制 BibTeX'
  } finally {
    citationActionId.value = null
  }
}

async function downloadFilteredCitations() {
  exportingCitations.value = true
  citationError.value = ''
  try {
    const result = await exportPaperCitations({
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
  () => {
    readCatalogueState()
    void load()
  },
  { immediate: true },
)
onBeforeUnmount(() => controller?.abort())
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
        <button v-if="assetType === 'paper'" class="button button--outline" :disabled="exportingCitations || !data?.total" @click="downloadFilteredCitations"><Download :size="16" />{{ exportingCitations ? '正在导出' : '导出 BibTeX' }}</button>
        <button class="button button--primary" @click="openRegistration"><Plus :size="16" />登记{{ meta.label }}</button>
      </div>
    </header>

    <section class="catalogue-toolbar">
      <label class="catalogue-search">
        <Search :size="19" />
        <input v-model="query" :placeholder="`搜索${meta.label}标题、摘要或关键词`" @input="updateSearch" />
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
              <option v-if="assetType === 'paper'" value="published">published</option>
            </select>
          </label>
          <label v-if="assetType === 'paper'">
            收录会议
            <select v-model="filters.venue" @change="applyFilters">
              <option value="">全部会议</option>
              <option v-for="venue in data?.paper_facets?.venues" :key="venue" :value="venue">{{ venue }}</option>
            </select>
          </label>
          <label v-if="assetType === 'paper'">
            发表年份
            <select v-model="filters.year" @change="applyFilters">
              <option value="">全部年份</option>
              <option v-for="year in data?.paper_facets?.years" :key="year" :value="String(year)">{{ year }}</option>
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
      <p>{{ query ? '尝试减少关键词，或清空搜索条件。' : `登记第一项${meta.label}，开始建立实验室共同目录。` }}</p>
    </div>

    <section v-else class="catalogue-results" :class="{ 'catalogue-results--grid': view === 'grid' }">
      <article v-for="asset in data?.items" :key="asset.id" class="catalogue-card">
        <div class="catalogue-card-icon"><AssetIcon :type="asset.type" :size="22" /></div>
        <div class="catalogue-card-copy">
          <div class="catalogue-title-line">
            <h2>{{ asset.title }}</h2>
            <span class="status-badge">{{ asset.status }}</span>
          </div>
          <p>{{ asset.summary }}</p>
          <div v-if="paperMetadata(asset)" class="paper-byline">{{ authorText(paperMetadata(asset)!) }}</div>
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
          <div><dt>{{ paperMetadata(asset) ? '会议收录' : '当前版本' }}</dt><dd>{{ paperMetadata(asset) ? `${paperMetadata(asset)!.venue} ${paperMetadata(asset)!.year}` : asset.current_version ?? '—' }}</dd></div>
          <div><dt>文件规模</dt><dd>{{ formatBytes(asset.total_size) }}</dd></div>
          <div><dt>数据状态</dt><dd><span class="data-status" :class="{ 'data-status--present': asset.file_count > 0 }"><Database :size="13" />{{ asset.file_count > 0 ? `已有数据 · ${asset.file_count} 个文件` : '暂无数据' }}</span></dd></div>
          <div><dt>负责人</dt><dd><span class="mini-avatar">{{ asset.owner.name.slice(0, 1) }}</span>{{ asset.owner.name }}</dd></div>
          <div><dt>更新日期</dt><dd>{{ formatDate(asset.updated_at) }}</dd></div>
        </dl>
        <div class="catalogue-actions">
          <a v-if="paperMetadata(asset)" :href="paperMetadata(asset)!.source_url" target="_blank" rel="noreferrer"><ExternalLink :size="17" /><span>官方页面</span></a>
          <button v-if="paperMetadata(asset)" title="复制这篇论文的 BibTeX 引用" :disabled="citationActionId === asset.id" @click="copyPaperCitation(asset)"><Check v-if="citationCopiedId === asset.id" :size="17" /><Copy v-else :size="17" /><span>{{ citationCopiedId === asset.id ? '已复制' : 'BibTeX' }}</span></button>
          <button title="获取此资产的 SCP 上传指令" @click="openUpload(asset)"><FolderUp :size="18" /><span>上传指令</span></button>
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
      <form ref="registrationDialog" class="registration-dialog" role="dialog" aria-modal="true" aria-labelledby="registration-title" @submit.prevent="registerAsset">
        <button class="registration-close" type="button" aria-label="关闭" :disabled="creating" @click="closeRegistration"><X :size="18" /></button>
        <p class="eyebrow">NEW {{ meta.english.toUpperCase() }}</p>
        <h2 id="registration-title">登记{{ meta.label }}</h2>
        <p class="registration-note">登记后可继续在详情页维护版本、关联关系与归档文件。</p>

        <label>标题<input v-model="registration.title" required autofocus maxlength="500" placeholder="例如：田野样本观测数据集" /></label>
        <label>资产标识（slug）<input v-model="registration.slug" required pattern="[a-z0-9]+(-[a-z0-9]+)*" minlength="3" maxlength="160" placeholder="例如：soil-samples-2026" /></label>
        <label>摘要<textarea v-model="registration.summary" maxlength="5000" rows="3" placeholder="简要说明研究内容、范围或用途"></textarea></label>
        <div class="registration-grid">
          <label>状态<select v-model="registration.status"><option value="draft">draft</option><option value="active">active</option><option value="available">available</option><option value="collected">collected</option><option v-if="assetType === 'paper'" value="published">published</option></select></label>
          <label>可见范围<select v-model="registration.visibility"><option value="lab">全实验室</option><option value="project">项目成员</option><option value="restricted">受限</option></select></label>
        </div>
        <template v-if="assetType === 'paper'">
          <div class="registration-grid">
            <label>会议<input v-model="registration.venue" required maxlength="80" placeholder="例如：ICLR" /></label>
            <label>年份<input v-model="registration.year" required type="number" min="1900" max="2200" /></label>
          </div>
          <label>会议类别<input v-model="registration.track" required maxlength="120" placeholder="例如：Conference Poster" /></label>
          <label>作者（逗号分隔）<input v-model="registration.authors" required placeholder="例如：Pan Lu, Bowen Chen" /></label>
          <label>官方来源标识<input v-model="registration.sourceId" required maxlength="200" placeholder="例如：2026.acl-long.1" /></label>
          <label>官方页面 URL<input v-model="registration.sourceUrl" required type="url" placeholder="https://..." /></label>
          <label>官方 PDF URL<input v-model="registration.pdfUrl" required type="url" placeholder="https://...pdf" /></label>
          <div class="registration-grid">
            <label>引用键（可选）<input v-model="registration.citationKey" pattern="[A-Za-z][A-Za-z0-9_:+.\-]*" maxlength="160" placeholder="例如：lu2026octotools" /></label>
            <label>页码（可选）<input v-model="registration.pages" maxlength="80" placeholder="例如：101--112" /></label>
          </div>
          <label>论文集名称（可选）<input v-model="registration.booktitle" maxlength="500" placeholder="例如：Proceedings of ACL 2026" /></label>
          <label>出版社（可选）<input v-model="registration.publisher" maxlength="300" placeholder="例如：Association for Computational Linguistics" /></label>
        </template>
        <div class="registration-grid">
          <label>初始版本（可选）<input v-model="registration.version" maxlength="80" placeholder="例如：v0.1" /></label>
          <label>标签（逗号分隔）<input v-model="registration.tags" placeholder="例如：生态, 田野" /></label>
        </div>
        <p v-if="createError" class="registration-error" role="alert">{{ createError }}</p>
        <footer><button class="button button--outline" type="button" :disabled="creating" @click="closeRegistration">取消</button><button class="button button--primary" :disabled="creating || !registrationValid" type="submit"><Save :size="16" />{{ creating ? '正在登记' : '确认登记' }}</button></footer>
      </form>
    </div>
    <div v-if="uploadAsset" class="registration-backdrop" @click.self="closeUpload">
      <form ref="uploadDialog" class="registration-dialog upload-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-title" @submit.prevent="generateUploadCommand">
        <button class="registration-close" type="button" aria-label="关闭" :disabled="uploadGenerating" @click="closeUpload"><X :size="18" /></button>
        <p class="eyebrow">SCP UPLOAD · {{ meta.english.toUpperCase() }}</p>
        <h2 id="upload-title">上传到「{{ uploadAsset.title }}」</h2>
        <p class="registration-note">目标资产与一级归档目录均按资产类型固定。填写本机待上传文件或目录的路径，复制命令到该电脑终端执行。</p>
        <label>本机待上传文件或目录<input v-model="upload.sourcePath" required autofocus placeholder="例如：/mnt/research/soil-samples.csv" /></label>
        <label>归档一级目录<select v-model="upload.directory" required><option v-for="folder in currentUploadFolders" :key="folder.name" :value="folder.name">{{ folder.name }} · {{ folder.label }}</option></select></label>
        <label>目录内细分路径（可选）<input v-model="upload.nestedPath" placeholder="例如：2026-08 或 experiment-a" /></label>
        <label class="upload-recursive"><input v-model="upload.recursive" type="checkbox" /> 上传整个目录（添加 <code>-r</code>）</label>
        <p v-if="uploadError" class="registration-error" role="alert">{{ uploadError }}</p>
        <footer><button class="button button--outline" type="button" :disabled="uploadGenerating" @click="closeUpload">取消</button><button class="button button--primary" :disabled="uploadGenerating || !upload.sourcePath.trim() || !upload.directory" type="submit"><FolderUp :size="16" />{{ uploadGenerating ? '正在生成' : '生成 SCP 命令' }}</button></footer>
        <section v-if="uploadResult" class="upload-command-result">
          <header><div><strong>上传指令已生成</strong><small>归档目录：{{ uploadResult.archive_relative_path }}</small></div><button class="button button--outline" type="button" @click="copyUploadCommand"><Check v-if="uploadCopied" :size="16" /><Copy v-else :size="16" />{{ uploadCopied ? '已复制' : '复制' }}</button></header>
          <pre><code>{{ uploadResult.command }}</code></pre>
          <p>完成传输后，到“归档健康”运行扫描；成功索引后此资产会显示为“已有数据”。</p>
        </section>
      </form>
    </div>

  </div>
</template>

<style scoped>
.registration-backdrop { position: fixed; z-index: 40; inset: 0; display: grid; padding: 20px; place-items: center; background: rgba(23, 34, 26, .48); }
.registration-dialog { position: relative; display: grid; width: min(100%, 610px); max-height: calc(100vh - 40px); padding: 28px; overflow-y: auto; background: #fdfefb; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 20px 50px rgba(24, 37, 29, .22); gap: 11px; }.registration-dialog h2 { margin: -5px 0 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 24px; font-weight: 500; }.registration-note { margin: -3px 0 7px; color: var(--muted); font-size: 12px; line-height: 1.55; }.registration-dialog label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.registration-dialog input, .registration-dialog textarea, .registration-dialog select { width: 100%; padding: 9px 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.registration-dialog textarea { resize: vertical; }.registration-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.registration-close { position: absolute; top: 13px; right: 13px; display: grid; width: 31px; height: 31px; color: #68776d; place-items: center; background: transparent; border: 0; border-radius: 50%; cursor: pointer; }.registration-close:hover { background: #eef2ed; }.registration-error { margin: 1px 0; color: #a6633b; font-size: 12px; }.registration-dialog footer { display: flex; margin-top: 9px; justify-content: flex-end; gap: 9px; } @media (max-width: 560px) { .registration-dialog { padding: 24px 20px; }.registration-grid { grid-template-columns: 1fr; } }
.data-status { display: inline-flex; color: #89968e; align-items: center; gap: 4px; font-family: inherit; font-size: 11px; }.data-status--present { color: var(--asset-accent); }.upload-dialog { width: min(100%, 720px); }.upload-recursive { display: flex !important; align-items: center; color: #637068 !important; font-weight: 500 !important; gap: 7px !important; }.upload-recursive input { width: auto !important; accent-color: var(--sage); }.upload-command-result { display: grid; margin-top: 8px; padding-top: 17px; gap: 10px; border-top: 1px solid var(--line); }.upload-command-result header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.upload-command-result header div { display: grid; gap: 3px; }.upload-command-result strong { font-size: 13px; }.upload-command-result small, .upload-command-result p { color: var(--muted); font-size: 11px; line-height: 1.55; }.upload-command-result p { margin: 0; }.upload-command-result pre { margin: 0; padding: 13px; overflow-x: auto; color: #dfeade; background: #17221b; border-radius: 6px; font-size: 11px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }.upload-command-result code { color: inherit; }
</style>
