<script setup lang="ts">
import { ArrowUpRight, CheckCircle2, CircleAlert, DatabaseZap, RefreshCw } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import AssetIcon from '@/components/AssetIcon.vue'
import { getDashboard } from '@/api/client'
import { assetMeta, assetTypes } from '@/catalogue'
import { useBranding } from '@/composables/useBranding'
import type { DashboardSummary } from '@/types'

const data = ref<DashboardSummary | null>(null)
const loading = ref(true)
const error = ref('')
const { pageEyebrow } = useBranding()

const totalAssets = computed(() => {
  if (!data.value) return 0
  return Object.values(data.value.counts).reduce((total, count) => total + count, 0)
})

const largestCount = computed(() => {
  if (!data.value) return 1
  return Math.max(...Object.values(data.value.counts), 1)
})

const recentCatalogue = computed(() => {
  const type = data.value?.recent_assets[0]?.type
  if (!type) return null
  return {
    path: `/${assetMeta[type].english.toLowerCase()}`,
    label: `查看${assetMeta[type].label}目录`,
  }
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await getDashboard()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取归档数据'
  } finally {
    loading.value = false
  }
}

function formatBytes(value: number) {
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index > 3 ? 2 : 1)} ${units[index]}`
}

function relativeTime(value: string) {
  const difference = Date.now() - new Date(value).getTime()
  const hours = Math.floor(difference / 3_600_000)
  if (hours < 1) return '刚刚'
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

onMounted(load)
</script>

<template>
  <div class="page dashboard-page">
    <header class="page-heading page-heading--dashboard">
      <div>
        <p class="eyebrow">{{ pageEyebrow(`KNOWLEDGE COMMONS · ${new Date().getFullYear()}`) }}</p>
        <h1>实验室科研资产总览</h1>
        <p>让散落在服务器里的研究成果，成为可检索、可理解、可传承的共同记忆。</p>
      </div>
      <div class="archive-index">
        <span>CATALOGUE INDEX</span>
        <strong>{{ String(totalAssets).padStart(4, '0') }}</strong>
        <small>已登记资产</small>
      </div>
    </header>

    <div v-if="loading" class="state-panel" role="status" aria-live="polite">
      <span class="loader-ring"></span>
      <p>正在读取实验室目录…</p>
    </div>

    <div v-else-if="error" class="state-panel state-panel--error" role="alert">
      <CircleAlert :size="28" />
      <strong>暂时无法连接归档服务</strong>
      <p>{{ error }}</p>
      <button class="button button--outline" @click="load"><RefreshCw :size="16" /> 重新连接</button>
    </div>

    <template v-else-if="data">
      <section class="metric-grid" aria-label="资产分类统计">
        <RouterLink
          v-for="(type, index) in assetTypes"
          :key="type"
          :to="`/${assetMeta[type].english.toLowerCase()}`"
          class="metric-card reveal"
          :style="{ '--accent': assetMeta[type].color, '--soft': assetMeta[type].softColor, '--delay': `${index * 55}ms` }"
        >
          <span class="metric-number">{{ data.counts[type].toLocaleString() }}</span>
          <span class="metric-icon"><AssetIcon :type="type" :size="22" /></span>
          <span class="metric-label">{{ assetMeta[type].label }}</span>
          <span class="metric-note">{{ assetMeta[type].english }}</span>
          <ArrowUpRight class="metric-arrow" :size="18" />
        </RouterLink>
      </section>

      <section class="dashboard-grid">
        <article class="panel catalogue-panel">
          <header class="panel-heading">
            <div>
              <span class="section-number">01</span>
              <div><h2>目录构成</h2><p>Catalogue composition</p></div>
            </div>
            <span class="data-note">实时索引</span>
          </header>
          <div class="composition-chart">
            <div v-for="type in assetTypes" :key="type" class="composition-row">
              <span class="composition-label">{{ assetMeta[type].label }}</span>
              <div class="composition-track">
                <span
                  :style="{
                    width: `${Math.max((data.counts[type] / largestCount) * 100, 2)}%`,
                    background: assetMeta[type].color,
                  }"
                ></span>
              </div>
              <strong>{{ data.counts[type] }}</strong>
            </div>
          </div>
          <footer class="panel-footnote">
            <span>按统一资产类型汇总</span><span>{{ totalAssets }} records indexed</span>
          </footer>
        </article>

        <article class="panel storage-panel">
          <header class="panel-heading">
            <div>
              <span class="section-number">02</span>
              <div><h2>归档健康</h2><p>Archive integrity</p></div>
            </div>
          </header>
          <div class="storage-total">
            <span class="storage-glyph"><DatabaseZap :size="28" /></span>
            <div><strong>{{ formatBytes(data.total_storage_bytes) }}</strong><span>已索引文件容量</span></div>
          </div>
          <div class="health-stat health-stat--good">
            <CheckCircle2 :size="19" />
            <span>路径正常</span>
            <strong>{{ data.healthy_files }}</strong>
          </div>
          <div class="health-stat" :class="{ 'health-stat--warning': data.missing_files > 0 }">
            <CircleAlert :size="19" />
            <span>路径失效</span>
            <strong>{{ data.missing_files }}</strong>
          </div>
          <RouterLink class="text-link" to="/archive-health">查看文件索引 <ArrowUpRight :size="15" /></RouterLink>
        </article>

        <article class="panel recent-panel">
          <header class="panel-heading">
            <div>
              <span class="section-number">03</span>
              <div><h2>最近归档</h2><p>Recently catalogued</p></div>
            </div>
            <RouterLink v-if="recentCatalogue" class="text-link" :to="recentCatalogue.path">{{ recentCatalogue.label }} <ArrowUpRight :size="15" /></RouterLink>
          </header>
          <div class="asset-table">
            <RouterLink
              v-for="asset in data.recent_assets"
              :key="asset.id"
              :to="{ name: 'asset-detail', params: { assetId: asset.id } }"
              class="asset-row"
            >
              <span class="asset-type-icon" :style="{ color: assetMeta[asset.type].color, background: assetMeta[asset.type].softColor }">
                <AssetIcon :type="asset.type" :size="18" />
              </span>
              <span class="asset-row-main"><strong>{{ asset.title }}</strong><small>{{ asset.summary }}</small></span>
              <span class="type-chip" :style="{ color: assetMeta[asset.type].color, background: assetMeta[asset.type].softColor }">
                {{ assetMeta[asset.type].label }}
              </span>
              <span class="asset-owner">{{ asset.owner.name }}</span>
              <time>{{ relativeTime(asset.updated_at) }}</time>
            </RouterLink>
            <div v-if="!data.recent_assets.length" class="dashboard-empty">
              <span>尚未登记科研资产。完成首次登记后，最近归档会显示在这里。</span>
              <RouterLink class="text-link" to="/papers">前往论文目录 <ArrowUpRight :size="15" /></RouterLink>
            </div>
          </div>
        </article>

        <aside class="dashboard-rail">
          <article class="panel activity-panel">
            <header class="panel-heading">
              <div><span class="section-number">04</span><div><h2>最近活动</h2><p>Lab activity</p></div></div>
            </header>
            <ol class="activity-list">
              <li v-if="!data.recent_activities.length" class="dashboard-empty dashboard-empty--activity">尚无归档活动。资产登记、更新和文件操作会记录在这里。</li>
              <li v-for="activity in data.recent_activities" :key="activity.id">
                <span class="activity-marker"></span>
                <div>
                  <strong>{{ activity.asset_title ?? activity.action_label }}</strong>
                  <p>{{ activity.actor_name ?? '系统' }} · {{ activity.description }}<span v-if="activity.occurrence_count > 1" class="activity-count"> ×{{ activity.occurrence_count }}</span></p>
                  <time>{{ relativeTime(activity.created_at) }}</time>
                </div>
              </li>
            </ol>
          </article>
          <article class="panel tag-panel">
            <header class="panel-heading panel-heading--compact">
              <div><span class="section-number">05</span><div><h2>知识标签</h2><p>Shared vocabulary</p></div></div>
            </header>
            <div class="tag-cloud">
              <p v-if="!data.popular_tags.length" class="dashboard-empty dashboard-empty--tags">尚无知识标签。为资产添加标签后，会形成团队共享词表。</p>
              <span v-for="([tag, count], index) in data.popular_tags" :key="tag" :class="{ prominent: index < 3 }">
                {{ tag }} <small>{{ count }}</small>
              </span>
            </div>
          </article>
        </aside>
      </section>
    </template>
  </div>
</template>
