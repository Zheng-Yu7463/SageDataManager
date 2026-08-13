<script setup lang="ts">
import { CheckCircle2, CircleAlert, FileJson, FileSpreadsheet, Upload } from '@lucide/vue'
import Papa from 'papaparse'
import { ref } from 'vue'

import { importAssets, importAssetsYaml } from '@/api/client'
import { useBranding } from '@/composables/useBranding'
import type { AssetCreateInput } from '@/types'

const source = ref(`[
  {
    "type": "dataset",
    "slug": "example-dataset-2026",
    "title": "示例数据集",
    "summary": "请替换为真实摘要。",
    "status": "draft",
    "visibility": "lab",
    "version": "v0.1",
    "tags": ["示例"],
    "details": {}
  }
]`)
const importing = ref(false)
const sourceFormat = ref<'json' | 'yaml'>('json')
const error = ref('')
const created = ref<string[]>([])
const { pageEyebrow } = useBranding()

function parseAssets(): AssetCreateInput[] {
  const parsed: unknown = JSON.parse(source.value)
  const assets = Array.isArray(parsed)
    ? parsed
    : typeof parsed === 'object' && parsed !== null && 'assets' in parsed
      ? (parsed as { assets: unknown }).assets
      : null
  if (!Array.isArray(assets)) throw new Error('请输入资产数组，或包含 assets 数组的 JSON 对象。')
  return assets as AssetCreateInput[]
}

function parseCsv(text: string): AssetCreateInput[] {
  const result = Papa.parse<Record<string, string>>(text.replace(/^\uFEFF/, ''), {
    header: true,
    skipEmptyLines: 'greedy',
    transformHeader: (header) => header.trim(),
  })
  if (result.errors.length) {
    const first = result.errors[0]
    throw new Error(`CSV 第 ${(first.row ?? 0) + 2} 行格式无效：${first.message}`)
  }
  const headers = result.meta.fields ?? []
  for (const required of ['type', 'slug', 'title']) if (!headers.includes(required)) throw new Error(`CSV 缺少必填列：${required}`)
  return result.data.map((row, index) => {
    const value = (key: string) => row[key]?.trim() ?? ''
    let details: Record<string, unknown> = {}
    if (value('details')) {
      try {
        const parsedDetails: unknown = JSON.parse(value('details'))
        if (typeof parsedDetails !== 'object' || parsedDetails === null || Array.isArray(parsedDetails)) throw new Error()
        details = parsedDetails as Record<string, unknown>
      } catch {
        throw new Error(`CSV 第 ${index + 2} 行 details 必须是 JSON 对象。`)
      }
    }
    return { type: value('type') as AssetCreateInput['type'], slug: value('slug'), title: value('title'), summary: value('summary'), status: value('status') || 'draft', visibility: (value('visibility') || 'lab') as AssetCreateInput['visibility'], version: value('version') || null, tags: value('tags').split('|').filter(Boolean), details }
  })
}

async function readImportFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const content = await file.text()
    sourceFormat.value = /\.ya?ml$/i.test(file.name) ? 'yaml' : 'json'
    source.value = file.name.toLowerCase().endsWith('.csv') ? JSON.stringify(parseCsv(content), null, 2) : content
    error.value = ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取导入文件'
  }
  ;(event.target as HTMLInputElement).value = ''
}

async function submit() {
  error.value = ''
  created.value = []
  let assets: AssetCreateInput[]
  if (sourceFormat.value === 'yaml') {
    importing.value = true
    try { const result = await importAssetsYaml(source.value); created.value = result.created.map((asset) => asset.title) }
    catch (reason) { error.value = reason instanceof Error ? reason.message : 'YAML 导入失败' }
    finally { importing.value = false }
    return
  }
  try { assets = parseAssets() } catch (reason) { error.value = reason instanceof Error ? reason.message : 'JSON 格式无效'; return }
  if (!assets.length) { error.value = '至少需要一条资产记录。'; return }
  importing.value = true
  try {
    const result = await importAssets(assets)
    created.value = result.created.map((asset) => asset.title)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导入失败'
  } finally { importing.value = false }
}
</script>

<template>
  <div class="page import-page">
    <header class="page-heading"><div><p class="eyebrow">{{ pageEyebrow('METADATA INTAKE') }}</p><h1>批量导入资产</h1><p>导入仅登记元数据。文件请在资产创建后通过对应行的安全上传入口传输并完成检测入库。</p></div></header>
    <section class="import-grid"><article class="panel import-panel"><header class="panel-heading"><div><span class="section-number">01</span><div><h2>导入 JSON、CSV 或 YAML</h2><p>最多 100 条</p></div></div><FileJson :size="20" /></header><div class="import-source-controls"><div class="format-switch" role="group" aria-label="导入内容格式"><button type="button" :aria-pressed="sourceFormat === 'json'" :class="{ active: sourceFormat === 'json' }" :disabled="importing" @click="sourceFormat = 'json'">JSON</button><button type="button" :aria-pressed="sourceFormat === 'yaml'" :class="{ active: sourceFormat === 'yaml' }" :disabled="importing" @click="sourceFormat = 'yaml'">YAML</button></div><label class="import-file-picker"><FileSpreadsheet :size="16" />从导入文件载入<input type="file" accept=".json,.csv,.yaml,.yml,application/json,text/csv,text/yaml" :disabled="importing" @change="readImportFile" /></label></div><textarea v-model="source" aria-label="导入数据内容" spellcheck="false" :disabled="importing"></textarea><p class="import-note">JSON 支持数组或 <code>{ "assets": [...] }</code>。CSV 必填列为 <code>type,slug,title</code>；可选 tags 用 <code>|</code> 分隔，details 为 JSON 对象。YAML 支持同样的数组或 <code>assets:</code> 结构。</p><p v-if="error" class="import-error" role="alert"><CircleAlert :size="16" />{{ error }}</p><button class="button button--primary" :disabled="importing" @click="submit"><Upload :size="16" />{{ importing ? '正在导入' : '验证并导入' }}</button></article><aside class="panel import-side"><header class="panel-heading"><div><span class="section-number">02</span><div><h2>安全规则</h2><p>Atomic import</p></div></div></header><ul><li>先校验整批内容、字段与 slug。</li><li>发现重复 slug 时不会创建任何记录。</li><li>不会读取、移动或上传本机文件。</li><li>成功后进入分类页生成 SCP 指令。</li></ul><div v-if="created.length" class="import-success"><CheckCircle2 :size="21" /><strong>已创建 {{ created.length }} 条资产</strong><p>{{ created.join('、') }}</p></div></aside></section>
  </div>
</template>

<style scoped>
.import-source-controls { display:flex; align-items:center; justify-content:space-between; gap:12px; }.format-switch { display:inline-grid; padding:3px; grid-template-columns:1fr 1fr; background:#eef2ed; border:1px solid var(--line); border-radius:6px; }.format-switch button { min-width:68px; min-height:34px; padding:0 12px; color:#6b786f; background:transparent; border:0; border-radius:4px; cursor:pointer; font-size:10px; font-weight:800; }.format-switch button.active { color:var(--sage-dark); background:#fff; box-shadow:0 1px 3px rgba(31,48,37,.12); }.format-switch button:disabled { cursor:wait; opacity:.6; }.import-file-picker { display:flex; width:fit-content; min-height:40px; align-items:center; gap:6px; color:#5c7163; font-size:11px; cursor:pointer; }.import-file-picker input { display:none; }
.import-grid { display:grid; grid-template-columns:minmax(0,1.6fr) minmax(250px,.8fr); gap:14px; }.import-panel { display:grid; gap:14px; }.import-panel textarea { min-height:430px; padding:14px; color:#dce9df; background:#17221b; border:1px solid #27362b; border-radius:7px; font:12px/1.55 ui-monospace,monospace; resize:vertical; }.import-note,.import-side li,.import-success p { color:#748178; font-size:12px; line-height:1.65; }.import-note { margin:0; }.import-error { display:flex; margin:0; align-items:center; gap:6px; color:#a6633b; font-size:12px; }.import-side ul { margin:17px 0; padding-left:18px; }.import-side li { margin:8px 0; }.import-success { margin-top:22px; padding:14px; color:#4f7658; background:#edf5ec; border:1px solid #d2e4d0; border-radius:7px; }.import-success strong { display:block; margin-top:5px; }.import-success p { margin:5px 0 0; } @media(max-width:800px){.import-grid{grid-template-columns:1fr}.import-panel textarea{min-height:320px}} @media(max-width:480px){.import-source-controls{align-items:stretch;flex-direction:column}.format-switch{width:100%}.import-file-picker{min-height:36px}}
</style>
