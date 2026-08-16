<script setup lang="ts">
import { NConfigProvider, NDialogProvider, NMessageProvider, zhCN, dateZhCN } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getCurrentAccount, getInstanceSetupStatus } from '@/api/client'
import AppShell from '@/components/AppShell.vue'
import AccountRegistrationView from '@/views/AccountRegistrationView.vue'
import { useBranding } from '@/composables/useBranding'
import LoginView from '@/views/LoginView.vue'
import SetupView from '@/views/SetupView.vue'
import { getSessionToken, useSession } from '@/session'
import type { AccountLoginResponse, InstanceSetupStatus } from '@/types'

const { branding, loadBranding } = useBranding()
const { account, restoring, establishSession, completeSessionRestoration, expireSession } = useSession()
const setupStatus = ref<InstanceSetupStatus | null>(null)
const startupError = ref('')
const startupRetrying = ref(false)
const route = useRoute()
const router = useRouter()
const invitationRoute = computed(() => route.name === 'account-registration')

async function restoreSession() {
  startupRetrying.value = true
  startupError.value = ''
  try {
    const [, status] = await Promise.all([loadBranding(), getInstanceSetupStatus()])
    setupStatus.value = status
  } catch (reason) {
    startupError.value = reason instanceof Error ? reason.message : '无法读取实例启动状态'
    completeSessionRestoration(null)
    startupRetrying.value = false
    return
  }
  if (!setupStatus.value.initialized) {
    expireSession()
    startupRetrying.value = false
    return
  }
  if (!getSessionToken()) {
    completeSessionRestoration(null)
    startupRetrying.value = false
    return
  }
  try {
    completeSessionRestoration(await getCurrentAccount())
  } catch (reason) {
    if (getSessionToken()) {
      startupError.value = reason instanceof Error ? reason.message : '无法恢复登录状态'
      completeSessionRestoration(null)
    } else {
      expireSession()
    }
  } finally {
    startupRetrying.value = false
  }
}

function completeLogin(response: AccountLoginResponse) {
  establishSession(response)
}

function completeSetup(response: AccountLoginResponse) {
  setupStatus.value = { initialized: true, authentication_ready: true }
  establishSession(response)
}

function completeInvitation(response: AccountLoginResponse) {
  establishSession(response)
  void router.replace({ name: 'dashboard' })
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
        <div v-if="restoring || startupRetrying" class="session-loading" role="status" aria-live="polite">正在读取实例状态…</div>
        <main v-else-if="startupError" class="startup-error" role="alert">
          <strong>无法连接 DataManager 服务</strong>
          <p>{{ startupError }}</p>
          <button class="button button--outline" type="button" @click="restoreSession">重试</button>
        </main>
        <SetupView v-else-if="!account && setupStatus && !setupStatus.initialized" :authentication-ready="setupStatus.authentication_ready" @authenticated="completeSetup" />
        <main v-else-if="!account && setupStatus && !setupStatus.authentication_ready" class="startup-error" role="alert">
          <strong>认证服务尚未配置</strong>
          <p>请设置 SAGE_AUTH_SESSION_SECRET 并重启后端。</p>
          <button class="button button--outline" type="button" @click="restoreSession">重新检查</button>
        </main>
        <AccountRegistrationView v-else-if="invitationRoute && setupStatus?.initialized" @authenticated="completeInvitation" />
        <LoginView v-else-if="!account" @authenticated="completeLogin" />
        <AppShell v-else :account="account" @sign-out="signOut" />
      </NMessageProvider>
    </NDialogProvider>
  </NConfigProvider>
</template>

<style scoped>
.startup-error {
  display: grid;
  min-height: 100vh;
  padding: 24px;
  place-items: center;
  align-content: center;
  text-align: center;
  background: #f2f4ee;
  gap: 8px;
}

.startup-error strong {
  font-family: "Iowan Old Style", "Songti SC", serif;
  font-size: 22px;
  font-weight: 500;
}

.startup-error p {
  margin: 0 0 8px;
  color: #9a5b3c;
  font-size: 12px;
}

.startup-error .button {
  justify-content: center;
}
</style>
