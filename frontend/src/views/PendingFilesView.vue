<script setup lang="ts">
import { Check, CircleAlert, FileQuestion, RefreshCw, Search, X } from '@lucide/vue'
import { useDebounceFn } from '@vueuse/core'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { claimUnclaimedFile, getAssetChoices, getUnclaimedFiles } from '@/api/client'
import { assetMeta } from '@/catalogue'
import { useOverlayFocus } from '@/composables/useOverlayFocus'
import { useBranding } from '@/composables/useBranding'
import type { AssetChoiceSummary, UnclaimedFileSummary } from '@/types'

const files = ref<UnclaimedFileSummary[]>([])
const assetChoices = ref<AssetChoiceSummary[]>([])
const activeFile = ref<UnclaimedFileSummary | null>(null)
const claimDialog = ref<HTMLElement | null>(null)
const selectedAssetId = ref('')
const assetQuery = ref('')
const choicesLoading = ref(false)
const choicesError = ref('')
const submitting = ref(false)
const claimError = ref('')
let choicesController: AbortController | undefined
let filesController: AbortController | undefined

const loading = ref(true)
const error = ref('')
const { pageEyebrow } = useBranding()

const claimOpen = computed(() => Boolean(activeFile.value))
useOverlayFocus(claimOpen, claimDialog, closeClaim)

async function load() {
  filesController?.abort()
  const requestController = new AbortController()
  filesController = requestController
  loading.value = true
  error.value = ''
  try {
    const result = await getUnclaimedFiles(requestController.signal)
    if (filesController !== requestController) return
    files.value = result
  } catch (reason) {
    if (filesController !== requestController) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    error.value = reason instanceof Error ? reason.message : '无法读取待认领文件'
  } finally {
    if (filesController === requestController) loading.value = false
  }
}

function formatBytes(value: number) {
  const units = ['B', 'KB', 'MB', 'GB']
  const index = value ? Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1) : 0
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`
}

async function loadAssetChoices() {
  choicesController?.abort()
  const controller = new AbortController()
  choicesController = controller
  choicesLoading.value = true
  choicesError.value = ''
  try {
    assetChoices.value = await getAssetChoices(assetQuery.value.trim(), controller.signal)
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    assetChoices.value = []
    choicesError.value = reason instanceof Error ? reason.message : '无法读取资产候选项'
  } finally {
    if (choicesController === controller) choicesLoading.value = false
  }
}

const searchAssetChoices = useDebounceFn(() => { void loadAssetChoices() }, 250)

function updateAssetQuery() {
  selectedAssetId.value = ''
  searchAssetChoices()
}

function openClaim(file: UnclaimedFileSummary) {
  activeFile.value = file
  selectedAssetId.value = ''
  assetQuery.value = ''
  assetChoices.value = []
  choicesError.value = ''
  claimError.value = ''
  void loadAssetChoices()
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

onMounted(load)
onBeforeUnmount(() => {
  filesController?.abort()
  choicesController?.abort()
})
</script>

<template>
  <div class="page pending-files-page">
    <header class="page-heading"><div><p class="eyebrow">{{ pageEyebrow('ARCHIVE INTAKE') }}</p><h1>待认领文件</h1><p>这些文件已由扫描器发现，但尚未匹配到实验室登记资产。</p></div><button class="button button--outline" :disabled="loading" @click="load"><RefreshCw :size="16" />刷新列表</button></header>
    <div v-if="loading" class="state-panel" role="status" aria-live="polite"><span class="loader-ring"></span><p>正在读取待认领文件…</p></div>
    <div v-else-if="error" class="state-panel state-panel--error" role="alert"><CircleAlert :size="28" /><strong>读取失败</strong><p>{{ error }}</p><button class="button button--outline" @click="load">重试</button></div>
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
      <section ref="claimDialog" class="claim-dialog" role="dialog" aria-modal="true" aria-labelledby="claim-title" tabindex="-1">
        <button class="claim-close" aria-label="关闭" :disabled="submitting" @click="closeClaim"><X :size="18" /></button>
        <p class="eyebrow">ASSIGN TO REGISTERED ASSET</p>
        <h2 id="claim-title">认领「{{ activeFile.file_name }}」</h2>
        <p>原始文件会保留在 <code>{{ activeFile.relative_path }}</code>，系统只建立与资产的归档关联。</p>
        <label class="claim-select-label" for="claim-asset-search">归属资产</label>
        <div class="claim-search"><Search :size="17" /><input id="claim-asset-search" v-model="assetQuery" autofocus :disabled="submitting" placeholder="搜索资产标题或 slug" autocomplete="off" @input="updateAssetQuery" /><span v-if="choicesLoading" class="tiny-spinner"></span></div>
        <div class="claim-choices" role="listbox" aria-label="资产候选项">
          <button v-for="asset in assetChoices" :key="asset.id" type="button" role="option" :aria-selected="selectedAssetId === asset.id" :class="{ selected: selectedAssetId === asset.id }" @click="selectedAssetId = asset.id"><span><strong>{{ asset.title }}</strong><small>{{ asset.slug }}</small></span><em>{{ assetMeta[asset.type].label }}</em><Check v-if="selectedAssetId === asset.id" :size="16" /></button>
          <p v-if="!choicesLoading && !choicesError && !assetChoices.length">没有匹配的已登记资产。</p>
        </div>
        <p v-if="choicesError" class="claim-error" role="alert">{{ choicesError }} <button type="button" @click="loadAssetChoices">重试</button></p>
        <p v-else-if="claimError" class="claim-error" role="alert">{{ claimError }}</p>
        <footer><button class="button button--outline" :disabled="submitting" @click="closeClaim">取消</button><button class="button button--primary" :disabled="submitting || !selectedAssetId" @click="claim"><Check :size="16" />{{ submitting ? '正在认领' : '确认认领' }}</button></footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.pending-list { display: grid; background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.pending-row { display: grid; min-height: 74px; padding: 13px 18px; align-items: center; grid-template-columns: 28px minmax(0, 1fr) 100px 80px auto; gap: 12px; border-bottom: 1px solid #e6ebe4; }
.pending-row:last-child { border-bottom: 0; }
.pending-row svg { color: #a96f29; }
.pending-row > div { min-width: 0; }
.pending-row strong { display: block; overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.pending-row p { margin: 4px 0 0; overflow: hidden; color: #748178; font-family: ui-monospace, "SFMono-Regular", monospace; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.pending-row span, .pending-row time { color: #68766d; font-size: 11px; white-space: nowrap; }
.pending-files-page :deep(.page-heading > .button), .button--compact { min-height: 44px; }.button--compact { min-width: 58px; padding: 0 11px; white-space: nowrap; font-size: 11px; }
.claim-backdrop { position: fixed; z-index: 40; inset: 0; display: grid; padding: 20px; place-items: center; background: rgba(23, 34, 26, .48); }
.claim-dialog { position: relative; width: min(100%, 500px); max-height: calc(100dvh - 40px); padding: 28px; overflow-y: auto; overscroll-behavior: contain; background: #fdfefb; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 20px 50px rgba(24, 37, 29, .22); }
.claim-dialog h2 { margin: 5px 0 12px; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 22px; font-weight: 500; }.claim-dialog > p { color: #718077; font-size: 12px; line-height: 1.65; }.claim-dialog code { color: #56705d; font-size: 11px; }.claim-close { position: absolute; top: 13px; right: 13px; display: grid; width: 44px; height: 44px; color: #68776d; place-items: center; background: transparent; border: 0; border-radius: 50%; cursor: pointer; }.claim-close:hover { background: #eef2ed; }.claim-select-label { display: block; margin: 20px 0 7px; color: #526056; font-size: 12px; font-weight: 700; }.claim-search { display: flex; min-height: 44px; padding: 0 11px; align-items: center; gap: 8px; color: #748178; background: #fff; border: 1px solid var(--line); border-radius: 5px; }.claim-search:focus-within { border-color: var(--sage); box-shadow: 0 0 0 3px var(--sage-soft); }.claim-search input { min-width: 0; flex: 1; background: transparent; border: 0; outline: 0; }.claim-choices { display: grid; max-height: 230px; margin-top: 8px; overflow-y: auto; background: #fff; border: 1px solid var(--line); border-radius: 5px; }.claim-choices > button { display: grid; min-height: 52px; padding: 8px 10px; align-items: center; color: var(--ink); text-align: left; background: transparent; border: 0; border-bottom: 1px solid #e8ece6; grid-template-columns: minmax(0, 1fr) auto 18px; gap: 8px; cursor: pointer; }.claim-choices > button:last-of-type { border-bottom: 0; }.claim-choices > button:hover, .claim-choices > button.selected { background: var(--sage-soft); }.claim-choices span { min-width: 0; }.claim-choices strong, .claim-choices small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.claim-choices strong { font-size: 12px; }.claim-choices small { margin-top: 3px; color: #758179; font-family: ui-monospace, "SFMono-Regular", monospace; font-size: 10px; }.claim-choices em { color: #617068; font-size: 10px; font-style: normal; }.claim-choices > p { margin: 0; padding: 20px 12px; color: #748178; text-align: center; font-size: 11px; }.claim-error { margin: 10px 0 0; color: #a6633b !important; }.claim-error button { color: inherit; text-decoration: underline; background: transparent; border: 0; cursor: pointer; }.claim-dialog footer { display: flex; margin-top: 23px; justify-content: flex-end; gap: 9px; }
@media (max-width: 600px) { .claim-backdrop { padding: 12px; }.pending-row { padding: 13px 14px; grid-template-columns: 25px minmax(0, 1fr) auto; gap: 9px; }.pending-row span, .pending-row time { display: none; }.claim-dialog { max-height: calc(100dvh - 24px); padding: 24px 20px; } }
</style>
