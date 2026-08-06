<script setup lang="ts">
import { Check, CircleAlert, FileQuestion, RefreshCw, X } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { claimUnclaimedFile, getAssets, getUnclaimedFiles } from '@/api/client'
import type { AssetSummary, UnclaimedFileSummary } from '@/types'

const files = ref<UnclaimedFileSummary[]>([])
const assets = ref<AssetSummary[]>([])
const activeFile = ref<UnclaimedFileSummary | null>(null)
const selectedAssetId = ref('')
const submitting = ref(false)
const claimError = ref('')

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

async function loadAssets() {
  try {
    const result = await getAssets(undefined, { pageSize: 100 })
    assets.value = result.items
  } catch {
    assets.value = []
  }
}

function openClaim(file: UnclaimedFileSummary) {
  activeFile.value = file
  selectedAssetId.value = assets.value[0]?.id ?? ''
  claimError.value = ''
}

function closeClaim() {
  if (!submitting.value) activeFile.value = null
}

async function claim() {
  if (!activeFile.value || !selectedAssetId.value) return
  submitting.value = true
  claimError.value = ''
  try {
    await claimUnclaimedFile(activeFile.value.id, selectedAssetId.value)
    files.value = files.value.filter((file) => file.id !== activeFile.value?.id)
    activeFile.value = null
  } catch (reason) {
    claimError.value = reason instanceof Error ? reason.message : '认领失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

onMounted(() => { void load(); void loadAssets() })
</script>

<template>
  <div class="page pending-files-page">
    <header class="page-heading"><div><p class="eyebrow">SAGE ARCHIVE INTAKE</p><h1>待认领文件</h1><p>这些文件已由扫描器发现，但尚未匹配到实验室登记资产。</p></div><button class="button button--outline" @click="load"><RefreshCw :size="16" />刷新列表</button></header>
    <div v-if="loading" class="state-panel"><span class="loader-ring"></span><p>正在读取待认领文件…</p></div>
    <div v-else-if="error" class="state-panel state-panel--error"><CircleAlert :size="28" /><strong>读取失败</strong><p>{{ error }}</p><button class="button button--outline" @click="load">重试</button></div>
    <div v-else-if="!files.length" class="empty-catalogue"><span><FileQuestion :size="30" /></span><h2>没有待认领文件</h2><p>下一次扫描发现无法匹配路径的文件时，会显示在这里。</p></div>
    <section v-else class="pending-list">
      <article v-for="file in files" :key="file.id" class="pending-row">
        <FileQuestion :size="21" />
        <div><strong>{{ file.file_name }}</strong><p>{{ file.relative_path }}</p></div>
        <span>{{ file.file_kind }}</span><time>{{ formatBytes(file.file_size) }}</time>
        <button class="button button--outline button--compact" @click="openClaim(file)">认领</button>
      </article>
    </section>

    <div v-if="activeFile" class="claim-backdrop" @click.self="closeClaim">
      <section class="claim-dialog" role="dialog" aria-modal="true" aria-labelledby="claim-title">
        <button class="claim-close" aria-label="关闭" :disabled="submitting" @click="closeClaim"><X :size="18" /></button>
        <p class="eyebrow">ASSIGN TO REGISTERED ASSET</p>
        <h2 id="claim-title">认领「{{ activeFile.file_name }}」</h2>
        <p>原始文件会保留在 <code>{{ activeFile.relative_path }}</code>，系统只建立与资产的归档关联。</p>
        <label class="claim-select-label" for="claim-asset">归属资产</label>
        <select id="claim-asset" v-model="selectedAssetId" class="claim-select" :disabled="submitting || !assets.length">
          <option value="" disabled>请选择已登记资产</option>
          <option v-for="asset in assets" :key="asset.id" :value="asset.id">{{ asset.title }} · {{ asset.type }}</option>
        </select>
        <p v-if="!assets.length" class="claim-error">当前无法读取可认领的资产，请刷新页面后重试。</p>
        <p v-else-if="claimError" class="claim-error">{{ claimError }}</p>
        <footer><button class="button button--outline" :disabled="submitting" @click="closeClaim">取消</button><button class="button button--primary" :disabled="submitting || !selectedAssetId" @click="claim"><Check :size="16" />{{ submitting ? '正在认领' : '确认认领' }}</button></footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.pending-list { display: grid; background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 10px; }.pending-row { display: grid; min-height: 74px; padding: 13px 18px; align-items: center; grid-template-columns: 28px minmax(0, 1fr) 100px 80px; gap: 12px; border-bottom: 1px solid #e6ebe4; }.pending-row:last-child { border-bottom: 0; }.pending-row svg { color: #b4772d; }.pending-row strong { font-size: 12px; }.pending-row p { margin: 4px 0 0; overflow: hidden; color: #849087; font-family: monospace; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.pending-row span, .pending-row time { color: #79857d; font-size: 11px; } @media (max-width: 600px) { .pending-row { grid-template-columns: 25px minmax(0, 1fr) auto; }.pending-row span { display: none; } }
.button--compact { justify-self: end; min-height: 32px; padding: 0 11px; font-size: 11px; }
.claim-backdrop { position: fixed; z-index: 10; inset: 0; display: grid; padding: 20px; place-items: center; background: rgba(23, 34, 26, .48); }
.claim-dialog { position: relative; width: min(100%, 500px); padding: 28px; background: #fdfefb; border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 20px 50px rgba(24, 37, 29, .22); }
.claim-dialog h2 { margin: 5px 0 12px; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 22px; font-weight: 500; }.claim-dialog > p { color: #718077; font-size: 12px; line-height: 1.65; }.claim-dialog code { color: #56705d; font-size: 11px; }.claim-close { position: absolute; top: 13px; right: 13px; display: grid; width: 31px; height: 31px; color: #68776d; place-items: center; background: transparent; border: 0; border-radius: 50%; cursor: pointer; }.claim-close:hover { background: #eef2ed; }.claim-select-label { display: block; margin: 20px 0 7px; color: #526056; font-size: 12px; font-weight: 700; }.claim-select { width: 100%; height: 40px; padding: 0 10px; color: var(--ink); background: #fff; border: 1px solid var(--line); border-radius: 5px; }.claim-error { margin: 10px 0 0; color: #a6633b !important; }.claim-dialog footer { display: flex; margin-top: 23px; justify-content: flex-end; gap: 9px; }
@media (max-width: 600px) { .pending-row { grid-template-columns: 25px minmax(0, 1fr) auto; }.pending-row span, .pending-row time { display: none; }.claim-dialog { padding: 24px 20px; } }
</style>
