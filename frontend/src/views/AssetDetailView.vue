<script setup lang="ts">
import { Archive, ArrowDownToLine, ArrowLeft, CheckCircle2, CircleAlert, Eye, FileText, GitBranch, History, Layers3, Pencil, Save, X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { archiveAsset, getAsset, getFileAccessTicket, updateAsset } from '@/api/client'
import AssetIcon from '@/components/AssetIcon.vue'
import { assetMeta } from '@/catalogue'
import type { AssetDetail, FileAccessMode, FileSummary, Visibility } from '@/types'

const route = useRoute()
const router = useRouter()
const data = ref<AssetDetail | null>(null)
const loading = ref(true)
const error = ref('')
let controller: AbortController | undefined
const fileActionId = ref<string | null>(null)
const fileActionError = ref('')
const previewingFile = ref<FileSummary | null>(null)
const previewUrl = ref('')
const editOpen = ref(false)
const saving = ref(false)
const archiving = ref(false)
const editError = ref('')
const edit = ref({ title: '', summary: '', status: '', visibility: 'lab' as Visibility, tags: '' })

const meta = computed(() => (data.value ? assetMeta[data.value.type] : null))
const previewableMimeTypes = new Set([
  'application/json', 'application/pdf', 'application/x-yaml', 'text/csv',
  'text/markdown', 'text/plain', 'text/tab-separated-values', 'text/yaml',
  'image/avif', 'image/gif', 'image/jpeg', 'image/png', 'image/webp',
])

async function load() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    data.value = await getAsset(String(route.params.assetId), controller.signal)
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    error.value = reason instanceof Error ? reason.message : '无法读取资产详情'
  } finally {
    loading.value = false
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
  try {
    await archiveAsset(data.value.id)
    await router.replace(`/${assetMeta[data.value.type].english.toLowerCase()}`)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法归档资产'
  } finally {
    archiving.value = false
  }
}

watch(() => route.params.assetId, load, { immediate: true })
onBeforeUnmount(() => {
  controller?.abort()
  closePreview()
})
</script>

<template>
  <div class="page detail-page" :style="meta ? { '--asset-accent': meta.color, '--asset-soft': meta.softColor } : {}">
    <div v-if="loading" class="state-panel"><span class="loader-ring"></span><p>正在调阅归档记录…</p></div>
    <div v-else-if="error" class="state-panel state-panel--error">
      <CircleAlert :size="28" /><strong>无法读取此资产</strong><p>{{ error }}</p>
      <button class="button button--outline" @click="load">重试</button>
    </div>
    <template v-else-if="data && meta">
      <button class="detail-back" @click="router.back()"><ArrowLeft :size="16" /> 返回目录</button>
      <header class="page-heading detail-heading">
        <div class="heading-icon"><AssetIcon :type="data.type" :size="28" /></div>
        <div>
          <p class="eyebrow">SAGE ARCHIVE · {{ meta.english.toUpperCase() }}</p>
          <h1>{{ data.title }}</h1>
          <p>{{ data.summary }}</p>
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
            <div v-for="([key, value]) in Object.entries(data.details)" :key="key"><dt>{{ key.replaceAll('_', ' ') }}</dt><dd>{{ displayValue(value) }}</dd></div>
          </dl>
        </article>

        <article class="panel detail-files">
          <header class="panel-heading"><div><span class="section-number">02</span><div><h2>文件索引</h2><p>Read-only file records</p></div></div><span class="data-note">{{ data.files.length }} records</span></header>
          <div v-if="data.files.length" class="detail-list">
            <div v-for="file in data.files" :key="file.id" class="detail-list-row">
              <FileText :size="19" /><span><strong>{{ file.file_name }}</strong><small>{{ file.file_kind }} · {{ file.mime_type ?? 'unknown type' }}</small></span><em :class="{ warning: file.health_status !== 'healthy' }">{{ healthLabel(file.health_status) }}</em><time>{{ formatBytes(file.file_size) }}</time>
              <div class="file-actions">
                <button v-if="canPreview(file)" :disabled="file.health_status !== 'healthy' || fileActionId === file.id" title="浏览器预览" @click="accessFile(file, 'preview')"><Eye :size="15" /></button>
                <button :disabled="file.health_status !== 'healthy' || fileActionId === file.id" title="下载文件" @click="accessFile(file, 'download')"><ArrowDownToLine :size="15" /></button>
              </div>
            </div>
            <p v-if="fileActionError" class="file-action-error">{{ fileActionError }}</p>
          </div>
          <p v-else class="detail-empty">尚未从受控存储根索引到文件记录。</p>
        </article>

        <article class="panel detail-versions">
          <header class="panel-heading"><div><span class="section-number">03</span><div><h2>版本沿革</h2><p>Version history</p></div></div></header>
          <ol v-if="data.versions.length" class="detail-timeline">
            <li v-for="version in data.versions" :key="version.id"><CheckCircle2 :size="17" /><div><strong>{{ version.version }} <small v-if="version.is_current">当前版本</small></strong><p>{{ version.release_notes || '未附版本说明' }}</p></div><time>{{ formatDate(version.created_at) }}</time></li>
          </ol>
          <p v-else class="detail-empty">尚未登记版本记录。</p>
        </article>

        <article class="panel detail-related">
          <header class="panel-heading"><div><span class="section-number">04</span><div><h2>关联资产</h2><p>Research context</p></div></div></header>
          <div v-if="data.related_assets.length" class="detail-list">
            <RouterLink v-for="asset in data.related_assets" :key="asset.id" :to="{ name: 'asset-detail', params: { assetId: asset.id } }" class="detail-list-row detail-link"><GitBranch :size="18" /><span><strong>{{ asset.title }}</strong><small>{{ asset.relation_type }} · {{ assetMeta[asset.type].label }}</small></span></RouterLink>
          </div>
          <p v-else class="detail-empty">尚未登记关联资产。</p>
        </article>

        <article class="panel detail-activity">
          <header class="panel-heading"><div><span class="section-number">05</span><div><h2>归档活动</h2><p>Catalogue history</p></div></div></header>
          <ol v-if="data.recent_activities.length" class="detail-timeline">
            <li v-for="activity in data.recent_activities" :key="activity.id"><History :size="17" /><div><strong>{{ activity.action }}</strong><p>{{ activity.actor_name ?? '系统' }} · {{ activity.description }}</p></div><time>{{ formatDate(activity.created_at) }}</time></li>
          </ol>
          <p v-else class="detail-empty">暂无归档活动。</p>
        </article>
      </section>
      <p class="detail-privacy"><Layers3 :size="15" /> 此页面仅展示数据库中的归档元数据；服务器文件路径不会发送到浏览器。</p>
      <div v-if="previewingFile" class="preview-overlay" role="dialog" aria-modal="true" :aria-label="`预览 ${previewingFile.file_name}`">
        <section class="preview-dialog">
          <header><div><p class="eyebrow">CONTROLLED PREVIEW</p><h2>{{ previewingFile.file_name }}</h2></div><button title="关闭预览" @click="closePreview"><X :size="18" /></button></header>
          <iframe v-if="previewUrl" :src="previewUrl" :title="`预览 ${previewingFile.file_name}`"></iframe>
        </section>
      </div>
      <div v-if="editOpen" class="preview-overlay" role="dialog" aria-modal="true" aria-label="编辑资产">
        <form class="edit-dialog" @submit.prevent="saveEdit">
          <header><div><p class="eyebrow">EDIT ASSET</p><h2>编辑「{{ data.title }}」</h2></div><button type="button" title="关闭编辑" :disabled="saving" @click="closeEdit"><X :size="18" /></button></header>
          <label>标题<input v-model="edit.title" required maxlength="500" /></label>
          <label>摘要<textarea v-model="edit.summary" maxlength="5000" rows="3"></textarea></label>
          <div class="edit-grid"><label>状态<input v-model="edit.status" required maxlength="40" /></label><label>可见范围<select v-model="edit.visibility"><option value="lab">全实验室</option><option value="project">项目成员</option><option value="restricted">受限</option></select></label></div>
          <label>标签（逗号分隔）<input v-model="edit.tags" /></label>
          <p v-if="editError" class="edit-error">{{ editError }}</p>
          <footer><button class="button button--outline" type="button" :disabled="saving" @click="closeEdit">取消</button><button class="button button--primary" :disabled="saving || !edit.title.trim()" type="submit"><Save :size="16" />{{ saving ? '正在保存' : '保存修改' }}</button></footer>
        </form>
      </div>
    </template>
  </div>
</template>

<style scoped>
.detail-page { --asset-accent: var(--sage); --asset-soft: var(--sage-soft); }
.detail-back { display: inline-flex; margin-bottom: 15px; padding: 0; align-items: center; gap: 5px; color: #66746b; background: transparent; border: 0; cursor: pointer; font-size: 12px; }
.detail-back:hover, .detail-link:hover { color: var(--asset-accent); }
.detail-heading { align-items: flex-start; }
.detail-heading .tag-list { margin-top: 12px; }
.detail-status { display: grid; padding: 7px 0; gap: 8px; text-align: right; }
.detail-status small, .detail-list-row small, .detail-timeline p, .detail-empty, .detail-privacy { color: #7c887f; font-size: 11px; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.detail-overview { grid-row: span 2; }
.detail-facts { display: grid; margin: 20px 0 0; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.detail-facts dt { margin-bottom: 6px; color: #94a097; font-size: 10px; text-transform: capitalize; }
.detail-facts dd { display: flex; margin: 0; align-items: center; gap: 6px; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 14px; overflow-wrap: anywhere; }
.detail-list { margin-top: 15px; border-top: 1px solid var(--line); }
.detail-list-row { display: grid; min-height: 62px; grid-template-columns: 25px minmax(0, 1fr) auto auto auto; padding: 10px 0; align-items: center; gap: 10px; border-bottom: 1px solid var(--line); }
.detail-list-row > svg { color: var(--asset-accent); }.detail-list-row span { display: grid; min-width: 0; gap: 3px; }.detail-list-row strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.detail-list-row em { color: var(--sage); font-size: 10px; font-style: normal; }.detail-list-row em.warning { color: #b37225; }.detail-list-row time, .detail-timeline time { color: #95a097; font-size: 10px; white-space: nowrap; }
.file-actions { display: flex; gap: 4px; }.file-actions button, .preview-dialog header button { display: grid; width: 29px; height: 29px; padding: 0; color: #506356; place-items: center; background: #f4f6f1; border: 1px solid var(--line); border-radius: 5px; cursor: pointer; }.file-actions button:hover:not(:disabled), .preview-dialog header button:hover { color: var(--asset-accent); border-color: var(--asset-accent); }.file-actions button:disabled { cursor: not-allowed; opacity: .45; }.file-action-error { margin: 12px 0 0; color: #a6633b; font-size: 11px; }
.detail-link { grid-template-columns: 25px minmax(0, 1fr); color: inherit; }.detail-empty { margin: 18px 0 0; }.detail-timeline { display: grid; margin: 15px 0 0; padding: 0; gap: 14px; list-style: none; }.detail-timeline li { display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; gap: 8px; }.detail-timeline svg { color: var(--asset-accent); }.detail-timeline strong { font-size: 12px; }.detail-timeline strong small { margin-left: 5px; color: var(--asset-accent); font-size: 10px; }.detail-timeline p { margin: 4px 0 0; line-height: 1.5; }.detail-privacy { display: flex; margin: 18px 1px 0; align-items: center; gap: 6px; }
.preview-overlay { position: fixed; z-index: 20; inset: 0; display: grid; padding: 26px; place-items: center; background: rgba(18, 29, 22, .52); }.preview-dialog { display: grid; width: min(100%, 1040px); height: min(84vh, 780px); grid-template-rows: auto minmax(0, 1fr); padding: 18px; background: #fff; border-radius: 11px; box-shadow: 0 25px 70px rgba(0,0,0,.28); }.preview-dialog header { display: flex; margin-bottom: 13px; align-items: flex-start; justify-content: space-between; gap: 12px; }.preview-dialog h2 { margin: 3px 0 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 17px; font-weight: 500; }.preview-dialog iframe { width: 100%; height: 100%; border: 1px solid var(--line); border-radius: 5px; background: #f8faf7; }
.detail-controls { display: flex; margin-top: 5px; justify-content: flex-end; gap: 6px; }.detail-controls .button { min-height: 29px; padding: 0 8px; font-size: 10px; }.detail-archive { color: #9a5b3c; background: #fff7f1; border: 1px solid #edd3c2; }.edit-dialog { display: grid; width: min(100%, 620px); max-height: 86vh; padding: 22px; overflow-y: auto; background: #fff; border-radius: 11px; box-shadow: 0 25px 70px rgba(0,0,0,.28); gap: 12px; }.edit-dialog header { display: flex; margin-bottom: 3px; align-items: flex-start; justify-content: space-between; gap: 12px; }.edit-dialog h2 { margin: 3px 0 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 19px; font-weight: 500; }.edit-dialog header button { display: grid; width: 29px; height: 29px; padding: 0; place-items: center; background: #f4f6f1; border: 1px solid var(--line); border-radius: 5px; cursor: pointer; }.edit-dialog label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.edit-dialog input, .edit-dialog textarea, .edit-dialog select { width: 100%; padding: 9px 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.edit-dialog textarea { resize: vertical; }.edit-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.edit-dialog footer { display: flex; justify-content: flex-end; gap: 8px; }.edit-error { margin: 0; color: #a6633b; font-size: 12px; }
@media (max-width: 760px) { .detail-grid { grid-template-columns: 1fr; }.detail-overview { grid-row: auto; }.detail-status { text-align: left; }.detail-controls { justify-content: flex-start; }.detail-facts { grid-template-columns: 1fr; gap: 14px; }.detail-list-row { grid-template-columns: 23px minmax(0, 1fr) auto; }.detail-list-row time { display: none; }.file-actions { grid-column: 2 / -1; }.preview-overlay { padding: 12px; }.preview-dialog { height: 88vh; padding: 13px; }.edit-grid { grid-template-columns: 1fr; } }
</style>
