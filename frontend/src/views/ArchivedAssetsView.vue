<script setup lang="ts">
import { Archive, ArchiveRestore, CircleAlert, RefreshCw } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { getArchivedAssets, restoreAsset } from '@/api/client'
import AssetIcon from '@/components/AssetIcon.vue'
import { assetMeta } from '@/catalogue'
import { useBranding } from '@/composables/useBranding'
import type { AssetSummary } from '@/types'

const assets = ref<AssetSummary[]>([])
const loading = ref(true)
const error = ref('')
const restoringId = ref<string | null>(null)
const { pageEyebrow } = useBranding()

async function load() {
  loading.value = true
  error.value = ''
  try {
    assets.value = await getArchivedAssets()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取已归档资产'
  } finally {
    loading.value = false
  }
}

async function restore(asset: AssetSummary) {
  restoringId.value = asset.id
  error.value = ''
  try {
    await restoreAsset(asset.id)
    assets.value = assets.value.filter((item) => item.id !== asset.id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法恢复资产'
  } finally {
    restoringId.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="page archived-page">
    <header class="page-heading">
      <div><p class="eyebrow">{{ pageEyebrow('RETENTION') }}</p><h1>已归档资产</h1><p>归档资产不会出现在普通目录中，原始文件和元数据均被保留，可随时恢复。</p></div>
      <button class="button button--outline" :disabled="loading" @click="load"><RefreshCw :size="16" />刷新</button>
    </header>
    <div v-if="loading" class="state-panel" role="status" aria-live="polite"><span class="loader-ring"></span><p>正在读取归档资产…</p></div>
    <div v-else-if="error && !assets.length" class="state-panel state-panel--error" role="alert"><CircleAlert :size="28" /><strong>无法读取归档资产</strong><p>{{ error }}</p><button class="button button--outline" @click="load">重试</button></div>
    <div v-else-if="!assets.length" class="archived-empty"><Archive :size="34" /><h2>暂无已归档资产</h2><p>从资产详情页归档的记录会保留在这里，方便恢复。</p></div>
    <section v-else class="archived-list">
      <p v-if="error" class="archived-error">{{ error }}</p>
      <article v-for="asset in assets" :key="asset.id" class="archived-row">
        <span class="archived-icon"><AssetIcon :type="asset.type" :size="21" /></span>
        <div><p class="eyebrow">{{ assetMeta[asset.type].english.toUpperCase() }}</p><h2>{{ asset.title }}</h2><p>{{ asset.summary || '未附摘要' }}</p></div>
        <span class="archived-data">{{ asset.file_count }} 个文件 · {{ asset.tags.join('、') || '无标签' }}</span>
        <button class="button button--primary" :disabled="restoringId === asset.id" @click="restore(asset)"><ArchiveRestore :size="16" />{{ restoringId === asset.id ? '正在恢复' : '恢复资产' }}</button>
      </article>
    </section>
  </div>
</template>

<style scoped>
.archived-list { display: grid; overflow: hidden; background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 8px; }.archived-row { display: grid; min-height: 82px; padding: 15px 18px; align-items: center; grid-template-columns: 42px minmax(0, 1fr) auto auto; gap: 14px; border-bottom: 1px solid var(--line); }.archived-row:last-child { border-bottom: 0; }.archived-icon { display: grid; width: 40px; height: 40px; color: #7c887f; place-items: center; background: #eef1ed; border-radius: 8px; }.archived-row h2 { margin: 3px 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 16px; font-weight: 500; }.archived-row p:not(.eyebrow) { display: -webkit-box; max-width: 520px; margin: 0; overflow: hidden; color: var(--muted); font-size: 11px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 1; }.archived-data { color: #7c887f; font-size: 10px; white-space: nowrap; }.archived-empty { display: grid; min-height: 330px; place-items: center; align-content: center; text-align: center; background: rgba(252,253,249,.8); border: 1px dashed #cbd4ca; border-radius: 8px; }.archived-empty svg { color: #829087; }.archived-empty h2 { margin: 12px 0 4px; font-family: "Iowan Old Style", "Songti SC", serif; }.archived-empty p { margin: 0; color: #7c887f; font-size: 12px; }.archived-error { margin: 0; padding: 12px 18px; color: #a6633b; font-size: 12px; } @media (max-width: 720px) { .archived-row { grid-template-columns: 42px minmax(0, 1fr); }.archived-data { grid-column: 2; }.archived-row button { grid-column: 2; width: fit-content; } }
</style>
