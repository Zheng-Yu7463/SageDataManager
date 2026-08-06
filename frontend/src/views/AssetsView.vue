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
import { createAsset, getAssets } from '@/api/client'
import { assetMeta } from '@/catalogue'
import type { AssetListResponse, AssetType, Visibility } from '@/types'

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
const registrationOpen = ref(false)
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
})

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

function openRegistration() {
  registration.value = {
    title: '',
    slug: '',
    summary: '',
    status: 'draft',
    visibility: 'lab',
    version: '',
    tags: '',
  }
  createError.value = ''
  registrationOpen.value = true
}

function closeRegistration() {
  if (!creating.value) registrationOpen.value = false
}

async function registerAsset() {
  if (!registration.value.title.trim() || !registration.value.slug.trim()) return
  creating.value = true
  createError.value = ''
  try {
    const asset = await createAsset({
      type: assetType.value,
      title: registration.value.title.trim(),
      slug: registration.value.slug.trim(),
      summary: registration.value.summary.trim(),
      status: registration.value.status.trim() || 'draft',
      visibility: registration.value.visibility,
      version: registration.value.version.trim() || null,
      tags: registration.value.tags.split(/[，,]/).map((tag) => tag.trim()).filter(Boolean),
      details: {},
    })
    registrationOpen.value = false
    await router.push({ name: 'asset-detail', params: { assetId: asset.id } })
  } catch (reason) {
    createError.value = reason instanceof Error ? reason.message : '登记失败，请稍后重试'
  } finally {
    creating.value = false
  }
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
      <button class="button button--primary" @click="openRegistration"><Plus :size="16" />登记{{ meta.label }}</button>
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
    <div v-if="registrationOpen" class="registration-backdrop" @click.self="closeRegistration">
      <form class="registration-dialog" aria-labelledby="registration-title" @submit.prevent="registerAsset">
        <button class="registration-close" type="button" aria-label="关闭" :disabled="creating" @click="closeRegistration"><X :size="18" /></button>
        <p class="eyebrow">NEW {{ meta.english.toUpperCase() }}</p>
        <h2 id="registration-title">登记{{ meta.label }}</h2>
        <p class="registration-note">登记后可继续在详情页维护版本、关联关系与归档文件。</p>

        <label>标题<input v-model="registration.title" required maxlength="500" placeholder="例如：田野样本观测数据集" /></label>
        <label>资产标识（slug）<input v-model="registration.slug" required pattern="[a-z0-9]+(-[a-z0-9]+)*" minlength="3" maxlength="160" placeholder="例如：soil-samples-2026" /></label>
        <label>摘要<textarea v-model="registration.summary" maxlength="5000" rows="3" placeholder="简要说明研究内容、范围或用途"></textarea></label>
        <div class="registration-grid">
          <label>状态<select v-model="registration.status"><option value="draft">draft</option><option value="active">active</option><option value="available">available</option><option value="collected">collected</option></select></label>
          <label>可见范围<select v-model="registration.visibility"><option value="lab">全实验室</option><option value="project">项目成员</option><option value="restricted">受限</option></select></label>
        </div>
        <div class="registration-grid">
          <label>初始版本（可选）<input v-model="registration.version" maxlength="80" placeholder="例如：v0.1" /></label>
          <label>标签（逗号分隔）<input v-model="registration.tags" placeholder="例如：生态, 田野" /></label>
        </div>
        <p v-if="createError" class="registration-error">{{ createError }}</p>
        <footer><button class="button button--outline" type="button" :disabled="creating" @click="closeRegistration">取消</button><button class="button button--primary" :disabled="creating || !registration.title.trim() || !registration.slug.trim()" type="submit"><Save :size="16" />{{ creating ? '正在登记' : '确认登记' }}</button></footer>
      </form>
    </div>

  </div>
</template>

<style scoped>
.registration-backdrop { position: fixed; z-index: 40; inset: 0; display: grid; padding: 20px; place-items: center; background: rgba(23, 34, 26, .48); }
.registration-dialog { position: relative; display: grid; width: min(100%, 610px); max-height: calc(100vh - 40px); padding: 28px; overflow-y: auto; background: #fdfefb; border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 20px 50px rgba(24, 37, 29, .22); gap: 11px; }.registration-dialog h2 { margin: -5px 0 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 24px; font-weight: 500; }.registration-note { margin: -3px 0 7px; color: var(--muted); font-size: 12px; line-height: 1.55; }.registration-dialog label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.registration-dialog input, .registration-dialog textarea, .registration-dialog select { width: 100%; padding: 9px 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.registration-dialog textarea { resize: vertical; }.registration-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.registration-close { position: absolute; top: 13px; right: 13px; display: grid; width: 31px; height: 31px; color: #68776d; place-items: center; background: transparent; border: 0; border-radius: 50%; cursor: pointer; }.registration-close:hover { background: #eef2ed; }.registration-error { margin: 1px 0; color: #a6633b; font-size: 12px; }.registration-dialog footer { display: flex; margin-top: 9px; justify-content: flex-end; gap: 9px; } @media (max-width: 560px) { .registration-dialog { padding: 24px 20px; }.registration-grid { grid-template-columns: 1fr; } }
</style>
