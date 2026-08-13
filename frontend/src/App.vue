<script setup lang="ts">
import { NConfigProvider, NDialogProvider, NMessageProvider, zhCN, dateZhCN } from 'naive-ui'
import { computed, onMounted } from 'vue'

import { getCurrentAccount } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import { useBranding } from '@/composables/useBranding'
import LoginView from '@/views/LoginView.vue'
import { getSessionToken, useSession } from '@/session'
import type { AccountLoginResponse } from '@/types'

const { branding, loadBranding } = useBranding()
const { account, restoring, establishSession, completeSessionRestoration, expireSession } = useSession()

async function restoreSession() {
  await loadBranding()
  if (!getSessionToken()) {
    completeSessionRestoration(null)
    return
  }
  try {
    completeSessionRestoration(await getCurrentAccount())
  } catch {
    expireSession()
  }
}

function completeLogin(response: AccountLoginResponse) {
  establishSession(response)
}

function signOut() {
  expireSession()
}

onMounted(restoreSession)

const themeOverrides = computed(() => ({
  common: {
    primaryColor: branding.primary_color,
    primaryColorHover: branding.primary_color,
    primaryColorPressed: branding.primary_color,
    borderRadius: '8px',
    fontFamily: '"Avenir Next", "PingFang SC", sans-serif',
  },
}))
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides">
    <NDialogProvider>
      <NMessageProvider>
        <div v-if="restoring" class="session-loading" role="status" aria-live="polite">正在恢复登录状态…</div>
        <LoginView v-else-if="!account" @authenticated="completeLogin" />
        <AppShell v-else :account="account" @sign-out="signOut" />
      </NMessageProvider>
    </NDialogProvider>
  </NConfigProvider>
</template>
