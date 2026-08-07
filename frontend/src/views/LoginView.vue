<script setup lang="ts">
import { CircleAlert, KeyRound, LogIn, RefreshCw, ShieldCheck } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { getLoginAccounts, loginAccount } from '@/api/client'
import type { AccountLoginResponse, AccountSummary } from '@/types'

const emit = defineEmits<{ authenticated: [account: AccountLoginResponse] }>()
const accounts = ref<AccountSummary[]>([])
const username = ref('')
const password = ref('')
const loading = ref(true)
const submitting = ref(false)
const error = ref('')

async function loadAccounts() {
  loading.value = true
  error.value = ''
  try {
    accounts.value = await getLoginAccounts()
    username.value = accounts.value[0]?.username ?? ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取管理员账号'
  } finally {
    loading.value = false
  }
}

async function login() {
  if (!username.value || !password.value) return
  submitting.value = true
  error.value = ''
  try {
    emit('authenticated', await loginAccount(username.value, password.value))
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '登录失败'
  } finally {
    submitting.value = false
  }
}

onMounted(loadAccounts)
</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <div class="login-mark"><i></i><i></i><i></i></div>
      <p class="eyebrow">SAGE RESEARCH ARCHIVE</p>
      <h1>管理员登录</h1>
      <p class="login-intro">当前仅开放实验室固定管理员账号。账号名也将用于生成 SCP 上传命令。</p>
      <div v-if="loading" class="login-state"><RefreshCw :size="18" class="is-spinning" />读取账号中…</div>
      <div v-else-if="error && !accounts.length" class="login-state login-state--error"><CircleAlert :size="19" />{{ error }}<button class="button button--outline" @click="loadAccounts">重试</button></div>
      <form v-else @submit.prevent="login">
        <label>账号<select v-model="username" required><option v-for="account in accounts" :key="account.id" :value="account.username">{{ account.username }} · 管理员</option></select></label>
        <label>密码<div class="password-field"><KeyRound :size="16" /><input v-model="password" required type="password" autocomplete="current-password" placeholder="输入统一初始密码" /></div></label>
        <p v-if="error" class="login-error">{{ error }}</p>
        <button class="button button--primary login-submit" :disabled="submitting || !password" type="submit"><LogIn :size="16" />{{ submitting ? '正在验证' : '进入归档系统' }}</button>
      </form>
      <p class="login-note"><ShieldCheck :size="15" /> 注册功能暂未开放；该入口仅适用于实验室局域网。</p>
    </section>
  </main>
</template>

<style scoped>
.login-page { display: grid; min-height: 100vh; padding: 24px; place-items: center; background: radial-gradient(circle at 75% 18%, rgba(202, 222, 204, .62), transparent 28rem), #f2f4ee; }.login-card { width: min(100%, 420px); padding: 37px; background: rgba(253,254,251,.96); border: 1px solid var(--line); border-radius: 14px; box-shadow: 0 22px 50px rgba(24, 37, 29, .14); }.login-mark { position: relative; width: 33px; height: 41px; margin-bottom: 22px; }.login-mark i { position: absolute; bottom: 5px; left: 15px; width: 1px; height: 29px; background: var(--sage); transform-origin: bottom; }.login-mark i::after { position: absolute; top: 2px; left: 0; width: 12px; height: 7px; content: ""; background: #8cac82; border-radius: 10px 1px 10px 1px; transform: rotate(-26deg); transform-origin: left bottom; }.login-mark i:nth-child(1) { height: 23px; transform: rotate(-33deg); }.login-mark i:nth-child(3) { height: 24px; transform: rotate(35deg); }.login-card h1 { margin: 5px 0 8px; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 29px; font-weight: 500; }.login-intro { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.65; }.login-card form { display: grid; margin-top: 25px; gap: 15px; }.login-card label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.login-card select, .password-field { width: 100%; height: 42px; padding: 0 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.password-field { display: flex; align-items: center; gap: 8px; }.password-field svg { color: #78877d; }.password-field input { width: 100%; min-width: 0; border: 0; outline: 0; }.login-submit { width: 100%; justify-content: center; margin-top: 2px; }.login-state { display: flex; margin-top: 25px; align-items: center; color: var(--muted); font-size: 13px; gap: 8px; }.login-state--error { align-items: flex-start; color: #a6633b; flex-wrap: wrap; }.login-state button { margin-left: 27px; }.login-error { margin: -3px 0 0; color: #a6633b; font-size: 12px; }.login-note { display: flex; margin: 24px 0 0; color: #7c887f; font-size: 11px; line-height: 1.55; gap: 6px; }.login-note svg { flex: 0 0 auto; color: var(--sage); }
</style>
