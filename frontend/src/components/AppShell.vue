<script setup lang="ts">
import {
  Archive,
  ArchiveRestore,
  Bell,
  ChevronDown,
  Command,
  Home,
  FileUp,
  Menu,
  Search,
  ScrollText,
  Settings,
  Tags,
  X,
} from '@lucide/vue'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { AccountSummary } from '@/types'
import AssetIcon from '@/components/AssetIcon.vue'
import { assetMeta, assetTypes } from '@/catalogue'

const route = useRoute()
const router = useRouter()
defineProps<{ account: AccountSummary }>()
const emit = defineEmits<{ signOut: [] }>()
const globalSearchInput = ref<HTMLInputElement | null>(null)

const mobileNavigationOpen = ref(false)
const globalQuery = ref('')

function submitGlobalSearch() {
  const query = globalQuery.value.trim()
  if (!query) return
  router.push({ name: 'search', query: { q: query } })
  mobileNavigationOpen.value = false
}

function focusGlobalSearch(event: KeyboardEvent) {
  if (
    (event.metaKey || event.ctrlKey) &&
    event.key.toLowerCase() === 'k' &&
    !event.altKey
  ) {
    event.preventDefault()
    globalSearchInput.value?.focus()
  }
}

onMounted(() => window.addEventListener('keydown', focusGlobalSearch))
onBeforeUnmount(() => window.removeEventListener('keydown', focusGlobalSearch))
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ 'sidebar--open': mobileNavigationOpen }">
      <button class="mobile-close" aria-label="关闭导航" @click="mobileNavigationOpen = false">
        <X :size="20" />
      </button>
      <RouterLink class="brand" to="/" @click="mobileNavigationOpen = false">
        <span class="brand-mark"><i></i><i></i><i></i></span>
        <span>
          <strong>SAGE</strong>
          <small>RESEARCH ARCHIVE</small>
        </span>
      </RouterLink>

      <p class="nav-kicker">知识目录</p>
      <nav class="primary-nav" aria-label="主导航">
        <RouterLink to="/" :class="{ active: route.name === 'dashboard' }" @click="mobileNavigationOpen = false">
          <Home :size="19" />
          <span>总览 <small>Overview</small></span>
        </RouterLink>
        <RouterLink
          v-for="type in assetTypes"
          :key="type"
          :to="`/${assetMeta[type].english.toLowerCase()}`"
          :class="{ active: route.meta.assetType === type }"
          @click="mobileNavigationOpen = false"
        >
          <AssetIcon :type="type" :size="19" />
          <span>{{ assetMeta[type].label }} <small>{{ assetMeta[type].english }}</small></span>
        </RouterLink>
      </nav>

      <p class="nav-kicker nav-kicker--secondary">管理</p>
      <nav class="primary-nav primary-nav--quiet" aria-label="管理导航">
        <RouterLink to="/unclaimed-files" :class="{ active: route.name === 'unclaimed-files' }" @click="mobileNavigationOpen = false"><Tags :size="19" /><span>待认领文件</span></RouterLink>
        <RouterLink to="/archive-health" :class="{ active: route.name === 'archive-health' }" @click="mobileNavigationOpen = false"><ArchiveRestore :size="19" /><span>归档健康</span></RouterLink>
        <RouterLink to="/import-assets" :class="{ active: route.name === 'import-assets' }" @click="mobileNavigationOpen = false"><FileUp :size="19" /><span>批量导入</span></RouterLink>
        <RouterLink to="/archived-assets" :class="{ active: route.name === 'archived-assets' }" @click="mobileNavigationOpen = false"><Archive :size="19" /><span>已归档资产</span></RouterLink>
        <RouterLink to="/settings" :class="{ active: route.name === 'settings' }" @click="mobileNavigationOpen = false"><Settings :size="19" /><span>系统设置</span></RouterLink>
      </nav>
        <RouterLink to="/activity-log" :class="{ active: route.name === 'activity-log' }" @click="mobileNavigationOpen = false"><ScrollText :size="19" /><span>操作日志</span></RouterLink>

      <div class="lab-signature">
        <div class="sprig" aria-hidden="true">
          <span></span><span></span><span></span><span></span><span></span>
        </div>
        <strong>SAGE Lab</strong>
        <p>数据 · 知识 · 传承</p>
        <small>Science · Archive · Growth</small>
      </div>
    </aside>

    <div v-if="mobileNavigationOpen" class="navigation-scrim" @click="mobileNavigationOpen = false"></div>

    <section class="workspace">
      <header class="topbar">
        <button class="mobile-menu" aria-label="打开导航" @click="mobileNavigationOpen = true">
          <Menu :size="20" />
        </button>
        <form class="global-search" @submit.prevent="submitGlobalSearch">
          <button type="submit" aria-label="提交全局搜索"><Search :size="19" /></button>
          <input ref="globalSearchInput" v-model="globalQuery" aria-label="全局搜索" placeholder="搜索论文、数据集、文献、项目或模型" />
          <span class="search-shortcut"><Command :size="13" /> K</span>
        </form>
        <div class="topbar-actions">
          <button class="icon-button" aria-label="通知">
            <Bell :size="20" />
            <span class="notification-dot"></span>
          </button>
          <button class="profile-button" title="退出当前账号" @click="emit('signOut')">
            <span class="avatar">{{ account.username.slice(0, 1).toUpperCase() }}</span>
            <span class="profile-copy"><strong>{{ account.name }}</strong><small>管理员 · 退出</small></span>
            <ChevronDown :size="16" />
          </button>
        </div>
      </header>
      <main class="main-content">
        <RouterView />
      </main>
    </section>
  </div>
</template>
