<script setup lang="ts">
import { CheckCircle2, KeyRound, Link2, ShieldCheck } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { acceptAccountInvitation, getAccountInvitation } from '@/api/client'
import { useBranding } from '@/composables/useBranding'
import type { AccountInvitationStatus, AccountLoginResponse } from '@/types'

const emit = defineEmits<{ authenticated: [account: AccountLoginResponse] }>()
const route = useRoute()
const token = computed(() => {
  const legacyToken = String(route.params.token ?? '')
  if (legacyToken) return legacyToken
  return new URLSearchParams(route.hash.slice(1)).get('token') ?? ''
})
const invitation = ref<AccountInvitationStatus | null>(null)
const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const form = ref(emptyForm())
const { branding, brandTitle } = useBranding()
let submitController: AbortController | null = null

const isRegistration = computed(() => invitation.value?.purpose === 'registration')
const formValid = computed(() => (
  Boolean(invitation.value)
  && (!isRegistration.value || Boolean(form.value.name.trim() && form.value.email.trim()))
  && form.value.password.length >= 10
  && form.value.password === form.value.passwordConfirmation
))

function emptyForm() {
  return {
    name: '',
    email: '',
    password: '',
    passwordConfirmation: '',
  }
}

function isAbortError(reason: unknown) {
  return reason instanceof DOMException && reason.name === 'AbortError'
}

function formatExpiry(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

async function loadInvitation(currentToken: string, signal: AbortSignal) {
  loading.value = true
  invitation.value = null
  error.value = ''
  if (!currentToken) {
    error.value = '注册链接缺少邀请凭据。'
    loading.value = false
    return
  }
  try {
    const result = await getAccountInvitation(currentToken, signal)
    if (!signal.aborted) invitation.value = result
  } catch (reason) {
    if (signal.aborted || isAbortError(reason)) return
    error.value = reason instanceof Error ? reason.message : '注册链接无效或已失效。'
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

async function submit() {
  if (!invitation.value || !formValid.value) return
  const currentToken = token.value
  submitController?.abort()
  const controller = new AbortController()
  submitController = controller
  submitting.value = true
  error.value = ''
  try {
    const response = await acceptAccountInvitation(currentToken, {
      ...(isRegistration.value
        ? { name: form.value.name.trim(), email: form.value.email.trim().toLowerCase() }
        : {}),
      password: form.value.password,
    }, controller.signal)
    if (!controller.signal.aborted) emit('authenticated', response)
  } catch (reason) {
    if (controller.signal.aborted || isAbortError(reason)) return
    error.value = reason instanceof Error ? reason.message : '无法完成账号设置'
  } finally {
    if (submitController === controller) {
      submitController = null
      submitting.value = false
    }
  }
}

watch(token, (currentToken, _previousToken, onCleanup) => {
  submitController?.abort()
  submitting.value = false
  form.value = emptyForm()
  const controller = new AbortController()
  onCleanup(() => controller.abort())
  void loadInvitation(currentToken, controller.signal)
}, { immediate: true })

onBeforeUnmount(() => submitController?.abort())
</script>

<template>
  <main class="registration-page">
    <section class="registration-card">
      <img v-if="branding.logo_url" class="registration-logo" :src="branding.logo_url" alt="" />
      <span v-else class="registration-mark"><Link2 :size="25" /></span>
      <p class="eyebrow">{{ brandTitle }} · ACCOUNT INVITATION</p>

      <div v-if="loading" class="registration-state" role="status">
        <span class="loader-ring"></span>
        <p>正在验证邀请链接…</p>
      </div>

      <div v-else-if="!invitation" class="registration-state registration-state--error" role="alert">
        <KeyRound :size="27" />
        <h1>链接不可用</h1>
        <p>{{ error }}</p>
        <a class="button button--outline" href="/">返回登录</a>
      </div>

      <template v-else>
        <h1>{{ isRegistration ? '完成管理员注册' : '设置新的登录密码' }}</h1>
        <p class="registration-intro">
          账号 <strong>{{ invitation.username }}</strong>
          {{ isRegistration ? '已由管理员预留，请补充个人信息。' : '正在使用一次性恢复链接。' }}
        </p>
        <form @submit.prevent="submit">
          <template v-if="isRegistration">
            <label>显示名称<input v-model="form.name" required autocomplete="name" maxlength="80" placeholder="输入你的姓名" /></label>
            <label>邮箱<input v-model="form.email" required type="email" autocomplete="email" maxlength="255" placeholder="输入你的邮箱" /></label>
          </template>
          <label>密码<div class="registration-password"><KeyRound :size="16" /><input v-model="form.password" required type="password" autocomplete="new-password" minlength="10" maxlength="256" placeholder="至少 10 位" /></div></label>
          <label>确认密码<div class="registration-password"><KeyRound :size="16" /><input v-model="form.passwordConfirmation" required type="password" autocomplete="new-password" minlength="10" maxlength="256" placeholder="再次输入密码" :aria-invalid="Boolean(form.passwordConfirmation) && form.password !== form.passwordConfirmation" /></div></label>
          <p v-if="form.passwordConfirmation && form.password !== form.passwordConfirmation" class="registration-error" role="alert">两次输入的密码不一致。</p>
          <p v-if="error" class="registration-error" role="alert">{{ error }}</p>
          <button class="button button--primary registration-submit" :disabled="submitting || !formValid" type="submit"><CheckCircle2 :size="16" />{{ submitting ? '正在保存' : isRegistration ? '完成注册并登录' : '更新密码并登录' }}</button>
        </form>
        <p class="registration-note"><ShieldCheck :size="15" />链接有效至 {{ formatExpiry(invitation.expires_at) }}，成功使用一次后立即失效。</p>
      </template>
    </section>
  </main>
</template>

<style scoped>
.registration-page { display: grid; min-height: 100vh; padding: 24px; place-items: center; background: #f2f4ee; }
.registration-card { width: min(100%, 460px); padding: 37px; background: rgba(253,254,251,.97); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 22px 50px rgba(24,37,29,.12); }
.registration-logo { display: block; width: 42px; height: 42px; margin-bottom: 20px; object-fit: contain; }
.registration-mark { display: grid; width: 42px; height: 42px; margin-bottom: 20px; color: var(--sage); place-items: center; background: var(--sage-soft); border-radius: 50%; }
.registration-card h1 { margin: 6px 0 8px; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 29px; font-weight: 500; }
.registration-intro { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.65; }
.registration-intro strong { color: var(--ink); }
.registration-card form { display: grid; margin-top: 24px; gap: 14px; }
.registration-card label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }
.registration-card label > input, .registration-password { width: 100%; height: 42px; padding: 0 10px; color: var(--ink); font: inherit; font-size: 13px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }
.registration-password { display: flex; align-items: center; gap: 8px; }
.registration-password:focus-within { border-color: var(--sage); box-shadow: 0 0 0 3px var(--sage-soft); }
.registration-password svg { color: #78877d; flex: 0 0 auto; }
.registration-password input { width: 100%; min-width: 0; border: 0; outline: 0; }
.registration-submit { width: 100%; margin-top: 3px; justify-content: center; }
.registration-error { margin: -3px 0 0; color: #a6633b; font-size: 12px; }
.registration-note { display: flex; margin: 22px 0 0; color: #7c887f; font-size: 10px; line-height: 1.55; gap: 6px; }
.registration-note svg { color: var(--sage); flex: 0 0 auto; }
.registration-state { display: grid; min-height: 190px; place-items: center; align-content: center; color: var(--muted); text-align: center; gap: 10px; }
.registration-state p { margin: 0; font-size: 12px; }
.registration-state h1 { margin: 0; }
.registration-state--error svg { color: #a6633b; }
.registration-state .button { margin-top: 6px; justify-content: center; }
@media (max-width: 520px) { .registration-page { padding: 16px; }.registration-card { padding: 27px 21px; } }
</style>
