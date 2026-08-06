<script setup lang="ts">
import { ArrowRight, Search, X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getAssets } from '@/api/client'
import { assetMeta } from '@/catalogue'
import AssetIcon from '@/components/AssetIcon.vue'
import type { AssetListResponse } from '@/types'

const route = useRoute()
const router = useRouter()
const query = ref('')
const data = ref<AssetListResponse | null>(null)
const loading = ref(false)
const error = ref('')
let controller: AbortController | undefined

const resultLabel = computed(() => `${data.value?.total ?? 0} 项跨类型资产`)

async function load(nextQuery: string) {
  controller?.abort()
  query.value = nextQuery
  if (!nextQuery) {
    data.value = null
    return
  }
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    data.value = await getAssets(undefined, { query: nextQuery, pageSize: 50 }, controller.signal)
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    error.value = reason instanceof Error ? reason.message : '搜索失败'
  } finally {
    loading.value = false
  }
}

function submit() {
  const value = query.value.trim()
  router.push({ name: 'search', query: value ? { q: value } : {} })
}

function clear() {
  query.value = ''
  router.push({ name: 'search' })
}

watch(
  () => route.query.q,
  (value) => load(typeof value === 'string' ? value.trim() : ''),
  { immediate: true },
)
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="page search-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">SAGE DISCOVERY</p>
        <h1>统一检索</h1>
        <p>一次搜索实验室登记的论文、数据集、文献、项目与模型。</p>
      </div>
    </header>

    <form class="discovery-search" @submit.prevent="submit">
      <Search :size="22" />
      <input v-model="query" autofocus placeholder="输入标题、摘要或关键词" />
      <button v-if="query" type="button" aria-label="清空搜索" @click="clear"><X :size="18" /></button>
      <button class="button button--primary">检索目录</button>
    </form>

    <div v-if="query" class="catalogue-summary search-summary">
      <p><strong>{{ resultLabel }}</strong> 与“{{ query }}”相关</p>
      <span v-if="loading" class="tiny-spinner"></span>
    </div>

    <div v-if="error" class="state-panel state-panel--error state-panel--inline"><strong>检索失败</strong><p>{{ error }}</p></div>
    <div v-else-if="!query" class="empty-catalogue">
      <span><Search :size="30" /></span><h2>从一个研究主题开始</h2><p>例如：气候科学、Transformer、多模态。</p>
    </div>
    <div v-else-if="!loading && data?.items.length === 0" class="empty-catalogue">
      <span><Search :size="30" /></span><h2>没有匹配的资产</h2><p>尝试使用更短的标题或主题词。</p>
    </div>
    <section v-else class="search-results">
      <RouterLink
        v-for="asset in data?.items"
        :key="asset.id"
        :to="`/${assetMeta[asset.type].english.toLowerCase()}?q=${encodeURIComponent(asset.title)}`"
        class="search-result"
      >
        <span class="catalogue-card-icon" :style="{ color: assetMeta[asset.type].color, background: assetMeta[asset.type].softColor }">
          <AssetIcon :type="asset.type" :size="21" />
        </span>
        <span class="search-result-copy">
          <span><em :style="{ color: assetMeta[asset.type].color }">{{ assetMeta[asset.type].label }}</em>{{ asset.title }}</span>
          <small>{{ asset.summary }}</small>
        </span>
        <span class="tag-list"><span v-for="tag in asset.tags.slice(0, 3)" :key="tag">{{ tag }}</span></span>
        <ArrowRight :size="18" />
      </RouterLink>
    </section>
  </div>
</template>

