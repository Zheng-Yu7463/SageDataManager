<script setup lang="ts">
import { Check, CircleAlert, Plus, RefreshCw, ShieldCheck, UserRound, UserRoundX, X } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { createAdminAccount, getAdminAccounts, getCurrentAccount, updateAdminAccount } from '@/api/client'
import type { AccountSummary } from '@/types'

const accounts = ref<AccountSummary[]>([])
const currentUsername = ref('')
const loading = ref(true)
const updatingUsername = ref<string | null>(null)
const error = ref('')
const createOpen = ref(false)
const creating = ref(false)
const createError = ref('')
const form = ref({ username: '', name: '', email: '' })

const activeCount = computed(() => accounts.value.filter((account) => account.is_active).length)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [items, current] = await Promise.all([getAdminAccounts(), getCurrentAccount()])
    accounts.value = items
    currentUsername.value = current.username
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取管理员账号'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = { username: '', name: '', email: '' }
  createError.value = ''
  createOpen.value = true
}

function closeCreate() {
  if (!creating.value) createOpen.value = false
}

async function createAccount() {
  if (!form.value.username.trim() || !form.value.name.trim() || !form.value.email.trim()) return
  creating.value = true
  createError.value = ''
  try {
    const account = await createAdminAccount({
      username: form.value.username.trim().toLowerCase(),
      name: form.value.name.trim(),
      email: form.value.email.trim().toLowerCase(),
    })
    accounts.value = [...accounts.value, account].sort((left, right) => left.username.localeCompare(right.username))
    createOpen.value = false
  } catch (reason) {
    createError.value = reason instanceof Error ? reason.message : '无法创建账号'
  } finally {
    creating.value = false
  }
}

async function toggleAccount(account: AccountSummary) {
  if (account.username === currentUsername.value) return
  updatingUsername.value = account.username
  error.value = ''
  try {
    const updated = await updateAdminAccount(account.username, { is_active: !account.is_active })
    accounts.value = accounts.value.map((item) => item.username === updated.username ? updated : item)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法更新账号状态'
  } finally {
    updatingUsername.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="page settings-page">
    <header class="page-heading settings-heading">
      <div>
        <p class="eyebrow">SAGE ADMINISTRATION</p>
        <h1>系统设置</h1>
        <p>管理可登录的实验室管理员账号。公开注册保持关闭，新增账号仍使用服务器配置的统一初始密码。</p>
      </div>
      <div class="settings-actions"><button class="button button--outline" :disabled="loading" @click="load"><RefreshCw :size="16" />刷新</button><button class="button button--primary" @click="openCreate"><Plus :size="16" />新增管理员</button></div>
    </header>

    <div v-if="loading" class="state-panel"><span class="loader-ring"></span><p>正在读取账号设置…</p></div>
    <div v-else-if="error && !accounts.length" class="state-panel state-panel--error"><CircleAlert :size="28" /><strong>无法读取账号</strong><p>{{ error }}</p><button class="button button--outline" @click="load">重试</button></div>
    <template v-else>
      <section class="settings-summary">
        <div><ShieldCheck :size="19" /><span><strong>{{ activeCount }}</strong><small>启用管理员</small></span></div>
        <div><UserRound :size="19" /><span><strong>{{ accounts.length }}</strong><small>已预置账号</small></span></div>
        <p>账号名同时用于生成 SCP 上传命令中的服务器用户名。</p>
      </section>
      <p v-if="error" class="settings-error">{{ error }}</p>
      <section class="accounts-panel">
        <header><div><h2>管理员账号</h2><p>可停用不再使用的账号；当前登录账号不可自行停用。</p></div><span>{{ accounts.length }} accounts</span></header>
        <div class="accounts-table">
          <div v-for="account in accounts" :key="account.id" class="account-row" :class="{ 'account-row--inactive': !account.is_active }">
            <span class="account-avatar">{{ account.username.slice(0, 1).toUpperCase() }}</span>
            <div class="account-copy"><strong>{{ account.name }}</strong><small>{{ account.username }} · {{ account.email }}</small></div>
            <span class="account-role">{{ account.role }}</span>
            <span class="account-status" :class="{ 'account-status--inactive': !account.is_active }">{{ account.is_active ? '已启用' : '已停用' }}</span>
            <button class="button button--outline account-toggle" :disabled="updatingUsername === account.username || account.username === currentUsername" :title="account.username === currentUsername ? '当前登录账号不可自行停用' : ''" @click="toggleAccount(account)"><UserRoundX v-if="account.is_active" :size="15" /><Check v-else :size="15" />{{ updatingUsername === account.username ? '处理中' : account.is_active ? '停用' : '启用' }}</button>
          </div>
        </div>
      </section>
    </template>

    <div v-if="createOpen" class="settings-backdrop" @click.self="closeCreate">
      <form class="settings-dialog" @submit.prevent="createAccount">
        <button class="settings-close" type="button" :disabled="creating" aria-label="关闭" @click="closeCreate"><X :size="18" /></button>
        <p class="eyebrow">PREPROVISION ACCOUNT</p>
        <h2>新增管理员账号</h2>
        <p>账号名应与服务器 SSH 用户名一致；创建后可立即用统一初始密码登录。</p>
        <label>账号名<input v-model="form.username" required autocomplete="off" pattern="[a-z0-9]+" maxlength="80" placeholder="例如：newmember" /></label>
        <label>显示名称<input v-model="form.name" required maxlength="80" placeholder="例如：张三" /></label>
        <label>邮箱<input v-model="form.email" required type="email" maxlength="255" placeholder="例如：newmember@sage.lab" /></label>
        <p v-if="createError" class="settings-error">{{ createError }}</p>
        <footer><button class="button button--outline" type="button" :disabled="creating" @click="closeCreate">取消</button><button class="button button--primary" :disabled="creating || !form.username.trim() || !form.name.trim() || !form.email.trim()" type="submit"><Plus :size="16" />{{ creating ? '正在创建' : '创建管理员' }}</button></footer>
      </form>
    </div>
  </div>
</template>

<style scoped>
.settings-heading { align-items: center; }.settings-actions { display: flex; gap: 9px; }.settings-summary { display: flex; margin-bottom: 16px; padding: 16px 20px; align-items: center; gap: 28px; background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 10px; }.settings-summary > div { display: flex; min-width: 120px; align-items: center; gap: 9px; }.settings-summary svg { color: var(--sage); }.settings-summary span { display: grid; gap: 2px; }.settings-summary strong { font-family: "Iowan Old Style", "Songti SC", serif; font-size: 20px; font-weight: 500; }.settings-summary small, .settings-summary p { color: var(--muted); font-size: 11px; }.settings-summary p { margin: 0 0 0 auto; }.accounts-panel { background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 10px; }.accounts-panel > header { display: flex; padding: 19px 20px; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); }.accounts-panel h2, .settings-dialog h2 { margin: 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 21px; font-weight: 500; }.accounts-panel header p, .settings-dialog > p { margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.55; }.accounts-panel header > span { color: #89968e; font-size: 10px; }.account-row { display: grid; min-height: 68px; padding: 10px 20px; align-items: center; grid-template-columns: 34px minmax(180px, 1fr) 90px 80px auto; gap: 12px; border-bottom: 1px solid #edf0eb; }.account-row:last-child { border-bottom: 0; }.account-row--inactive { opacity: .6; }.account-avatar { display: grid; width: 32px; height: 32px; color: #fff; place-items: center; background: var(--sage); border-radius: 50%; font-size: 12px; font-weight: 800; }.account-copy { display: grid; min-width: 0; gap: 3px; }.account-copy strong { font-size: 12px; }.account-copy small { overflow: hidden; color: #7c887f; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.account-role { color: #56705f; font-size: 10px; font-weight: 700; text-transform: uppercase; }.account-status { color: #2e7351; font-size: 10px; font-weight: 700; }.account-status--inactive { color: #a6633b; }.account-toggle { min-width: 72px; justify-content: center; }.settings-error { margin: 0 0 13px; color: #a6633b; font-size: 12px; }.settings-backdrop { position: fixed; z-index: 40; inset: 0; display: grid; padding: 20px; place-items: center; background: rgba(23,34,26,.48); }.settings-dialog { position: relative; display: grid; width: min(100%, 500px); padding: 28px; background: #fdfefb; border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 20px 50px rgba(24,37,29,.22); gap: 12px; }.settings-dialog label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.settings-dialog input { width: 100%; padding: 9px 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.settings-dialog footer { display: flex; margin-top: 7px; justify-content: flex-end; gap: 9px; }.settings-close { position: absolute; top: 12px; right: 12px; display: grid; width: 31px; height: 31px; color: #68776d; place-items: center; background: transparent; border: 0; border-radius: 50%; cursor: pointer; }.settings-close:hover { background: #eef2ed; } @media (max-width: 720px) { .settings-heading { align-items: flex-start; }.settings-actions { margin-top: 3px; }.settings-summary { align-items: flex-start; flex-wrap: wrap; gap: 17px; }.settings-summary p { width: 100%; margin-left: 0; }.account-row { padding: 12px 14px; grid-template-columns: 34px minmax(0, 1fr) auto; }.account-role { display: none; }.account-status { grid-column: 2; }.account-toggle { grid-column: 3; grid-row: 1 / 3; } }
</style>
