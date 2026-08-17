<script setup lang="ts">
import { CircleAlert, RefreshCw, ScrollText } from '@lucide/vue'
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { getActivities } from '@/api/client'
import { assetMeta } from '@/catalogue'
import { useBranding } from '@/composables/useBranding'
import type { ActivityListResponse } from '@/types'
import { parseApiDate } from '@/utils/dates'

const data = ref<ActivityListResponse | null>(null)
const loading = ref(true)
const error = ref('')
const action = ref('')
const page = ref(1)
const { pageEyebrow } = useBranding()
let controller: AbortController | undefined

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parseApiDate(value))
}

async function load(nextPage = page.value) {
  controller?.abort()
  const requestController = new AbortController()
  controller = requestController
  const requestedAction = action.value
  loading.value = true
  error.value = ''
  page.value = nextPage
  try {
    const result = await getActivities(nextPage, requestedAction || undefined, requestController.signal)
    if (controller !== requestController) return
    data.value = result
  } catch (reason) {
    if (controller !== requestController) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    error.value = reason instanceof Error ? reason.message : '无法读取操作日志'
  } finally {
    if (controller === requestController) loading.value = false
  }
}

function filter() {
  void load(1)
}

onMounted(load)
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="page activity-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">{{ pageEyebrow('AUDIT TRAIL') }}</p>
        <h1>操作日志</h1>
        <p>记录资产登记、编辑、归档、关联、文件访问和上传指令等管理行为。</p>
      </div>
      <button class="button button--outline" :disabled="loading" @click="load()">
        <RefreshCw :size="16" />刷新
      </button>
    </header>

    <section class="panel activity-filter">
      <label>
        操作类型
        <select v-model="action" @change="filter">
          <option value="">全部操作</option>
          <option v-for="facet in data?.facets" :key="facet.value" :value="facet.value">{{ facet.label }}（{{ facet.count }}）</option>
        </select>
      </label>
      <span v-if="data">共 {{ data.total }} 条记录</span>
    </section>

    <div v-if="loading" class="state-panel" role="status" aria-live="polite">
      <span class="loader-ring"></span><p>正在读取操作日志…</p>
    </div>
    <div v-else-if="error" class="state-panel state-panel--error" role="alert">
      <CircleAlert :size="28" /><strong>无法读取操作日志</strong><p>{{ error }}</p>
      <button class="button button--outline" @click="load()">重试</button>
    </div>
    <section v-else-if="data" class="panel activity-table">
      <div v-if="!data.items.length" class="activity-empty">
        <ScrollText :size="26" />尚无符合条件的操作记录。
      </div>
      <article v-for="item in data.items" :key="item.id">
        <span class="activity-icon"><ScrollText :size="16" /></span>
        <div class="activity-copy">
          <strong>{{ item.description }}</strong>
          <p>
            {{ item.actor_name ?? '系统' }}<template v-if="item.credential_name"> / {{ item.credential_name }}</template> · {{ item.action_label }}
            <template v-if="item.asset_title"> · {{ item.asset_title }}</template>
          </p>
        </div>
        <span v-if="item.asset_type" class="activity-type">
          {{ assetMeta[item.asset_type].label }}
        </span>
        <time>{{ formatDate(item.created_at) }}</time>
      </article>
      <footer v-if="data.total > data.page_size">
        <button class="button button--outline" :disabled="page === 1" @click="load(page - 1)">上一页</button>
        <span>第 {{ page }} 页</span>
        <button class="button button--outline" :disabled="page * data.page_size >= data.total" @click="load(page + 1)">下一页</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.activity-filter { display: flex; margin-bottom: 12px; padding: 14px 18px; align-items: end; justify-content: space-between; gap: 12px; }
.activity-filter label { display: grid; color: #526158; font-size: 11px; font-weight: 700; gap: 6px; }
.activity-page :deep(.page-heading > .button), .activity-filter select { min-height: 44px; }.activity-filter select { min-width: 180px; padding: 0 10px; color: var(--ink); background: #fff; border: 1px solid var(--line); border-radius: 5px; }
.activity-filter > span { color: #68766d; font-size: 11px; }
.activity-table { overflow: hidden; }
.activity-table article { display: grid; min-height: 70px; padding: 12px 18px; align-items: center; grid-template-columns: 30px minmax(0, 1fr) auto auto; gap: 12px; border-bottom: 1px solid var(--line); }
.activity-icon { display: grid; width: 28px; height: 28px; color: var(--sage); place-items: center; background: var(--sage-soft); border-radius: 5px; }
.activity-copy { min-width: 0; }
.activity-table strong { font-size: 12px; }
.activity-table p { overflow: hidden; margin: 4px 0 0; color: #68766d; font-size: 11px; line-height: 1.45; text-overflow: ellipsis; white-space: nowrap; }
.activity-type, .activity-table time { color: #748178; font-size: 10px; white-space: nowrap; }
.activity-table footer { display: flex; padding: 14px 18px; align-items: center; justify-content: flex-end; gap: 10px; color: #68766d; font-size: 11px; }
.activity-empty { display: flex; min-height: 220px; align-items: center; justify-content: center; gap: 9px; color: #748178; font-size: 12px; }
@media (max-width: 650px) {
  .activity-filter { align-items: stretch; flex-direction: column; }
  .activity-filter select { width: 100%; }
  .activity-table article { padding: 12px 14px; align-items: start; grid-template-columns: 30px minmax(0, 1fr); gap: 9px; }
  .activity-type, .activity-table time { display: none; }
  .activity-table p { white-space: normal; }
}
</style>
