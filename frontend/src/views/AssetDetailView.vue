<script setup lang="ts">
import { useDebounceFn } from '@vueuse/core'
import { Archive, ArrowDownToLine, ArrowLeft, ArrowUpRight, Check, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Copy, Eye, FileText, Folder, FolderOpen, GitBranch, History, Layers3, Link2, Pencil, Plus, Save, Search, Trash2, X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { addAssetRelation, addAssetVersion, archiveAsset, getAsset, getAssetChoices, getFileAccessTicket, getPublicationCitation, removeAssetRelation, updateAsset } from '@/api/client'
import AssetIcon from '@/components/AssetIcon.vue'
import { assetMeta } from '@/catalogue'
import { useOverlayFocus } from '@/composables/useOverlayFocus'
import { useBranding } from '@/composables/useBranding'
import { isPublicationMetadata } from '@/types'
import type { AssetChoiceSummary, AssetDetail, FileAccessMode, FileSummary, PublicationCitation, PublicationMetadata, RelatedAssetSummary, Visibility } from '@/types'
import { copyText, downloadTextFile } from '@/utils/textFiles'

const route = useRoute()
const router = useRouter()
const { pageEyebrow, setPageTitle } = useBranding()
const data = ref<AssetDetail | null>(null)
const loading = ref(true)
const error = ref('')
let controller: AbortController | undefined
const fileActionId = ref<string | null>(null)
const fileActionError = ref('')
const previewingFile = ref<FileSummary | null>(null)
const previewUrl = ref('')
const previewDialog = ref<HTMLElement | null>(null)
const editOpen = ref(false)
const editDialog = ref<HTMLElement | null>(null)
const saving = ref(false)
const archiving = ref(false)
const actionError = ref('')
const editError = ref('')
const edit = ref({ title: '', summary: '', status: '', visibility: 'lab' as Visibility, tags: '' })

const relationOpen = ref(false)
const relationDialog = ref<HTMLElement | null>(null)
const relationCandidates = ref<AssetChoiceSummary[]>([])
const relationQuery = ref('')
const relationCandidatesLoading = ref(false)
let relationCandidatesController: AbortController | undefined
const relationTargetId = ref('')
const relationType = ref('related_to')
const relationSaving = ref(false)
const relationError = ref('')
const meta = computed(() => (data.value ? assetMeta[data.value.type] : null))
const publication = computed<PublicationMetadata | null>(() => data.value && ['paper', 'literature'].includes(data.value.type) && isPublicationMetadata(data.value.details) ? data.value.details : null)
const citation = ref<PublicationCitation | null>(null)
const citationLoading = ref(false)
const citationCopied = ref(false)
const citationError = ref('')
const versionOpen = ref(false)
const versionDialog = ref<HTMLElement | null>(null)
const versionSaving = ref(false)
const versionError = ref('')
const versionDraft = ref({ version: '', releaseNotes: '', makeCurrent: true })
const previewableMimeTypes = new Set([
  'application/json', 'application/pdf', 'application/x-yaml', 'text/csv',
  'text/markdown', 'text/plain', 'text/tab-separated-values', 'text/yaml',
  'image/avif', 'image/gif', 'image/jpeg', 'image/png', 'image/webp',
])
const removingRelationId = ref<string | null>(null)
const previewOpen = computed(() => Boolean(previewingFile.value))
const returnLocation = computed(() => {
  const requested = route.query.returnTo
  if (typeof requested === 'string' && requested.startsWith('/') && !requested.startsWith('//')) return requested
  return data.value ? `/${assetMeta[data.value.type].english.toLowerCase()}` : '/'
})

useOverlayFocus(previewOpen, previewDialog, closePreview)
useOverlayFocus(editOpen, editDialog, closeEdit)
useOverlayFocus(versionOpen, versionDialog, closeVersion)
useOverlayFocus(relationOpen, relationDialog, closeRelation)

interface FileBrowserNode {
  name: string
  path: string
  directory: boolean
  file?: FileSummary
  children: FileBrowserNode[]
}

interface FileBrowserRow {
  name: string
  path: string
  directory: boolean
  depth: number
  childrenCount: number
  file?: FileSummary
}

const expandedDirectories = ref<Set<string>>(new Set())

function relativeFilePath(file: FileSummary, asset = data.value) {
  const prefix = asset ? [asset.type, asset.slug].join("/") + "/" : ""
  return prefix && file.relative_path.startsWith(prefix)
    ? file.relative_path.slice(prefix.length)
    : file.relative_path
}

function directoryPaths(asset: AssetDetail) {
  return asset.files.flatMap((file) => {
    const parts = relativeFilePath(file, asset).split("/").filter(Boolean)
    return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"))
  })
}

function isDirectoryExpanded(path: string) {
  return expandedDirectories.value.has(path)
}

function toggleDirectory(path: string) {
  const next = new Set(expandedDirectories.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expandedDirectories.value = next
}

function buildFileBrowserRows(asset: AssetDetail) {
  const root: FileBrowserNode = { name: "", path: "", directory: true, children: [] }
  for (const file of [...asset.files].sort((left, right) => relativeFilePath(left, asset).localeCompare(relativeFilePath(right, asset), "zh-CN"))) {
    const filePath = relativeFilePath(file, asset)
    const parts = filePath.split("/").filter(Boolean)
    let parent = root
    for (const [index, part] of parts.slice(0, -1).entries()) {
      const path = parts.slice(0, index + 1).join("/")
      let directory = parent.children.find((item) => item.directory && item.path === path)
      if (!directory) {
        directory = { name: part, path, directory: true, children: [] }
        parent.children.push(directory)
      }
      parent = directory
    }
    parent.children.push({ name: file.file_name, path: filePath, directory: false, file, children: [] })
  }

  const rows: FileBrowserRow[] = []
  const visit = (nodes: FileBrowserNode[], depth: number) => {
    for (const node of [...nodes].sort((left, right) => Number(right.directory) - Number(left.directory) || left.name.localeCompare(right.name, "zh-CN"))) {
      rows.push({ name: node.name, path: node.path, directory: node.directory, depth, childrenCount: node.children.length, file: node.file })
      if (node.directory && isDirectoryExpanded(node.path)) visit(node.children, depth + 1)
    }
  }
  visit(root.children, 0)
  return rows
}

const fileBrowserRows = computed(() => (data.value ? buildFileBrowserRows(data.value) : []))

async function loadCitation(asset: AssetDetail, requestController: AbortController) {
  citationLoading.value = true
  try {
    const nextCitation = await getPublicationCitation(asset.id, requestController.signal)
    if (controller !== requestController) return
    citation.value = nextCitation
  } catch (reason) {
    if (controller !== requestController) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    citationError.value = reason instanceof Error ? reason.message : '无法读取 BibTeX'
  } finally {
    if (controller === requestController) citationLoading.value = false
  }
}

async function load() {
  controller?.abort()
  const requestController = new AbortController()
  controller = requestController
  loading.value = true
  error.value = ''
  data.value = null
  citation.value = null
  citationLoading.value = false
  citationError.value = ''
  actionError.value = ''
  try {
    const next = await getAsset(String(route.params.assetId), requestController.signal)
    if (controller !== requestController) return
    data.value = next
    expandedDirectories.value = new Set(directoryPaths(next))
    setPageTitle(next.title)
    if (['paper', 'literature'].includes(next.type) && isPublicationMetadata(next.details)) {
      void loadCitation(next, requestController)
    }
  } catch (reason) {
    if (controller !== requestController) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    error.value = reason instanceof Error ? reason.message : '无法读取资产详情'
  } finally {
    if (controller === requestController) loading.value = false
  }
}

function formatBytes(value: number) {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(new Date(value))
}

function displayValue(value: unknown) {
  return Array.isArray(value) ? value.join('、') : String(value)
}

const genericDetails = computed(() => data.value ? Object.entries(data.value.details) : [])

function healthLabel(status: string) {
  return { healthy: '健康', missing: '缺失', unverified: '待校验', changed: '已变更' }[status] ?? status
}

function canPreview(file: FileSummary) {
  return !!file.mime_type && previewableMimeTypes.has(file.mime_type)
}

async function accessFile(file: FileSummary, mode: FileAccessMode) {
  fileActionId.value = file.id
  fileActionError.value = ''
  try {
    const ticket = await getFileAccessTicket(file.id, mode)
    if (mode === 'preview') {
      previewingFile.value = file
      previewUrl.value = ticket.content_url
      return
    }
    const link = document.createElement('a')
    link.href = ticket.content_url
    link.download = file.file_name
    document.body.append(link)
    link.click()
    link.remove()
  } catch (reason) {
    fileActionError.value = reason instanceof Error ? reason.message : '文件操作失败'
  } finally {
    fileActionId.value = null
  }
}

function closePreview() {
  previewingFile.value = null
  previewUrl.value = ''
}

function returnToCatalogue() {
  void router.push(returnLocation.value)
}

async function copyCitation() {
  if (!citation.value) return
  citationError.value = ''
  try {
    await copyText(citation.value.bibtex)
    citationCopied.value = true
    window.setTimeout(() => { citationCopied.value = false }, 1800)
  } catch {
    citationError.value = '浏览器无法写入剪贴板，请下载引用文件。'
  }
}

function downloadCitation() {
  if (!citation.value) return
  downloadTextFile(citation.value.filename, citation.value.bibtex, 'application/x-bibtex;charset=utf-8')
}

function openEdit() {
  if (!data.value) return
  edit.value = {
    title: data.value.title,
    summary: data.value.summary,
    status: data.value.status,
    visibility: data.value.visibility,
    tags: data.value.tags.join(', '),
  }
  editError.value = ''
  editOpen.value = true
}

function closeEdit() {
  if (!saving.value) editOpen.value = false
}

async function saveEdit() {
  if (!data.value || !edit.value.title.trim()) return
  saving.value = true
  editError.value = ''
  try {
    await updateAsset(data.value.id, {
      title: edit.value.title.trim(),
      summary: edit.value.summary.trim(),
      status: edit.value.status.trim(),
      visibility: edit.value.visibility,
      tags: edit.value.tags.split(/[，,]/).map((tag) => tag.trim()).filter(Boolean),
    })
    editOpen.value = false
    await load()
  } catch (reason) {
    editError.value = reason instanceof Error ? reason.message : '无法保存资产信息'
  } finally {
    saving.value = false
  }
}

async function archiveCurrentAsset() {
  if (!data.value || !window.confirm('归档后资产将从普通目录隐藏，但不会删除原始文件。确定继续吗？')) return
  archiving.value = true
  actionError.value = ''
  try {
    await archiveAsset(data.value.id)
    await router.replace(`/${assetMeta[data.value.type].english.toLowerCase()}`)
  } catch (reason) {
    actionError.value = reason instanceof Error ? reason.message : '无法归档资产'
  } finally {
    archiving.value = false
  }
}

function openVersion() {
  versionDraft.value = { version: '', releaseNotes: '', makeCurrent: true }
  versionError.value = ''
  versionOpen.value = true
}

function closeVersion() {
  if (!versionSaving.value) versionOpen.value = false
}

async function saveVersion() {
  if (!data.value || !versionDraft.value.version.trim()) return
  versionSaving.value = true
  versionError.value = ''
  try {
    await addAssetVersion(data.value.id, {
      version: versionDraft.value.version.trim(),
      release_notes: versionDraft.value.releaseNotes.trim(),
      make_current: versionDraft.value.makeCurrent,
    })
    versionOpen.value = false
    await load()
  } catch (reason) {
    versionError.value = reason instanceof Error ? reason.message : '无法登记版本'
  } finally {
    versionSaving.value = false
  }
}

async function loadRelationCandidates() {
  relationCandidatesController?.abort()
  const requestController = new AbortController()
  relationCandidatesController = requestController
  relationCandidatesLoading.value = true
  relationError.value = ''
  try {
    const relatedIds = new Set(data.value?.related_assets.map((asset) => asset.id) ?? [])
    relationCandidates.value = (await getAssetChoices(relationQuery.value.trim(), requestController.signal)).filter(
      (asset) => asset.id !== data.value?.id && !relatedIds.has(asset.id),
    )
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    relationCandidates.value = []
    relationError.value = reason instanceof Error ? reason.message : '无法读取可关联资产'
  } finally {
    if (relationCandidatesController === requestController) relationCandidatesLoading.value = false
  }
}

const searchRelationCandidates = useDebounceFn(() => {
  void loadRelationCandidates()
}, 250)

function updateRelationQuery() {
  relationTargetId.value = ''
  void searchRelationCandidates()
}

function openRelation() {
  if (!data.value) return
  relationOpen.value = true
  relationQuery.value = ''
  relationTargetId.value = ''
  relationType.value = 'related_to'
  void loadRelationCandidates()
}

function closeRelation() {
  if (relationSaving.value) return
  relationCandidatesController?.abort()
  relationOpen.value = false
}

async function saveRelation() {
  if (!data.value || !relationTargetId.value || !relationType.value.trim()) return
  relationSaving.value = true
  relationError.value = ''
  try {
    await addAssetRelation(data.value.id, {
      target_asset_id: relationTargetId.value,
      relation_type: relationType.value.trim(),
    })
    relationOpen.value = false
    await load()
  } catch (reason) {
    relationError.value = reason instanceof Error ? reason.message : '无法建立关联'
  } finally {
    relationSaving.value = false
  }
}

async function removeRelation(relation: RelatedAssetSummary) {
  if (!data.value || !window.confirm(`解除与「${relation.title}」的关联吗？`)) return
  removingRelationId.value = relation.relation_id
  actionError.value = ''
  try {
    await removeAssetRelation(data.value.id, relation.relation_id)
    await load()
  } catch (reason) {
    actionError.value = reason instanceof Error ? reason.message : '无法解除关联'
  } finally {
    removingRelationId.value = null
  }
}

watch(() => route.params.assetId, load, { immediate: true })
onBeforeUnmount(() => {
  controller?.abort()
  relationCandidatesController?.abort()
  closePreview()
})
</script>

<template>
  <div class="page detail-page" :style="meta ? { '--asset-accent': meta.color, '--asset-soft': meta.softColor } : {}">
    <div v-if="loading" class="state-panel" role="status" aria-live="polite"><span class="loader-ring"></span><p>正在调阅归档记录…</p></div>
    <div v-else-if="error" class="state-panel state-panel--error" role="alert">
      <CircleAlert :size="28" /><strong>无法读取此资产</strong><p>{{ error }}</p>
      <button class="button button--outline" @click="load">重试</button>
    </div>
    <template v-else-if="data && meta">
      <button class="detail-back" @click="returnToCatalogue"><ArrowLeft :size="16" /> 返回目录</button>
      <p v-if="actionError" class="detail-action-error" role="alert"><CircleAlert :size="16" />{{ actionError }}</p>
      <header class="page-heading detail-heading">
        <div class="heading-icon"><AssetIcon :type="data.type" :size="28" /></div>
        <div>
          <p class="eyebrow">{{ pageEyebrow(`ARCHIVE · ${meta.english.toUpperCase()}`) }}</p>
          <h1>{{ data.title }}</h1>
          <p v-if="publication" class="publication-byline-detail">{{ publication.authors.join('、') }}</p>
          <p v-else>{{ data.summary }}</p>
          <div class="tag-list"><span v-for="tag in data.tags" :key="tag">{{ tag }}</span></div>
        </div>
        <div class="detail-status"><span class="status-badge">{{ data.status }}</span><small>更新于 {{ formatDate(data.updated_at) }}</small><div class="detail-controls"><button class="button button--outline" @click="openEdit"><Pencil :size="15" />编辑</button><button class="button detail-archive" :disabled="archiving" @click="archiveCurrentAsset"><Archive :size="15" />{{ archiving ? '正在归档' : '归档' }}</button></div></div>
      </header>

      <section class="detail-grid">
        <article class="panel detail-overview">
          <header class="panel-heading"><div><span class="section-number">01</span><div><h2>归档概要</h2><p>Archive profile</p></div></div></header>
          <dl class="detail-facts">
            <div><dt>资产类型</dt><dd>{{ meta.label }}</dd></div>
            <div><dt>负责人</dt><dd><span class="mini-avatar">{{ data.owner.name.slice(0, 1) }}</span>{{ data.owner.name }}</dd></div>
            <div><dt>当前版本</dt><dd>{{ data.current_version ?? '—' }}</dd></div>
            <div><dt>已索引容量</dt><dd>{{ formatBytes(data.total_size) }}</dd></div>
            <template v-if="publication">
              <div><dt>发表来源</dt><dd>{{ publication.venue }} {{ publication.year }}</dd></div>
              <div><dt>文献类别</dt><dd>{{ publication.track }}</dd></div>
              <div class="detail-fact-wide"><dt>作者</dt><dd>{{ publication.authors.join('、') }}</dd></div>
              <div v-if="publication.doi"><dt>DOI</dt><dd>{{ publication.doi }}</dd></div>
              <div class="detail-fact-links"><dt>官方来源</dt><dd><a :href="publication.source_url" target="_blank" rel="noreferrer"><Link2 :size="14" />来源页面</a><a v-if="publication.publication_url" :href="publication.publication_url" target="_blank" rel="noreferrer"><Link2 :size="14" />出版页面</a><a :href="publication.pdf_url" target="_blank" rel="noreferrer"><FileText :size="14" />正式 PDF</a></dd></div>
            </template>
            <div v-else v-for="([key, value]) in genericDetails" :key="key"><dt>{{ key.replaceAll('_', ' ') }}</dt><dd>{{ displayValue(value) }}</dd></div>
          </dl>
        </article>

        <article v-if="publication" class="panel detail-publication">
          <header class="panel-heading"><div><span class="section-number">02</span><div><h2>{{ data.type === 'paper' ? '论文内容' : '文献内容' }}</h2><p>Abstract & citation</p></div></div></header>
          <div class="publication-content-grid">
            <section class="publication-abstract" aria-labelledby="publication-abstract-title"><span>ABSTRACT</span><h3 id="publication-abstract-title">摘要</h3><p>{{ publication.abstract || '暂未收录摘要。' }}</p></section>
            <section class="publication-citation" aria-labelledby="publication-citation-title">
              <header>
                <div><span>BIBTEX CITATION</span><h3 id="publication-citation-title">出版物引用</h3></div>
                <div v-if="citation" class="publication-citation-actions"><button class="button button--outline" type="button" @click="copyCitation"><Check v-if="citationCopied" :size="15" /><Copy v-else :size="15" />{{ citationCopied ? '已复制' : '复制' }}</button><button class="button button--outline" type="button" @click="downloadCitation"><ArrowDownToLine :size="15" />下载 .bib</button></div>
              </header>
              <div v-if="citationLoading" class="publication-citation-loading" role="status" aria-live="polite"><span class="tiny-spinner"></span>正在生成引用…</div>
              <pre v-else-if="citation"><code>{{ citation.bibtex }}</code></pre>
              <p v-if="citationError" class="publication-citation-error" role="alert">{{ citationError }}</p>
            </section>
          </div>
        </article>

        <article class="panel detail-files">
          <header class="panel-heading"><div><span class="section-number">{{ publication ? '03' : '02' }}</span><div><h2>文件浏览</h2><p>Repository browser</p></div></div><span class="data-note">{{ data.files.length }} files</span></header>
          <div v-if="data.files.length" class="repository-browser">
            <div class="repository-toolbar"><span>受控归档 · {{ data.files.length }} 个文件 · {{ formatBytes(data.total_size) }}</span><small>相对路径仅在当前资产内展示</small></div>
            <div class="repository-head"><span>文件</span><span>类型</span><span>状态</span><span>大小</span><span>操作</span></div>
            <div v-for="row in fileBrowserRows" :key="row.path" class="repository-row" :class="{ 'repository-row--directory': row.directory }">
              <button v-if="row.directory" class="repository-name repository-directory" :aria-expanded="isDirectoryExpanded(row.path)" :style="{ paddingLeft: (16 + row.depth * 24) + 'px' }" @click="toggleDirectory(row.path)"><ChevronDown v-if="isDirectoryExpanded(row.path)" :size="15" /><ChevronRight v-else :size="15" /><FolderOpen v-if="isDirectoryExpanded(row.path)" :size="17" /><Folder v-else :size="17" /><strong>{{ row.name }}</strong><small>{{ row.childrenCount }} 项</small></button>
              <div v-else-if="row.file" class="repository-name" :style="{ paddingLeft: (31 + row.depth * 24) + 'px' }"><FileText :size="17" /><span><strong>{{ row.name }}</strong><small>{{ relativeFilePath(row.file) }}</small></span></div>
              <template v-if="!row.directory && row.file">
                <span class="repository-kind">{{ row.file.file_kind }}</span><em :class="{ warning: row.file.health_status !== 'healthy' }">{{ healthLabel(row.file.health_status) }}</em><time>{{ formatBytes(row.file.file_size) }}</time>
                <div class="file-actions"><button v-if="canPreview(row.file)" :disabled="row.file.health_status !== 'healthy' || fileActionId === row.file.id" title="浏览器预览" @click="accessFile(row.file, 'preview')"><Eye :size="15" /></button><button :disabled="row.file.health_status !== 'healthy' || fileActionId === row.file.id" title="下载文件" @click="accessFile(row.file, 'download')"><ArrowDownToLine :size="15" /></button></div>
              </template>
              <template v-else><span></span><span></span><span></span><span></span></template>
            </div>
            <p v-if="fileActionError" class="file-action-error">{{ fileActionError }}</p>
          </div>
          <p v-else class="detail-empty">尚未从受控存储根索引到文件记录。</p>
        </article>

        <article class="panel detail-versions">
          <header class="panel-heading"><div><span class="section-number">{{ publication ? '04' : '03' }}</span><div><h2>版本沿革</h2><p>Version history</p></div></div><button class="section-action" @click="openVersion"><Plus :size="14" />新增版本</button></header>
          <div class="detail-section-body">
            <ol v-if="data.versions.length" class="detail-timeline">
              <li v-for="version in data.versions" :key="version.id"><CheckCircle2 :size="17" /><div><strong>{{ version.version }} <small v-if="version.is_current">当前版本</small></strong><p>{{ version.release_notes || '未附版本说明' }}</p></div><time>{{ formatDate(version.created_at) }}</time></li>
            </ol>
            <p v-else class="detail-empty">尚未登记版本记录。</p>
          </div>
        </article>

        <article class="panel detail-related">
          <header class="panel-heading"><div><span class="section-number">{{ publication ? '05' : '04' }}</span><div><h2>关联资产</h2><p>Research context</p></div></div><button class="section-action" @click="openRelation"><Plus :size="14" />添加关联</button></header>
          <div class="detail-section-body">
            <div v-if="data.related_assets.length" class="detail-list">
              <div v-for="asset in data.related_assets" :key="asset.relation_id" class="detail-list-row detail-related-row"><RouterLink :to="{ name: 'asset-detail', params: { assetId: asset.id } }" class="detail-link"><GitBranch :size="18" /><span><strong>{{ asset.title }}</strong><small>{{ asset.relation_type }} · {{ assetMeta[asset.type].label }}</small></span></RouterLink><button class="relation-remove" :disabled="removingRelationId === asset.relation_id" :title="`解除与 ${asset.title} 的关联`" @click="removeRelation(asset)"><Trash2 :size="14" /></button></div>
            </div>
            <p v-else class="detail-empty">尚未登记关联资产。可将论文、数据集、项目或模型串联为研究上下文。</p>
          </div>
        </article>

        <article class="panel detail-activity">
          <header class="panel-heading"><div><span class="section-number">{{ publication ? '06' : '05' }}</span><div><h2>近期活动</h2><p>Activity summary</p></div></div><RouterLink class="section-action" to="/activity-log">查看完整日志 <ArrowUpRight :size="14" /></RouterLink></header>
          <div class="detail-section-body">
            <ol v-if="data.recent_activities.length" class="detail-timeline">
              <li v-for="activity in data.recent_activities" :key="activity.id"><History :size="17" /><div><strong>{{ activity.action_label }}<span v-if="activity.occurrence_count > 1" class="activity-count"> ×{{ activity.occurrence_count }}</span></strong><p>{{ activity.actor_name ?? '系统' }}<template v-if="activity.credential_name"> / {{ activity.credential_name }}</template> · {{ activity.description }}</p></div><time>{{ formatDate(activity.created_at) }}</time></li>
            </ol>
            <p v-else class="detail-empty">暂无近期活动。</p>
          </div>
        </article>
      </section>
      <p class="detail-privacy"><Layers3 :size="15" /> 此页面仅展示数据库中的归档元数据；服务器文件路径不会发送到浏览器。</p>
      <div v-if="previewingFile" class="preview-overlay" @click.self="closePreview">
        <section ref="previewDialog" class="preview-dialog" role="dialog" aria-modal="true" :aria-label="`预览 ${previewingFile.file_name}`" tabindex="-1">
          <header><div><p class="eyebrow">CONTROLLED PREVIEW</p><h2>{{ previewingFile.file_name }}</h2></div><button autofocus aria-label="关闭预览" title="关闭预览" @click="closePreview"><X :size="18" /></button></header>
          <iframe v-if="previewUrl" :src="previewUrl" :title="`预览 ${previewingFile.file_name}`"></iframe>
        </section>
      </div>
      <div v-if="editOpen" class="preview-overlay" @click.self="closeEdit">
        <form ref="editDialog" class="edit-dialog" role="dialog" aria-modal="true" aria-label="编辑资产" @submit.prevent="saveEdit">
          <header><div><p class="eyebrow">EDIT ASSET</p><h2>编辑「{{ data.title }}」</h2></div><button type="button" title="关闭编辑" :disabled="saving" @click="closeEdit"><X :size="18" /></button></header>
          <label>标题<input v-model="edit.title" required autofocus maxlength="500" /></label>
          <label>摘要<textarea v-model="edit.summary" maxlength="5000" rows="3"></textarea></label>
          <div class="edit-grid"><label>状态<input v-model="edit.status" required maxlength="40" /></label><label>可见范围<select v-model="edit.visibility"><option value="lab">全实验室</option><option value="project">项目成员</option><option value="restricted">受限</option></select></label></div>
          <label>标签（逗号分隔）<input v-model="edit.tags" /></label>
          <p v-if="editError" class="edit-error">{{ editError }}</p>
          <footer><button class="button button--outline" type="button" :disabled="saving" @click="closeEdit">取消</button><button class="button button--primary" :disabled="saving || !edit.title.trim()" type="submit"><Save :size="16" />{{ saving ? '正在保存' : '保存修改' }}</button></footer>
        </form>
      </div>
      <div v-if="versionOpen" class="preview-overlay" @click.self="closeVersion">
        <form ref="versionDialog" class="edit-dialog" role="dialog" aria-modal="true" aria-label="新增版本" @submit.prevent="saveVersion">
          <header><div><p class="eyebrow">REGISTER VERSION</p><h2>新增版本</h2></div><button type="button" title="关闭" :disabled="versionSaving" @click="closeVersion"><X :size="18" /></button></header>
          <label>版本号<input v-model="versionDraft.version" required autofocus maxlength="80" placeholder="例如：v1.1 或 2026.08" /></label>
          <label>版本说明<textarea v-model="versionDraft.releaseNotes" maxlength="5000" rows="3" placeholder="说明本次版本包含的变更" /></label>
          <label class="version-current"><input v-model="versionDraft.makeCurrent" type="checkbox" />设为当前版本</label>
          <p v-if="versionError" class="edit-error">{{ versionError }}</p>
          <footer><button class="button button--outline" type="button" :disabled="versionSaving" @click="closeVersion">取消</button><button class="button button--primary" :disabled="versionSaving || !versionDraft.version.trim()" type="submit"><Save :size="16" />{{ versionSaving ? '正在登记' : '登记版本' }}</button></footer>
        </form>
      </div>
      <div v-if="relationOpen" class="preview-overlay" @click.self="closeRelation">
        <form ref="relationDialog" class="edit-dialog relation-dialog" role="dialog" aria-modal="true" aria-label="添加关联资产" @submit.prevent="saveRelation">
          <header><div><p class="eyebrow">LINK ASSETS</p><h2>添加关联资产</h2></div><button type="button" title="关闭" :disabled="relationSaving" @click="closeRelation"><X :size="18" /></button></header>
          <p class="relation-help"><Link2 :size="16" />关联只补充元数据，不会移动、复制或删除任何文件。</p>
          <label for="relation-asset-search">关联到</label>
          <div class="relation-search"><Search :size="17" /><input id="relation-asset-search" v-model="relationQuery" autofocus :disabled="relationSaving" placeholder="搜索资产标题或 slug" autocomplete="off" @input="updateRelationQuery" /><span v-if="relationCandidatesLoading" class="tiny-spinner"></span></div>
          <div class="relation-choices" role="listbox" aria-label="关联资产候选项">
            <button v-for="asset in relationCandidates" :key="asset.id" type="button" role="option" :aria-selected="relationTargetId === asset.id" :class="{ selected: relationTargetId === asset.id }" @click="relationTargetId = asset.id"><span><strong>{{ asset.title }}</strong><small>{{ asset.slug }}</small></span><em>{{ assetMeta[asset.type].label }}</em><Check v-if="relationTargetId === asset.id" :size="16" /></button>
            <p v-if="!relationCandidatesLoading && !relationError && !relationCandidates.length">没有匹配的未关联资产。</p>
          </div>
          <label>关系类型<input v-model="relationType" required maxlength="60" placeholder="例如：derived_from、supports、documents" /></label>
          <p v-if="relationError" class="edit-error">{{ relationError }}</p>
          <footer><button class="button button--outline" type="button" :disabled="relationSaving" @click="closeRelation">取消</button><button class="button button--primary" :disabled="relationSaving || !relationTargetId || !relationType.trim()" type="submit"><Link2 :size="16" />{{ relationSaving ? '正在关联' : '建立关联' }}</button></footer>
        </form>
      </div>
    </template>
  </div>
</template>

<style scoped>
.detail-page { --asset-accent: var(--sage); --asset-soft: var(--sage-soft); }
.detail-back { display: inline-flex; min-height: 30px; margin: -4px 0 11px; padding: 0 4px 0 0; align-items: center; gap: 5px; color: #66746b; background: transparent; border: 0; cursor: pointer; font-size: 12px; }
.detail-back:hover, .detail-link:hover { color: var(--asset-accent); }
.detail-action-error { display: flex; min-height: 40px; margin: 0 0 14px; padding: 9px 11px; align-items: center; color: #9a5b3c; background: #fff7f1; border: 1px solid #edd3c2; border-radius: 6px; gap: 7px; font-size: 11px; }
.detail-heading { display: grid; margin-bottom: 24px; grid-template-columns: 42px minmax(0, 1fr) 132px; align-items: start; justify-content: initial; gap: 16px; } .detail-heading .heading-icon { margin-top: 7px; } .detail-heading > div:nth-child(2) { min-width: 0; }
.detail-heading > div:nth-child(2) > p:not(.eyebrow) { display: -webkit-box; max-width: 940px; overflow: hidden; line-height: 1.6; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.detail-heading .publication-byline-detail { color: #5f6f64; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 12px; }
.detail-heading .tag-list { margin-top: 12px; }
.detail-status { display: grid; padding: 7px 0; align-items: start; justify-items: end; gap: 8px; text-align: right; }
.detail-status small, .detail-list-row small, .detail-timeline p, .detail-empty, .detail-privacy { color: #7c887f; font-size: 11px; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.version-current { display: flex !important; align-items: center; gap: 8px !important; font-weight: 500 !important; }.version-current input { width: auto !important; accent-color: var(--sage); }
.detail-overview, .detail-publication { grid-column: 1 / -1; } .detail-overview .panel-heading { min-height: 62px; padding-top: 14px; padding-bottom: 14px; }
.publication-content-grid { display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(360px, .92fr); }
.publication-abstract, .publication-citation { min-width: 0; padding: 20px; }
.publication-citation { border-left: 1px solid var(--line); }.publication-citation > header { display: flex; min-height: 36px; align-items: flex-end; justify-content: space-between; gap: 16px; }.publication-abstract > span, .publication-citation header span { color: #7d8981; font-size: 9px; font-weight: 800; letter-spacing: 0; }.publication-abstract h3, .publication-citation h3 { margin: 3px 0 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 18px; font-weight: 500; }.publication-abstract p { margin: 13px 0 0; color: #526158; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 12px; line-height: 1.75; }.publication-citation-actions { display: flex; gap: 7px; }.publication-citation-actions .button { min-height: 32px; padding: 0 10px; }.publication-citation pre { max-height: 300px; margin: 13px 0 0; padding: 14px 16px; overflow: auto; color: #dfeade; background: #17221b; border-radius: 6px; font-size: 11px; line-height: 1.65; white-space: pre-wrap; overflow-wrap: anywhere; }.publication-citation-loading { display: flex; min-height: 74px; align-items: center; color: var(--muted); gap: 8px; font-size: 11px; }.publication-citation-error { margin: 10px 0 0; color: #a6633b; font-size: 11px; }
.detail-facts { display: grid; margin: 0; padding: 18px 20px 20px; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px 24px; }
.detail-facts dt { margin-bottom: 7px; color: #849188; font-size: 10px; text-transform: capitalize; }
.detail-facts dd { display: flex; margin: 0; align-items: center; gap: 6px; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 14px; line-height: 1.5; overflow-wrap: anywhere; }
.detail-fact-wide { grid-column: span 3; }
.detail-fact-links { grid-column: span 1; }
.detail-section-body { min-height: 104px; padding: 17px 18px 18px; }
.detail-list { border-top: 1px solid var(--line); }
.detail-files, .detail-activity { grid-column: 1 / -1; }
.repository-browser { margin-top: 15px; overflow: hidden; border: 1px solid var(--line); border-radius: 7px; }
.repository-toolbar { display: flex; min-height: 40px; padding: 0 14px; align-items: center; justify-content: space-between; gap: 12px; color: #5c6c61; background: #f6f8f4; border-bottom: 1px solid var(--line); font-size: 11px; }
.repository-toolbar small { color: #8b968e; }
.repository-head, .repository-row { display: grid; grid-template-columns: minmax(0, 1fr) 80px 70px 78px 76px; align-items: center; }
.repository-head { min-height: 32px; padding: 0 13px; color: #8a968d; background: #fbfcfa; border-bottom: 1px solid var(--line); font-size: 10px; text-transform: uppercase; letter-spacing: .04em; }
.repository-row { min-height: 48px; border-bottom: 1px solid #edf0eb; }
.repository-row:last-of-type { border-bottom: 0; }
.repository-row--directory { background: #fdfefd; }
.repository-name { display: flex; min-width: 0; min-height: 48px; align-items: center; gap: 8px; color: #34483a; text-align: left; }
.repository-name > svg { flex: 0 0 auto; color: var(--asset-accent); }
.repository-name > span { display: grid; min-width: 0; gap: 2px; }
.repository-name strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.repository-name small { overflow: hidden; color: #8b968e; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.repository-directory { width: 100%; padding-top: 0; padding-bottom: 0; background: transparent; border: 0; cursor: pointer; }
.repository-directory:hover { color: var(--asset-accent); background: var(--asset-soft); }
.repository-directory > svg:first-child { color: #89968d; }
.repository-row--directory .repository-directory { grid-column: 1 / -1; } .repository-row--directory > span { display: none; } .repository-directory small { margin-left: 3px; padding: 0; color: #8b968e; }
.repository-kind { color: #66766b; font-size: 10px; }
.repository-row em { color: var(--sage); font-size: 10px; font-style: normal; }
.repository-row em.warning { color: #b37225; }
.repository-row time { color: #89968d; font-size: 10px; }
.detail-list-row { display: grid; min-height: 62px; grid-template-columns: 25px minmax(0, 1fr) auto auto auto; padding: 10px 0; align-items: center; gap: 10px; border-bottom: 1px solid var(--line); }
.detail-list-row > svg { color: var(--asset-accent); }.detail-list-row span { display: grid; min-width: 0; gap: 3px; }.detail-list-row strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.detail-list-row em { color: var(--sage); font-size: 10px; font-style: normal; }.detail-list-row em.warning { color: #b37225; }.detail-list-row time, .detail-timeline time { color: #95a097; font-size: 10px; white-space: nowrap; }
.file-actions { display: flex; gap: 4px; }.file-actions button, .preview-dialog header button { display: grid; width: 29px; height: 29px; padding: 0; color: #506356; place-items: center; background: #f4f6f1; border: 1px solid var(--line); border-radius: 5px; cursor: pointer; }.file-actions button:hover:not(:disabled), .preview-dialog header button:hover { color: var(--asset-accent); border-color: var(--asset-accent); }.file-actions button:disabled { cursor: not-allowed; opacity: .45; }.file-action-error { margin: 12px 0 0; color: #a6633b; font-size: 11px; }
.detail-link { grid-template-columns: 25px minmax(0, 1fr); color: inherit; }.detail-empty { display: flex; min-height: 68px; margin: 0; align-items: center; color: #7c887f; line-height: 1.6; }.detail-timeline { display: grid; margin: 0; padding: 0; list-style: none; }.detail-timeline li { position: relative; display: grid; min-height: 68px; padding: 7px 0 13px; grid-template-columns: 22px minmax(0, 1fr) auto; align-items: start; gap: 9px; }.detail-timeline li:not(:last-child)::after { position: absolute; top: 28px; bottom: -1px; left: 8px; width: 1px; content: ""; background: #d9e2da; }.detail-timeline svg { position: relative; z-index: 1; margin-top: 1px; color: var(--asset-accent); background: rgba(252, 253, 249, .98); }.detail-timeline strong { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; font-size: 12px; line-height: 1.4; }.detail-timeline strong small { color: var(--asset-accent); font-size: 9px; }.detail-timeline p { margin: 5px 0 0; line-height: 1.55; overflow-wrap: anywhere; }.detail-timeline time { padding-top: 2px; }.detail-privacy { display: flex; margin: 18px 1px 0; align-items: center; gap: 6px; }
.section-action, .relation-remove { display: inline-flex; min-height: 30px; padding: 0 2px; align-items: center; gap: 4px; color: #63746a; background: transparent; border: 0; cursor: pointer; font-size: 10px; }.section-action:hover, .relation-remove:hover:not(:disabled) { color: var(--asset-accent); }.detail-related-row { grid-template-columns: minmax(0, 1fr) auto; }.detail-related-row .detail-link { display: grid; min-width: 0; align-items: center; gap: 10px; }.relation-remove { width: 30px; height: 30px; justify-content: center; color: #a6633b; border: 1px solid #eddcd1; border-radius: 5px; }.relation-remove:disabled { opacity: .45; cursor: wait; }.relation-help { display: flex; margin: 0; align-items: center; gap: 6px; color: #6b7a70; font-size: 12px; line-height: 1.5; }.relation-help svg { color: var(--sage); }
.relation-search { display: flex; min-height: 44px; padding: 0 11px; align-items: center; gap: 8px; color: #748178; background: #fff; border: 1px solid var(--line); border-radius: 5px; }.relation-search:focus-within { border-color: var(--sage); box-shadow: 0 0 0 3px var(--sage-soft); }.relation-search input { min-width: 0; flex: 1; padding-right: 0; padding-left: 0; background: transparent; border: 0; outline: 0; }.relation-choices { display: grid; max-height: 230px; overflow-y: auto; background: #fff; border: 1px solid var(--line); border-radius: 5px; }.relation-choices > button { display: grid; min-height: 52px; padding: 8px 10px; align-items: center; color: var(--ink); text-align: left; background: transparent; border: 0; border-bottom: 1px solid #e8ece6; grid-template-columns: minmax(0, 1fr) auto 18px; gap: 8px; cursor: pointer; }.relation-choices > button:last-of-type { border-bottom: 0; }.relation-choices > button:hover, .relation-choices > button.selected { background: var(--sage-soft); }.relation-choices span { min-width: 0; }.relation-choices strong, .relation-choices small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.relation-choices strong { font-size: 12px; }.relation-choices small { margin-top: 3px; color: #758179; font-family: ui-monospace, "SFMono-Regular", monospace; font-size: 10px; }.relation-choices em { color: #617068; font-size: 10px; font-style: normal; }.relation-choices > p { margin: 0; padding: 20px 12px; color: #748178; text-align: center; font-size: 11px; }
.preview-overlay { position: fixed; z-index: 40; inset: 0; display: grid; padding: 26px; place-items: center; background: rgba(18, 29, 22, .52); }.preview-dialog { display: grid; width: min(100%, 1040px); height: min(84vh, 780px); grid-template-rows: auto minmax(0, 1fr); padding: 18px; background: #fff; border-radius: 8px; box-shadow: 0 25px 70px rgba(0,0,0,.28); }.preview-dialog header { display: flex; margin-bottom: 13px; align-items: flex-start; justify-content: space-between; gap: 12px; }.preview-dialog h2 { margin: 3px 0 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 17px; font-weight: 500; }.preview-dialog iframe { width: 100%; height: 100%; border: 1px solid var(--line); border-radius: 5px; background: #f8faf7; }
.detail-controls { display: flex; margin-top: 5px; justify-content: flex-end; gap: 6px; }.detail-controls .button { min-height: 29px; padding: 0 8px; font-size: 10px; }.detail-archive { color: #9a5b3c; background: #fff7f1; border: 1px solid #edd3c2; }.edit-dialog { display: grid; width: min(100%, 620px); max-height: 86vh; padding: 22px; overflow-y: auto; background: #fff; border-radius: 8px; box-shadow: 0 25px 70px rgba(0,0,0,.28); gap: 12px; }.edit-dialog header { display: flex; margin-bottom: 3px; align-items: flex-start; justify-content: space-between; gap: 12px; }.edit-dialog h2 { margin: 3px 0 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 19px; font-weight: 500; }.edit-dialog header button { display: grid; width: 29px; height: 29px; padding: 0; place-items: center; background: #f4f6f1; border: 1px solid var(--line); border-radius: 5px; cursor: pointer; }.edit-dialog label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.edit-dialog input, .edit-dialog textarea, .edit-dialog select { width: 100%; padding: 9px 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.edit-dialog textarea { resize: vertical; }.edit-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.edit-dialog footer { display: flex; justify-content: flex-end; gap: 8px; }.edit-error { margin: 0; color: #a6633b; font-size: 12px; }
@media (max-width: 1040px) { .publication-content-grid { grid-template-columns: 1fr; }.publication-citation { border-top: 1px solid var(--line); border-left: 0; }.detail-facts { grid-template-columns: repeat(3, minmax(0, 1fr)); }.detail-fact-wide { grid-column: span 2; } }
@media (max-width: 760px) { .detail-grid { grid-template-columns: 1fr; }.detail-heading { grid-template-columns: 34px minmax(0, 1fr); gap: 10px; }.detail-heading .heading-icon { margin-top: 5px; }.detail-heading h1 { font-size: 27px; line-height: 1.13; }.detail-heading > div:nth-child(2) > p:not(.eyebrow) { -webkit-line-clamp: 3; }.detail-status { grid-column: 1 / -1; display: flex; padding: 0 0 0 44px; align-items: center; justify-content: flex-start; flex-wrap: wrap; text-align: left; }.detail-controls { width: 100%; margin-top: 0; justify-content: flex-start; }.detail-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }.detail-fact-wide, .detail-fact-links { grid-column: 1 / -1; }.detail-section-body { min-height: 0; padding: 15px 14px 16px; }.detail-list-row { grid-template-columns: 23px minmax(0, 1fr) auto; }.detail-timeline li { grid-template-columns: 22px minmax(0, 1fr); }.detail-timeline time { grid-column: 2; margin-top: -8px; }.repository-toolbar { padding: 9px 12px; align-items: flex-start; flex-direction: column; gap: 3px; }.repository-head { display: none; }.repository-row { grid-template-columns: minmax(0, 1fr) auto auto; }.repository-kind, .repository-row time { display: none; }.repository-row .repository-name { min-width: 0; }.repository-row .file-actions { grid-column: auto; margin-right: 10px; }.detail-list-row time { display: none; }.preview-overlay { padding: 12px; }.preview-dialog { height: 88vh; padding: 13px; }.edit-grid { grid-template-columns: 1fr; }.publication-citation > header { align-items: flex-start; flex-direction: column; }.publication-citation-actions { width: 100%; }.publication-citation-actions .button { flex: 1; } }
@media (max-width: 460px) { .detail-facts { grid-template-columns: 1fr; }.detail-fact-wide, .detail-fact-links { grid-column: auto; }.publication-abstract, .publication-citation { padding: 16px; }.publication-citation pre { font-size: 10px; }.repository-row { grid-template-columns: minmax(0, 1fr) auto; }.repository-row em { display: none; }.repository-row .file-actions { margin-right: 8px; } }
</style>
