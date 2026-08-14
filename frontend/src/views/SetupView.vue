<script setup lang="ts">
import { KeyRound, ServerCog, ShieldCheck, UserRoundPlus } from '@lucide/vue'
import { computed, ref } from 'vue'

import { initializeInstance } from '@/api/client'
import { useBranding } from '@/composables/useBranding'
import type { AccountLoginResponse } from '@/types'

const props = defineProps<{ authenticationReady: boolean }>()
const emit = defineEmits<{ authenticated: [account: AccountLoginResponse] }>()
const { branding, brandTitle } = useBranding()
const form = ref({ username: '', name: '', email: '', password: '', passwordConfirmation: '' })
const submitting = ref(false)
const error = ref('')
const passwordsMatch = computed(() => (
  !form.value.passwordConfirmation || form.value.password === form.value.passwordConfirmation
))
const canSubmit = computed(() => Boolean(
  props.authenticationReady
  && form.value.username.trim()
  && form.value.name.trim()
  && form.value.email.trim()
  && form.value.password.length >= 10
  && form.value.password === form.value.passwordConfirmation,
))

async function initialize() {
  if (!canSubmit.value || submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    emit('authenticated', await initializeInstance({
      username: form.value.username.trim().toLowerCase(),
      name: form.value.name.trim(),
      email: form.value.email.trim().toLowerCase(),
      password: form.value.password,
    }))
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法初始化管理员账号'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="setup-page">
    <section class="setup-panel" aria-labelledby="setup-title">
      <header class="setup-heading">
        <img v-if="branding.logo_url" class="setup-logo" :src="branding.logo_url" alt="" />
        <span v-else class="setup-icon"><ServerCog :size="22" /></span>
        <div>
          <p class="eyebrow">{{ brandTitle }} · FIRST RUN</p>
          <h1 id="setup-title">初始化管理员</h1>
        </div>
      </header>
      <p class="setup-intro">这是当前实例的首次启动。创建实例所有者后，初始化入口将永久关闭。</p>

      <div v-if="!authenticationReady" class="setup-blocker" role="alert">
        <ShieldCheck :size="18" />
        <p>
          <strong>服务器配置尚未完成</strong>
          <span>请设置 <code>SAGE_AUTH_SESSION_SECRET</code> 并重启后端，再创建首个管理员。</span>
        </p>
      </div>

      <form @submit.prevent="initialize">
        <div class="setup-grid">
          <label>
            管理员账号
            <input v-model="form.username" required autofocus autocomplete="username" pattern="[a-z0-9]+" maxlength="80" placeholder="例如：admin" />
          </label>
          <label>
            显示名称
            <input v-model="form.name" required autocomplete="name" maxlength="80" placeholder="例如：实验室管理员" />
          </label>
        </div>
        <label>
          邮箱
          <input v-model="form.email" required type="email" autocomplete="email" maxlength="255" placeholder="admin@example.org" />
        </label>
        <div class="setup-grid">
          <label>
            管理员密码
            <span class="setup-password">
              <KeyRound :size="16" />
              <input v-model="form.password" required type="password" autocomplete="new-password" minlength="10" maxlength="256" placeholder="至少 10 位" />
            </span>
          </label>
          <label>
            确认密码
            <span class="setup-password" :class="{ 'setup-password--invalid': !passwordsMatch }">
              <KeyRound :size="16" />
              <input v-model="form.passwordConfirmation" required type="password" autocomplete="new-password" minlength="10" maxlength="256" placeholder="再次输入密码" :aria-invalid="!passwordsMatch" />
            </span>
          </label>
        </div>
        <p v-if="!passwordsMatch" class="setup-error" role="alert">两次输入的密码不一致。</p>
        <p v-else-if="error" class="setup-error" role="alert">{{ error }}</p>
        <button class="button button--primary setup-submit" type="submit" :disabled="submitting || !canSubmit">
          <UserRoundPlus :size="17" />
          {{ submitting ? '正在初始化' : '创建管理员并进入系统' }}
        </button>
      </form>
      <p class="setup-footnote">
        <ShieldCheck :size="15" />
        后续账号只能由已登录管理员在系统设置中创建。
      </p>
    </section>
  </main>
</template>

<style scoped>
.setup-page {
  display: grid;
  min-height: 100vh;
  padding: 24px;
  place-items: center;
  background: #f2f4ee;
}

.setup-panel {
  width: min(100%, 590px);
  padding: 34px;
  background: rgba(253, 254, 251, 0.97);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 22px 50px rgba(24, 37, 29, 0.12);
}

.setup-heading {
  display: flex;
  align-items: center;
  gap: 13px;
}

.setup-heading h1 {
  margin: 4px 0 0;
  font-family: "Iowan Old Style", "Songti SC", serif;
  font-size: 29px;
  font-weight: 500;
}

.setup-icon {
  display: grid;
  width: 42px;
  height: 42px;
  color: #fff;
  place-items: center;
  background: var(--sage);
  border-radius: 6px;
}

.setup-logo {
  width: 42px;
  height: 42px;
  object-fit: contain;
}

.setup-intro {
  margin: 16px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.65;
}

.setup-blocker {
  display: flex;
  margin-top: 18px;
  padding: 12px 13px;
  color: #8f5938;
  align-items: flex-start;
  background: #fff6ef;
  border-left: 3px solid #bd7750;
  gap: 9px;
}

.setup-blocker svg {
  margin-top: 1px;
  flex: 0 0 auto;
}

.setup-blocker p {
  display: grid;
  margin: 0;
  gap: 3px;
}

.setup-blocker strong {
  font-size: 12px;
}

.setup-blocker span,
.setup-blocker code {
  font-size: 10px;
}

.setup-blocker span {
  line-height: 1.55;
}

.setup-panel form {
  display: grid;
  margin-top: 22px;
  gap: 14px;
}

.setup-panel label {
  display: grid;
  min-width: 0;
  color: #526056;
  font-size: 12px;
  font-weight: 700;
  gap: 6px;
}

.setup-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.setup-panel input {
  width: 100%;
  min-width: 0;
  height: 42px;
  padding: 0 10px;
  color: var(--ink);
  font: inherit;
  font-size: 13px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 5px;
  outline-color: var(--sage);
}

.setup-password {
  display: flex;
  height: 42px;
  padding: 0 10px;
  align-items: center;
  color: #78877d;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 5px;
  gap: 8px;
}

.setup-password:focus-within {
  border-color: var(--sage);
  box-shadow: 0 0 0 3px var(--sage-soft);
}

.setup-password--invalid {
  border-color: #bd7750;
}

.setup-password input {
  height: auto;
  padding: 0;
  border: 0;
  outline: 0;
}

.setup-error {
  margin: -2px 0 0;
  color: #a6633b;
  font-size: 12px;
}

.setup-submit {
  width: 100%;
  min-height: 42px;
  justify-content: center;
}

.setup-footnote {
  display: flex;
  margin: 21px 0 0;
  color: #78857d;
  font-size: 10px;
  line-height: 1.55;
  gap: 7px;
}

.setup-footnote svg {
  color: var(--sage);
  flex: 0 0 auto;
}

@media (max-width: 600px) {
  .setup-page {
    padding: 14px;
  }

  .setup-panel {
    padding: 25px 20px;
  }

  .setup-grid {
    grid-template-columns: 1fr;
  }

  .setup-heading h1 {
    font-size: 25px;
  }
}
</style>
