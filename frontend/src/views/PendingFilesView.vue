<script setup lang="ts">
import { CircleAlert, FileQuestion, RefreshCw } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { getUnclaimedFiles } from '@/api/client'
import type { UnclaimedFileSummary } from '@/types'

const files = ref<UnclaimedFileSummary[]>([])
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    files.value = await getUnclaimedFiles()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取待认领文件'
  } finally {
    loading.value = false
  }
}

function formatBytes(value: number) {
  const units = ['B', 'KB', 'MB', 'GB']
  const index = value ? Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1) : 0
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`
}

onMounted(load)
</script>

<template>
  <div class="page pending-files-page">
    <header class="page-heading"><div><p class="eyebrow">SAGE ARCHIVE INTAKE</p><h1>待认领文件</h1><p>这些文件已由扫描器发现，但尚未匹配到实验室登记资产。</p></div><button class="button button--outline" @click="load"><RefreshCw :size="16" />刷新列表</button></header>
    <div v-if="loading" class="state-panel"><span class="loader-ring"></span><p>正在读取待认领文件…</p></div>
    <div v-else-if="error" class="state-panel state-panel--error"><CircleAlert :size="28" /><strong>读取失败</strong><p>{{ error }}</p><button class="button button--outline" @click="load">重试</button></div>
    <div v-else-if="!files.length" class="empty-catalogue"><span><FileQuestion :size="30" /></span><h2>没有待认领文件</h2><p>下一次扫描发现无法匹配路径的文件时，会显示在这里。</p></div>
    <section v-else class="pending-list"><article v-for="file in files" :key="file.id" class="pending-row"><FileQuestion :size="21" /><div><strong>{{ file.file_name }}</strong><p>{{ file.relative_path }}</p></div><span>{{ file.file_kind }}</span><time>{{ formatBytes(file.file_size) }}</time></article></section>
  </div>
</template>

<style scoped>
.pending-list { display: grid; background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 10px; }.pending-row { display: grid; min-height: 74px; padding: 13px 18px; align-items: center; grid-template-columns: 28px minmax(0, 1fr) 100px 80px; gap: 12px; border-bottom: 1px solid #e6ebe4; }.pending-row:last-child { border-bottom: 0; }.pending-row svg { color: #b4772d; }.pending-row strong { font-size: 12px; }.pending-row p { margin: 4px 0 0; overflow: hidden; color: #849087; font-family: monospace; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.pending-row span, .pending-row time { color: #79857d; font-size: 11px; } @media (max-width: 600px) { .pending-row { grid-template-columns: 25px minmax(0, 1fr) auto; }.pending-row span { display: none; } }
</style>
