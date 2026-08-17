<script setup lang="ts">
import { ArchiveRestore, CheckCircle2, CircleAlert, FileSearch, RefreshCw, ShieldAlert } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { getArchiveHealth, runArchiveScan } from '@/api/client'
import { useBranding } from '@/composables/useBranding'
import type { ArchiveHealthSummary } from '@/types'
import { parseApiDate } from '@/utils/dates'

const data = ref<ArchiveHealthSummary | null>(null)
const loading = ref(true)
const running = ref(false)
const loadError = ref('')
const scanError = ref('')
const { pageEyebrow } = useBranding()

async function refreshSummary() {
  data.value = await getArchiveHealth()
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    await refreshSummary()
  } catch (reason) {
    loadError.value = reason instanceof Error ? reason.message : '无法读取归档健康状态'
  } finally {
    loading.value = false
  }
}

async function scan() {
  running.value = true
  scanError.value = ''
  try {
    await runArchiveScan()
    try {
      await refreshSummary()
      loadError.value = ''
    } catch {
      scanError.value = '扫描已完成，但暂时无法刷新健康摘要。请稍后刷新页面查看最新结果。'
    }
  } catch (reason) {
    scanError.value = reason instanceof Error ? reason.message : '扫描未能完成'
    try {
      await refreshSummary()
      loadError.value = ''
    } catch {
      scanError.value += ' 同时无法刷新扫描记录，请稍后刷新页面。'
    }
  } finally {
    running.value = false
  }
}

function formatDate(value: string | null) {
  if (!value) return '进行中'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(parseApiDate(value))
}

const scanStatusLabels = { running: '进行中', completed: '已完成', failed: '失败' } as const

onMounted(load)
</script>

<template>
  <div class="page archive-health-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">{{ pageEyebrow('ARCHIVE INTEGRITY') }}</p>
        <h1>归档健康</h1>
        <p>扫描器只同步文件事实；未匹配文件会保留为待认领，不会自动登记为科研资产。</p>
      </div>
      <button class="button button--primary" :disabled="running || loading || !data?.storage_available" @click="scan">
        <RefreshCw :size="16" :class="{ 'is-spinning': running }" />{{ running ? '正在扫描' : '运行扫描' }}
      </button>
    </header>

    <div v-if="loading && !data" class="state-panel" role="status" aria-live="polite"><span class="loader-ring"></span><p>正在读取扫描索引…</p></div>
    <div v-else-if="loadError && !data" class="state-panel state-panel--error" role="alert"><CircleAlert :size="28" /><strong>归档服务暂不可用</strong><p>{{ loadError }}</p><button class="button button--outline" @click="load">重试</button></div>
    <template v-else-if="data">
      <p v-if="scanError || loadError" class="archive-action-error" role="alert"><CircleAlert :size="17" />{{ scanError || loadError }}</p>
      <section class="archive-metrics">
        <article class="archive-metric"><CheckCircle2 :size="21" /><strong>{{ data.healthy_files }}</strong><span>健康文件</span></article>
        <article class="archive-metric"><FileSearch :size="21" /><strong>{{ data.indexed_files }}</strong><span>已索引文件</span></article>
        <article class="archive-metric" :class="{ warning: data.missing_files > 0 }"><ShieldAlert :size="21" /><strong>{{ data.missing_files }}</strong><span>失效记录</span></article>
        <article class="archive-metric" :class="{ warning: data.unclaimed_files > 0 }"><ArchiveRestore :size="21" /><strong>{{ data.unclaimed_files }}</strong><span>待认领文件</span></article>
      </section>

      <section class="archive-grid">
        <article class="panel archive-status">
          <header class="panel-heading"><div><span class="section-number">01</span><div><h2>存储根状态</h2><p>Read-only storage root</p></div></div></header>
          <div class="archive-status-body">
            <span class="archive-status-icon" :class="{ offline: !data.storage_available }"><CheckCircle2 v-if="data.storage_available" :size="26" /><CircleAlert v-else :size="26" /></span>
            <div><strong>{{ data.storage_available ? '存储根可读取' : '存储根不可用' }}</strong><p>{{ data.storage_available ? '文件路径保持在服务端，浏览器只会得到安全的索引元数据。' : '确认 SAGE_STORAGE_ROOT 已挂载，并在服务端可读取。' }}</p></div>
          </div>
          <div v-if="data.latest_scan" class="latest-scan"><span>最近扫描</span><strong>{{ formatDate(data.latest_scan.completed_at) }}</strong><small>{{ data.latest_scan.message }}</small></div>
          <div v-else class="latest-scan"><span>尚未执行扫描</span><small>当前可用模拟归档目录来验证完整流程。</small></div>
        </article>

        <article class="panel archive-history">
          <header class="panel-heading"><div><span class="section-number">02</span><div><h2>扫描记录</h2><p>Recent scan runs</p></div></div><span class="data-note">{{ data.recent_scans.length }} runs</span></header>
          <div v-if="data.recent_scans.length" class="scan-history">
            <div v-for="scanRun in data.recent_scans" :key="scanRun.id" class="scan-row">
              <span class="scan-state" :class="scanRun.status" aria-hidden="true"></span>
              <div class="scan-copy">
                <div class="scan-heading"><strong>{{ scanRun.source === 'mock-archive' ? '模拟归档扫描' : '存储根扫描' }}</strong><span class="scan-status" :class="scanRun.status">{{ scanStatusLabels[scanRun.status] }}</span></div>
                <p>发现 {{ scanRun.files_discovered }} · 已索引 {{ scanRun.files_indexed }} · 待认领 {{ scanRun.files_unclaimed }}</p>
                <p v-if="scanRun.message" class="scan-message">{{ scanRun.message }}</p>
              </div>
              <time>{{ formatDate(scanRun.completed_at) }}</time>
            </div>
          </div>
          <p v-else class="archive-empty">暂无扫描记录。使用右上角按钮启动第一次扫描。</p>
        </article>
      </section>
      <p class="archive-note">扫描器不会删除物理文件，也不会覆盖资产标题、标签、负责人或其他业务元数据。</p>
    </template>
  </div>
</template>

<style scoped>
.archive-action-error { display: flex; margin: 0 0 14px; padding: 11px 13px; align-items: center; color: #965b38; background: #fff6ef; border-left: 3px solid #bd7750; gap: 8px; font-size: 11px; }.archive-metrics { display: grid; margin-bottom: 14px; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }.archive-metric { display: grid; min-height: 118px; padding: 18px; color: var(--sage); background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 8px; align-content: start; gap: 5px; }.archive-metric.warning { color: #b4772d; }.archive-metric strong { color: var(--ink); font-family: "Iowan Old Style", serif; font-size: 31px; font-weight: 500; }.archive-metric span { color: #849087; font-size: 11px; }.archive-grid { display: grid; grid-template-columns: minmax(280px, .75fr) minmax(0, 1.25fr); gap: 14px; }.archive-status-body { display: flex; padding: 24px 21px; align-items: flex-start; gap: 14px; }.archive-status-icon { display: grid; width: 50px; height: 50px; flex: 0 0 auto; color: var(--sage); place-items: center; background: var(--sage-soft); border-radius: 50%; }.archive-status-icon.offline { color: #a6633b; background: #f7e9e1; }.archive-status-body strong { font-family: "Iowan Old Style", "Songti SC", serif; font-size: 17px; }.archive-status-body p, .latest-scan small, .scan-row p, .archive-empty, .archive-note { color: #7c887f; font-size: 11px; line-height: 1.6; }.archive-status-body p { margin: 7px 0 0; }.latest-scan { display: grid; margin: 0 21px 21px; padding: 13px 0; gap: 4px; border-top: 1px solid #e4e9e2; }.latest-scan span { color: #9aa39d; font-size: 10px; }.latest-scan strong { font-family: "Iowan Old Style", serif; font-size: 14px; }.latest-scan small { margin: 0; }.scan-history { display: grid; }.scan-row { display: grid; min-height: 72px; padding: 13px 20px; align-items: center; grid-template-columns: 18px minmax(0, 1fr) auto; gap: 10px; border-bottom: 1px solid #e6ebe4; }.scan-state { width: 9px; height: 9px; background: var(--sage); border-radius: 50%; }.scan-state.failed { background: #a6633b; }.scan-copy { min-width: 0; }.scan-heading { display: flex; align-items: center; gap: 8px; }.scan-row strong { font-size: 12px; }.scan-status { padding: 2px 6px; color: #42624c; background: #eaf2eb; border-radius: 3px; font-size: 9px; font-weight: 700; }.scan-status.failed { color: #914f31; background: #f8e9e1; }.scan-status.running { color: #846220; background: #f8f0dc; }.scan-row p { margin: 3px 0 0; }.scan-row .scan-message { color: #55645a; overflow-wrap: anywhere; }.scan-row time { color: #98a29c; font-size: 10px; white-space: nowrap; }.archive-empty { margin: 24px 20px; }.archive-note { margin: 16px 2px 0; }.is-spinning { animation: spin 800ms linear infinite; }
@media (max-width: 850px) { .archive-metrics { grid-template-columns: repeat(2, 1fr); }.archive-grid { grid-template-columns: 1fr; } } @media (max-width: 460px) { .archive-metric { min-height: 102px; padding: 15px; }.archive-metric strong { font-size: 27px; }.scan-row { grid-template-columns: 16px 1fr; }.scan-row time { display: none; } }
</style>
