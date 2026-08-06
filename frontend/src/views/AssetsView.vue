<script setup lang="ts">
import { useDebounceFn } from '@vueuse/core'
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Eye,
  Grid2X2,
  List,
  LockKeyhole,
  Search,
  ShieldCheck,
  SlidersHorizontal,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AssetIcon from '@/components/AssetIcon.vue'
import { getAssets } from '@/api/client'
import { assetMeta } from '@/catalogue'
import type { AssetListResponse, AssetType } from '@/types'

const route = useRoute()
const router = useRouter()
const data = ref<AssetListResponse | null>(null)
const loading = ref(false)
const error = ref('')
const query = ref(typeof route.query.q === 'string' ? route.query.q : '')
const page = ref(1)
const view = ref<'list' | 'grid'>('list')
let controller: AbortController | undefined

const assetType = computed(() => route.meta.assetType as AssetType)
const meta = computed(() => assetMeta[assetType.value])
const totalPages = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / 20)))

async function load() {
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    data.value = await getAssets(
      assetType.value,
      { query: query.value.trim(), page: page.value, pageSize: 20 },
      controller.signal,
    )
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    error.value = reason instanceof Error ? reason.message : '无法读取资产目录'
  } finally {
    loading.value = false
  }
}

const updateSearch = useDebounceFn(() => {
  page.value = 1
  router.replace({ query: query.value.trim() ? { q: query.value.trim() } : {} })
  load()
}, 280)

function changePage(nextPage: number) {
  if (nextPage < 1 || nextPage > totalPages.value) return
  page.value = nextPage
  load()
  window.scrollTo({ top: 0, behavior: 'smooth' })
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
  const entries = Object.entries(details).filter(([, value]) => typeof value !== 'object').slice(0, 2)
  return entries.map(([key, value]) => `${key.replace('_', ' ')} · ${value}`).join('  /  ')
}

watch(
  () => route.meta.assetType,
  () => {
    query.value = typeof route.query.q === 'string' ? route.query.q : ''
    page.value = 1
    load()
  },
  { immediate: true },
)
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="page assets-page" :style="{ '--asset-accent': meta.color, '--asset-soft': meta.softColor }">
    <header class="page-heading assets-heading">
      <div class="heading-icon"><AssetIcon :type="assetType" :size="27" /></div>
      <div>
        <p class="eyebrow">SAGE CATALOGUE · {{ meta.english.toUpperCase() }}</p>
        <h1>{{ meta.label }}目录</h1>
        <p>{{ meta.description }}。所有内容均来自实验室受控存储根。</p>
      </div>
      <button class="button button--primary" disabled title="写入流程将在下一阶段开放">登记{{ meta.label }}</button>
    </header>

    <section class="catalogue-toolbar">
      <label class="catalogue-search">
        <Search :size="19" />
        <input v-model="query" :placeholder="`搜索${meta.label}标题、摘要或关键词`" @input="updateSearch" />
        <span v-if="loading" class="tiny-spinner"></span>
      </label>
      <button class="filter-button" disabled title="结构化筛选将在下一阶段开放"><SlidersHorizontal :size="17" /> 筛选条件 <span>0</span></button>
      <div class="view-switch" aria-label="视图切换">
        <button :class="{ active: view === 'list' }" aria-label="列表视图" @click="view = 'list'"><List :size="18" /></button>
        <button :class="{ active: view === 'grid' }" aria-label="卡片视图" @click="view = 'grid'"><Grid2X2 :size="17" /></button>
      </div>
    </section>

    <div class="catalogue-summary">
      <p><strong>{{ data?.total ?? 0 }}</strong> 项{{ meta.label }}资产</p>
      <span>按最近更新时间排序</span>
    </div>

    <div v-if="error" class="state-panel state-panel--error state-panel--inline">
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
          <div class="tag-list"><span v-for="tag in asset.tags" :key="tag">{{ tag }}</span></div>
        </div>
        <dl class="catalogue-facts">
          <div><dt>当前版本</dt><dd>{{ asset.current_version ?? '—' }}</dd></div>
          <div><dt>文件规模</dt><dd>{{ formatBytes(asset.total_size) }}</dd></div>
          <div><dt>负责人</dt><dd><span class="mini-avatar">{{ asset.owner.name.slice(0, 1) }}</span>{{ asset.owner.name }}</dd></div>
          <div><dt>更新日期</dt><dd>{{ formatDate(asset.updated_at) }}</dd></div>
        </dl>
        <div class="catalogue-detail-line">{{ detailText(asset.details) }}</div>
        <div class="visibility-line">
          <ShieldCheck v-if="asset.visibility === 'lab'" :size="15" />
          <LockKeyhole v-else :size="15" />
          {{ asset.visibility === 'lab' ? '全实验室可见' : asset.visibility === 'project' ? '项目成员可见' : '受限资产' }}
        </div>
        <div class="catalogue-actions">
          <button disabled title="文件预览将在下一阶段开放"><Eye :size="18" /><span>预览</span></button>
          <button disabled title="受控下载将在下一阶段开放"><ArrowDownToLine :size="18" /><span>下载</span></button>
          <RouterLink class="action-primary" :to="{ name: 'asset-detail', params: { assetId: asset.id } }">查看详情 <ArrowRight :size="16" /></RouterLink>
        </div>
      </article>
    </section>

    <footer v-if="data && data.total > 20" class="pagination">
      <button :disabled="page === 1" @click="changePage(page - 1)"><ArrowLeft :size="16" /> 上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页</span>
      <button :disabled="page === totalPages" @click="changePage(page + 1)">下一页 <ArrowRight :size="16" /></button>
    </footer>
  </div>
</template>
