<script setup lang="ts">
import { ArrowLeft, CheckCircle2, CircleAlert, FileText, GitBranch, History, Layers3 } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getAsset } from '@/api/client'
import AssetIcon from '@/components/AssetIcon.vue'
import { assetMeta } from '@/catalogue'
import type { AssetDetail } from '@/types'

const route = useRoute()
const router = useRouter()
const data = ref<AssetDetail | null>(null)
const loading = ref(true)
const error = ref('')
let controller: AbortController | undefined

const meta = computed(() => (data.value ? assetMeta[data.value.type] : null))

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

watch(() => route.params.assetId, load, { immediate: true })
onBeforeUnmount(() => controller?.abort())
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
        <div class="detail-status"><span class="status-badge">{{ data.status }}</span><small>更新于 {{ formatDate(data.updated_at) }}</small></div>
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
            </div>
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
.detail-list-row { display: grid; min-height: 62px; grid-template-columns: 25px minmax(0, 1fr) auto auto; padding: 10px 0; align-items: center; gap: 10px; border-bottom: 1px solid var(--line); }
.detail-list-row > svg { color: var(--asset-accent); }.detail-list-row span { display: grid; min-width: 0; gap: 3px; }.detail-list-row strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.detail-list-row em { color: var(--sage); font-size: 10px; font-style: normal; }.detail-list-row em.warning { color: #b37225; }.detail-list-row time, .detail-timeline time { color: #95a097; font-size: 10px; white-space: nowrap; }
.detail-link { grid-template-columns: 25px minmax(0, 1fr); color: inherit; }.detail-empty { margin: 18px 0 0; }.detail-timeline { display: grid; margin: 15px 0 0; padding: 0; gap: 14px; list-style: none; }.detail-timeline li { display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; gap: 8px; }.detail-timeline svg { color: var(--asset-accent); }.detail-timeline strong { font-size: 12px; }.detail-timeline strong small { margin-left: 5px; color: var(--asset-accent); font-size: 10px; }.detail-timeline p { margin: 4px 0 0; line-height: 1.5; }.detail-privacy { display: flex; margin: 18px 1px 0; align-items: center; gap: 6px; }
@media (max-width: 760px) { .detail-grid { grid-template-columns: 1fr; }.detail-overview { grid-row: auto; }.detail-status { text-align: left; }.detail-facts { grid-template-columns: 1fr; gap: 14px; }.detail-list-row { grid-template-columns: 23px minmax(0, 1fr) auto; }.detail-list-row time { display: none; } }
</style>
