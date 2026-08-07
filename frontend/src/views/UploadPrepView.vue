<script setup lang="ts">
import { Check, CircleAlert, Copy, FolderUp, RefreshCw } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { getAssets, getUploadCommand } from '@/api/client'
import type { AssetSummary, UploadCommandResult } from '@/types'

const assets = ref<AssetSummary[]>([])
const loading = ref(true)
const error = ref('')
const generating = ref(false)
const copied = ref(false)
const result = ref<UploadCommandResult | null>(null)
const form = ref({
  assetId: '',
  sourcePath: '/path/to/local/file-or-directory',
  targetSubdirectory: 'incoming',
  recursive: false,
})

async function loadAssets() {
  loading.value = true
  error.value = ''
  try {
    const response = await getAssets(undefined, { pageSize: 100 })
    assets.value = response.items
    form.value.assetId = response.items[0]?.id ?? ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取资产目录'
  } finally {
    loading.value = false
  }
}

async function generate() {
  if (!form.value.assetId || !form.value.sourcePath.trim()) return
  generating.value = true
  error.value = ''
  copied.value = false
  try {
    result.value = await getUploadCommand({
      asset_id: form.value.assetId,
      source_path: form.value.sourcePath.trim(),
      target_subdirectory: form.value.targetSubdirectory.trim(),
      recursive: form.value.recursive,
    })
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法生成上传命令'
  } finally {
    generating.value = false
  }
}

async function copyCommand() {
  if (!result.value) return
  try {
    await navigator.clipboard.writeText(result.value.command)
    copied.value = true
  } catch {
    error.value = '浏览器无法写入剪贴板，请手动复制命令。'
  }
}

onMounted(loadAssets)
</script>

<template>
  <div class="page upload-prep-page">
    <header class="page-heading">
      <div><p class="eyebrow">SAGE ARCHIVE INTAKE</p><h1>上传准备</h1><p>选择归属资产后生成 SCP 命令，在保存文件的电脑终端中执行即可上传。</p></div>
      <button class="button button--outline" :disabled="loading" @click="loadAssets"><RefreshCw :size="16" />刷新资产</button>
    </header>

    <div v-if="loading" class="state-panel"><span class="loader-ring"></span><p>正在读取可上传的资产…</p></div>
    <div v-else-if="error && !assets.length" class="state-panel state-panel--error"><CircleAlert :size="28" /><strong>无法准备上传</strong><p>{{ error }}</p><button class="button button--outline" @click="loadAssets">重试</button></div>
    <section v-else class="upload-panel">
      <header><span><FolderUp :size="22" /></span><div><h2>生成传输命令</h2><p>网站不会读取你的本地文件；源路径只会写入命令文本。</p></div></header>
      <form @submit.prevent="generate">
        <label>归属资产<select v-model="form.assetId" required><option v-for="asset in assets" :key="asset.id" :value="asset.id">{{ asset.title }} · {{ asset.type }}</option></select></label>
        <label>本机文件或目录路径<input v-model="form.sourcePath" required placeholder="/path/to/local/file" /></label>
        <label>资产内目标子目录<input v-model="form.targetSubdirectory" required placeholder="incoming 或 raw/2026-08" /></label>
        <label class="recursive"><input v-model="form.recursive" type="checkbox" /> 上传整个目录（添加 <code>-r</code>）</label>
        <p v-if="error" class="upload-error">{{ error }}</p>
        <button class="button button--primary" :disabled="generating || !form.assetId || !form.sourcePath.trim()" type="submit"><FolderUp :size="16" />{{ generating ? '正在生成' : '生成 SCP 命令' }}</button>
      </form>
    </section>

    <section v-if="result" class="command-panel">
      <header><div><p class="eyebrow">READY TO TRANSFER</p><h2>{{ result.asset_title }}</h2><p>目标归档路径：<code>{{ result.archive_relative_path }}</code></p></div><button class="button button--outline" @click="copyCommand"><Check v-if="copied" :size="16" /><Copy v-else :size="16" />{{ copied ? '已复制' : '复制命令' }}</button></header>
      <pre><code>{{ result.command }}</code></pre>
      <p class="command-note">命令会先在服务器创建目标目录，再传输文件。完成后请到“归档健康”运行扫描，文件就会出现在该资产下。</p>
    </section>
  </div>
</template>

<style scoped>
.upload-panel, .command-panel { max-width: 840px; padding: 24px; background: rgba(252,253,249,.94); border: 1px solid var(--line); border-radius: 11px; }.upload-panel header, .command-panel header { display: flex; align-items: flex-start; gap: 12px; }.upload-panel header > span { display: grid; width: 42px; height: 42px; color: #416fab; place-items: center; background: #e7eef8; border-radius: 10px; }.upload-panel h2, .command-panel h2 { margin: 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 20px; font-weight: 500; }.upload-panel header p, .command-panel header p, .command-note { margin: 5px 0 0; color: var(--muted); font-size: 12px; line-height: 1.55; }.upload-panel form { display: grid; margin-top: 23px; gap: 14px; }.upload-panel label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.upload-panel select, .upload-panel input:not([type="checkbox"]) { width: 100%; height: 40px; padding: 0 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.upload-panel .recursive { display: flex; align-items: center; color: #637068; font-weight: 500; gap: 7px; }.upload-panel .recursive input { accent-color: var(--sage); }.upload-panel .button { width: fit-content; margin-top: 2px; }.upload-error { margin: 0; color: #a6633b; font-size: 12px; }.command-panel { margin-top: 15px; }.command-panel header { justify-content: space-between; gap: 15px; }.command-panel pre { margin: 18px 0 0; padding: 16px; overflow-x: auto; color: #dfeade; background: #17221b; border-radius: 7px; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }.command-panel code { color: #496a54; }.command-panel pre code { color: inherit; }.command-note { margin-top: 13px; }
</style>
