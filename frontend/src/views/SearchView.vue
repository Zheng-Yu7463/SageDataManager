<script setup lang="ts">
import { ArrowLeft, ArrowRight, Search, X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getAssets } from '@/api/client'
import { assetMeta } from '@/catalogue'
import AssetIcon from '@/components/AssetIcon.vue'
import { useBranding } from '@/composables/useBranding'
import { isPublicationMetadata } from '@/types'
import type { AssetListResponse, AssetSummary } from '@/types'

const route = useRoute()
const router = useRouter()
const inputQuery = ref('')
const activeQuery = ref('')
const data = ref<AssetListResponse | null>(null)
const loading = ref(false)
const error = ref('')
const page = ref(1)
const pageSize = 20
const { pageEyebrow } = useBranding()
let controller: AbortController | undefined

const resultLabel = computed(() => `${data.value?.total ?? 0} 项跨类型资产`)
const pageCount = computed(() => Math.max(1, Math.ceil((data.value?.total ?? 0) / pageSize)))

function resultContext(asset: AssetSummary) {
  if (['paper', 'literature'].includes(asset.type) && isPublicationMetadata(asset.details)) {
    const authors = asset.details.authors.slice(0, 3).join('、')
    const remainder = asset.details.authors.length > 3 ? ` 等 ${asset.details.authors.length} 位作者` : ''
    return `${authors}${remainder} · ${asset.details.venue} ${asset.details.year}`
  }
  return `负责人：${asset.owner.name}`
}

async function load(nextQuery: string, nextPage: number) {
  controller?.abort()
  activeQuery.value = nextQuery
  inputQuery.value = nextQuery
  page.value = nextPage
  if (!nextQuery) {
    controller = undefined
    data.value = null
    error.value = ''
    loading.value = false
    return
  }
  const requestController = new AbortController()
  controller = requestController
  loading.value = true
  error.value = ''
  try {
    const result = await getAssets(undefined, { query: nextQuery, page: nextPage, pageSize }, requestController.signal)
    if (controller !== requestController) return
    const lastPage = Math.max(1, Math.ceil(result.total / pageSize))
    if (nextPage > lastPage) {
      await router.replace({
        name: 'search',
        query: lastPage > 1 ? { q: nextQuery, page: String(lastPage) } : { q: nextQuery },
      })
      return
    }
    data.value = result
  } catch (reason) {
    if (controller !== requestController) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    error.value = reason instanceof Error ? reason.message : '搜索失败'
  } finally {
    if (controller === requestController) loading.value = false
  }
}

function submit() {
  const value = inputQuery.value.trim()
  if (value === activeQuery.value) {
    void load(value, page.value)
    return
  }
  void router.push({ name: 'search', query: value ? { q: value } : {} })
}

function clear() {
  inputQuery.value = ''
  router.push({ name: 'search' })
}

function goToPage(nextPage: number) {
  void router.push({ name: 'search', query: { q: activeQuery.value, page: String(nextPage) } })
}

watch(
  () => [route.query.q, route.query.page],
  ([queryValue, pageValue]) => {
    const nextQuery = typeof queryValue === 'string' ? queryValue.trim() : ''
    const parsedPage = typeof pageValue === 'string' && /^\d+$/.test(pageValue) ? Number(pageValue) : 1
    if (pageValue !== undefined && parsedPage < 1) {
      return router.replace({ name: 'search', query: nextQuery ? { q: nextQuery } : {} })
    }
    return load(nextQuery, parsedPage)
  },
  { immediate: true },
)
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="page search-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">{{ pageEyebrow('DISCOVERY') }}</p>
        <h1>统一检索</h1>
        <p>一次搜索实验室登记的论文、数据集、文献、项目与模型。</p>
      </div>
    </header>

    <form class="discovery-search" @submit.prevent="submit">
      <Search :size="22" />
      <input v-model="inputQuery" aria-label="统一检索关键词" autofocus placeholder="输入标题、摘要或关键词" />
      <button v-if="inputQuery" type="button" aria-label="清空搜索" @click="clear"><X :size="18" /></button>
      <button class="button button--primary">检索目录</button>
    </form>

    <div v-if="activeQuery" class="catalogue-summary search-summary">
      <p><strong>{{ resultLabel }}</strong> 与“{{ activeQuery }}”相关</p>
      <span v-if="loading" class="tiny-spinner"></span>
    </div>

    <div v-if="error" class="state-panel state-panel--error state-panel--inline" role="alert"><strong>检索失败</strong><p>{{ error }}</p><button class="button button--outline" @click="load(activeQuery, page)">重试</button></div>
    <div v-if="!activeQuery" class="empty-catalogue">
      <span><Search :size="30" /></span><h2>从一个研究主题开始</h2><p>例如：气候科学、Transformer、多模态。</p>
    </div>
    <div v-else-if="loading && !data" class="state-panel" role="status" aria-live="polite"><span class="loader-ring"></span><p>正在检索目录…</p></div>
    <div v-else-if="!loading && !error && data?.items.length === 0" class="empty-catalogue">
      <span><Search :size="30" /></span><h2>没有匹配的资产</h2><p>尝试使用更短的标题或主题词。</p>
    </div>
    <section v-if="data?.items.length" class="search-results">
      <RouterLink
        v-for="asset in data?.items"
        :key="asset.id"
        :to="{ name: 'asset-detail', params: { assetId: asset.id }, query: { returnTo: route.fullPath } }"
        class="search-result"
        :aria-label="`查看${assetMeta[asset.type].label}：${asset.title}`"
      >
        <span class="catalogue-card-icon" :style="{ color: assetMeta[asset.type].color, background: assetMeta[asset.type].softColor }">
          <AssetIcon :type="asset.type" :size="21" />
        </span>
        <span class="search-result-copy">
          <span><em :style="{ color: assetMeta[asset.type].color }">{{ assetMeta[asset.type].label }}</em>{{ asset.title }}</span>
          <small>{{ asset.summary }}</small>
          <small class="search-result-context">{{ resultContext(asset) }}</small>
        </span>
        <span class="tag-list"><span v-for="tag in asset.tags.slice(0, 3)" :key="tag">{{ tag }}</span></span>
        <ArrowRight :size="18" />
      </RouterLink>
    </section>
    <nav v-if="data && pageCount > 1" class="pagination" aria-label="搜索结果分页">
      <button :disabled="page <= 1 || loading" @click="goToPage(page - 1)"><ArrowLeft :size="14" />上一页</button>
      <span>第 {{ page }} / {{ pageCount }} 页</span>
      <button :disabled="page >= pageCount || loading" @click="goToPage(page + 1)">下一页<ArrowRight :size="14" /></button>
    </nav>
  </div>
</template>
