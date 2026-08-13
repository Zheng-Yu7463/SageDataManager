<script setup lang="ts">
import {
  Archive,
  ArchiveRestore,
  Bell,
  ChevronDown,
  Command,
  Home,
  FileUp,
  LogOut,
  Menu,
  Search,
  ScrollText,
  Settings,
  Tags,
  X,
} from '@lucide/vue'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { AccountSummary } from '@/types'
import AssetIcon from '@/components/AssetIcon.vue'
import { assetMeta, assetTypes } from '@/catalogue'
import { useOverlayFocus } from '@/composables/useOverlayFocus'
import { useBranding } from '@/composables/useBranding'

const route = useRoute()
const router = useRouter()
defineProps<{ account: AccountSummary }>()
const emit = defineEmits<{ signOut: [] }>()
const globalSearchInput = ref<HTMLInputElement | null>(null)
const sidebar = ref<HTMLElement | null>(null)
const profileMenu = ref<HTMLElement | null>(null)

const mobileNavigationOpen = ref(false)
const profileMenuOpen = ref(false)
const globalQuery = ref('')
const { branding } = useBranding()

useOverlayFocus(mobileNavigationOpen, sidebar, () => { mobileNavigationOpen.value = false })

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
  } else if (event.key === 'Escape') {
    profileMenuOpen.value = false
  }
}

function closeProfileMenu(event: PointerEvent) {
  if (profileMenuOpen.value && !profileMenu.value?.contains(event.target as Node)) {
    profileMenuOpen.value = false
  }
}

watch(() => route.fullPath, () => {
  mobileNavigationOpen.value = false
  profileMenuOpen.value = false
  globalQuery.value = route.name === 'search' && typeof route.query.q === 'string' ? route.query.q : ''
}, { immediate: true })

onMounted(() => {
  window.addEventListener('keydown', focusGlobalSearch)
  window.addEventListener('pointerdown', closeProfileMenu)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', focusGlobalSearch)
  window.removeEventListener('pointerdown', closeProfileMenu)
})
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" href="#main-content">跳到主内容</a>
    <aside ref="sidebar" class="sidebar" :class="{ 'sidebar--open': mobileNavigationOpen }">
      <button class="mobile-close" aria-label="关闭导航" @click="mobileNavigationOpen = false">
        <X :size="20" />
      </button>
      <RouterLink class="brand" to="/" @click="mobileNavigationOpen = false">
        <img v-if="branding.logo_url" class="brand-logo" :src="branding.logo_url" alt="" />
        <span v-else class="brand-mark"><i></i><i></i><i></i></span>
        <span>
          <strong>{{ branding.product_name }}</strong>
          <small>{{ branding.product_subtitle }}</small>
        </span>
      </RouterLink>

      <p class="nav-kicker">资产目录</p>
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

      <p class="nav-kicker nav-kicker--secondary">系统管理</p>
      <nav class="primary-nav primary-nav--quiet" aria-label="管理导航">
        <RouterLink to="/unclaimed-files" :class="{ active: route.name === 'unclaimed-files' }" @click="mobileNavigationOpen = false"><Tags :size="19" /><span>待认领文件</span></RouterLink>
        <RouterLink to="/archive-health" :class="{ active: route.name === 'archive-health' }" @click="mobileNavigationOpen = false"><ArchiveRestore :size="19" /><span>归档健康</span></RouterLink>
        <RouterLink to="/import-assets" :class="{ active: route.name === 'import-assets' }" @click="mobileNavigationOpen = false"><FileUp :size="19" /><span>批量导入</span></RouterLink>
        <RouterLink to="/archived-assets" :class="{ active: route.name === 'archived-assets' }" @click="mobileNavigationOpen = false"><Archive :size="19" /><span>已归档资产</span></RouterLink>
        <RouterLink to="/settings" :class="{ active: route.name === 'settings' }" @click="mobileNavigationOpen = false"><Settings :size="19" /><span>系统设置</span></RouterLink>
        <RouterLink to="/activity-log" :class="{ active: route.name === 'activity-log' }" @click="mobileNavigationOpen = false"><ScrollText :size="19" /><span>操作日志</span></RouterLink>
      </nav>

      <div class="lab-signature" :aria-label="`${branding.organization_name}，${branding.slogan}`">
        <div class="sprig" aria-hidden="true">
          <span></span><span></span><span></span><span></span><span></span>
        </div>
        <div class="lab-signature-copy">
          <strong>{{ branding.organization_name }}</strong>
          <p>{{ branding.slogan }}</p>
          <small>{{ branding.slogan_secondary }}</small>
        </div>
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
          <button class="icon-button" aria-label="查看操作日志" title="查看操作日志" @click="router.push({ name: 'activity-log' })">
            <Bell :size="20" />
          </button>
          <div ref="profileMenu" class="profile-menu">
            <button class="profile-button" :aria-expanded="profileMenuOpen" :aria-label="`账户菜单：${account.name}`" title="账户菜单" @click="profileMenuOpen = !profileMenuOpen">
              <span class="avatar">{{ account.username.slice(0, 1).toUpperCase() }}</span>
              <span class="profile-copy"><strong>{{ account.name }}</strong><small>管理员 · {{ account.username }}</small></span>
              <ChevronDown :size="16" />
            </button>
            <div v-if="profileMenuOpen" class="profile-popover">
              <div><strong>{{ account.name }}</strong><small>{{ account.email }}</small></div>
              <button @click="emit('signOut')"><LogOut :size="16" />退出登录</button>
            </div>
          </div>
        </div>
      </header>
      <main id="main-content" class="main-content" tabindex="-1">
        <RouterView v-slot="{ Component, route: currentRoute }">
          <component :is="Component" :key="String(currentRoute.name)" />
        </RouterView>
      </main>
    </section>
  </div>
</template>
