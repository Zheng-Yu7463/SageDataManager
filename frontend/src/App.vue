<script setup lang="ts">
import { NConfigProvider, NDialogProvider, NMessageProvider, zhCN, dateZhCN } from 'naive-ui'
import { onMounted, ref } from 'vue'

import { getCurrentAccount } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import LoginView from '@/views/LoginView.vue'
import type { AccountLoginResponse, AccountSummary } from '@/types'

const account = ref<AccountSummary | null>(null)
const restoring = ref(true)

async function restoreSession() {
  if (!window.localStorage.getItem('sage-session-token')) {
    restoring.value = false
    return
  }
  try {
    account.value = await getCurrentAccount()
  } catch {
    window.localStorage.removeItem('sage-session-token')
  } finally {
    restoring.value = false
  }
}

function completeLogin(response: AccountLoginResponse) {
  window.localStorage.setItem('sage-session-token', response.session_token)
  account.value = response
}

function signOut() {
  window.localStorage.removeItem('sage-session-token')
  account.value = null
}

onMounted(restoreSession)

const themeOverrides = {
  common: {
    primaryColor: '#2e7351',
    primaryColorHover: '#245f42',
    primaryColorPressed: '#1d5037',
    borderRadius: '8px',
    fontFamily: '"Avenir Next", "PingFang SC", sans-serif',
  },
}
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides">
    <NDialogProvider>
      <NMessageProvider>
        <div v-if="restoring" class="session-loading">正在恢复登录状态…</div>
        <LoginView v-else-if="!account" @authenticated="completeLogin" />
        <AppShell v-else :account="account" @sign-out="signOut" />
      </NMessageProvider>
    </NDialogProvider>
  </NConfigProvider>
</template>

