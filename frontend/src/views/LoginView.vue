<script setup lang="ts">
import { KeyRound, LogIn, ShieldCheck } from '@lucide/vue'
import { ref } from 'vue'

import { loginAccount } from '@/api/client'
import { useBranding } from '@/composables/useBranding'
import type { AccountLoginResponse } from '@/types'

const emit = defineEmits<{ authenticated: [account: AccountLoginResponse] }>()
const username = ref('')
const password = ref('')
const submitting = ref(false)
const error = ref('')
const { branding, brandTitle } = useBranding()

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

</script>

<template>
  <main class="login-page">
    <section class="login-card">
      <img v-if="branding.logo_url" class="login-logo" :src="branding.logo_url" alt="" />
      <div v-else class="login-mark"><i></i><i></i><i></i></div>
      <p class="eyebrow">{{ brandTitle }}</p>
      <h1>管理员登录</h1>
      <p class="login-intro">请输入管理员账号和密码。账号名也将用于生成 SCP 上传命令。</p>
      <form @submit.prevent="login">
        <label>账号<input v-model="username" required autocomplete="username" maxlength="80" pattern="[a-z0-9]+" placeholder="输入账号名" :aria-invalid="Boolean(error)" :aria-describedby="error ? 'login-error' : undefined" /></label>
        <label>密码<div class="password-field"><KeyRound :size="16" /><input v-model="password" required type="password" autocomplete="current-password" placeholder="输入统一初始密码" :aria-invalid="Boolean(error)" :aria-describedby="error ? 'login-error' : undefined" /></div></label>
        <p v-if="error" id="login-error" class="login-error" role="alert">{{ error }}</p>
        <button class="button button--primary login-submit" :disabled="submitting || !password" type="submit"><LogIn :size="16" />{{ submitting ? '正在验证' : '进入归档系统' }}</button>
      </form>
      <p class="login-note"><ShieldCheck :size="15" /> 注册功能暂未开放；该入口仅适用于实验室局域网。</p>
    </section>
  </main>
</template>

<style scoped>
.login-page { display: grid; min-height: 100vh; padding: 24px; place-items: center; background: #f2f4ee; }.login-card { width: min(100%, 420px); padding: 37px; background: rgba(253,254,251,.96); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 22px 50px rgba(24, 37, 29, .12); }.login-logo { display: block; width: 42px; height: 42px; margin-bottom: 20px; object-fit: contain; }.login-mark { position: relative; width: 33px; height: 41px; margin-bottom: 22px; }.login-mark i { position: absolute; bottom: 5px; left: 15px; width: 1px; height: 29px; background: var(--sage); transform-origin: bottom; }.login-mark i::after { position: absolute; top: 2px; left: 0; width: 12px; height: 7px; content: ""; background: color-mix(in srgb, var(--sage) 60%, white); border-radius: 10px 1px 10px 1px; transform: rotate(-26deg); transform-origin: left bottom; }.login-mark i:nth-child(1) { height: 23px; transform: rotate(-33deg); }.login-mark i:nth-child(3) { height: 24px; transform: rotate(35deg); }.login-card h1 { margin: 5px 0 8px; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 29px; font-weight: 500; }.login-intro { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.65; }.login-card form { display: grid; margin-top: 25px; gap: 15px; }.login-card label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.login-card > form > label > input, .password-field { width: 100%; height: 42px; padding: 0 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.password-field { display: flex; align-items: center; gap: 8px; }.password-field:focus-within { border-color: var(--sage); box-shadow: 0 0 0 3px var(--sage-soft); }.password-field svg { color: #78877d; }.password-field input { width: 100%; min-width: 0; border: 0; outline: 0; }.login-submit { width: 100%; justify-content: center; margin-top: 2px; }.login-error { margin: -3px 0 0; color: #a6633b; font-size: 12px; }.login-note { display: flex; margin: 24px 0 0; color: #7c887f; font-size: 11px; line-height: 1.55; gap: 6px; }.login-note svg { flex: 0 0 auto; color: var(--sage); }
</style>
