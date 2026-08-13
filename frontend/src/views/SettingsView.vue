<script setup lang="ts">
import { Check, CircleAlert, ImageUp, Palette, Plus, RefreshCw, RotateCcw, Save, ShieldCheck, UserRound, UserRoundX, X } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { createAdminAccount, getAdminAccounts, getCurrentAccount, removeInstanceLogo, updateAdminAccount, updateInstanceBranding, uploadInstanceLogo } from '@/api/client'
import { useBranding } from '@/composables/useBranding'
import { useOverlayFocus } from '@/composables/useOverlayFocus'
import type { AccountSummary, InstanceBrandingInput } from '@/types'

const accounts = ref<AccountSummary[]>([])
const currentUsername = ref('')
const loading = ref(true)
const updatingUsername = ref<string | null>(null)
const error = ref('')
const createOpen = ref(false)
const createDialog = ref<HTMLElement | null>(null)
const creating = ref(false)
const createError = ref('')
const form = ref({ username: '', name: '', email: '' })
const brandingSaving = ref(false)
const logoUpdating = ref(false)
const brandingMessage = ref('')
const brandingError = ref('')
const logoInput = ref<HTMLInputElement | null>(null)
const { branding, applyBranding, pageEyebrow } = useBranding()
const brandingForm = ref<InstanceBrandingInput>({
  product_name: branding.product_name,
  product_subtitle: branding.product_subtitle,
  organization_name: branding.organization_name,
  slogan: branding.slogan,
  slogan_secondary: branding.slogan_secondary,
  primary_color: branding.primary_color,
})

useOverlayFocus(createOpen, createDialog, closeCreate)

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

async function saveBranding() {
  brandingSaving.value = true
  brandingError.value = ''
  brandingMessage.value = ''
  try {
    const updated = await updateInstanceBranding(brandingForm.value)
    applyBranding(updated)
    brandingForm.value.primary_color = updated.primary_color
    brandingMessage.value = '品牌设置已应用'
  } catch (reason) {
    brandingError.value = reason instanceof Error ? reason.message : '无法保存品牌设置'
  } finally {
    brandingSaving.value = false
  }
}

async function selectLogo(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  logoUpdating.value = true
  brandingError.value = ''
  brandingMessage.value = ''
  try {
    applyBranding(await uploadInstanceLogo(file))
    brandingMessage.value = 'Logo 已更新'
  } catch (reason) {
    brandingError.value = reason instanceof Error ? reason.message : '无法上传 Logo'
  } finally {
    logoUpdating.value = false
    if (logoInput.value) logoInput.value.value = ''
  }
}

async function restoreDefaultLogo() {
  logoUpdating.value = true
  brandingError.value = ''
  brandingMessage.value = ''
  try {
    applyBranding(await removeInstanceLogo())
    brandingMessage.value = '已恢复默认标志'
  } catch (reason) {
    brandingError.value = reason instanceof Error ? reason.message : '无法恢复默认标志'
  } finally {
    logoUpdating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page settings-page">
    <header class="page-heading settings-heading">
      <div>
        <p class="eyebrow">{{ pageEyebrow('ADMINISTRATION') }}</p>
        <h1>系统设置</h1>
        <p>配置当前 DataManager 实例的品牌与管理员账号。</p>
      </div>
      <div class="settings-actions"><button class="button button--outline" :disabled="loading" @click="load"><RefreshCw :size="16" />刷新</button><button class="button button--primary" @click="openCreate"><Plus :size="16" />新增管理员</button></div>
    </header>

    <section class="branding-panel" aria-labelledby="branding-title">
      <header>
        <span class="settings-section-icon"><Palette :size="18" /></span>
        <div><h2 id="branding-title">品牌与外观</h2><p>设置名称、组织标语、主题主色和实例 Logo，保存后全站立即生效。</p></div>
        <span>INSTANCE IDENTITY</span>
      </header>
      <div class="branding-workspace">
        <form class="branding-form" @submit.prevent="saveBranding">
          <div class="branding-fields branding-fields--names">
            <label>产品名称<input v-model="brandingForm.product_name" required maxlength="80" placeholder="例如：SAGE" /></label>
            <label>产品副标题<input v-model="brandingForm.product_subtitle" required maxlength="120" placeholder="例如：RESEARCH ARCHIVE" /></label>
            <label>组织名称<input v-model="brandingForm.organization_name" required maxlength="120" placeholder="例如：SAGE Lab" /></label>
          </div>
          <div class="branding-fields branding-fields--slogans">
            <label>主标语<input v-model="brandingForm.slogan" required maxlength="160" placeholder="例如：求真 · 典藏 · 生长 · 卓越" /></label>
            <label>辅助标语<input v-model="brandingForm.slogan_secondary" required maxlength="160" placeholder="例如：Science · Archive · Growth · Excellence" /></label>
          </div>
          <div class="branding-controls">
            <label class="color-field">品牌主色<span><input v-model="brandingForm.primary_color" type="color" /><input v-model="brandingForm.primary_color" required maxlength="7" pattern="#[0-9A-Fa-f]{6}" aria-label="品牌主色色值" /></span></label>
            <div class="logo-control"><span>实例 Logo</span><div><input ref="logoInput" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" @change="selectLogo" /><button class="button button--outline" type="button" :disabled="logoUpdating" @click="logoInput?.click()"><ImageUp :size="15" />{{ logoUpdating ? '处理中' : '上传图片' }}</button><button v-if="branding.logo_url" class="button button--quiet" type="button" :disabled="logoUpdating" @click="restoreDefaultLogo"><RotateCcw :size="15" />恢复默认</button></div><small>PNG、JPEG 或 WebP，最大 1 MB</small></div>
          </div>
          <div class="branding-feedback"><p v-if="brandingError" class="settings-error" role="alert">{{ brandingError }}</p><p v-else-if="brandingMessage" class="settings-success" role="status"><Check :size="14" />{{ brandingMessage }}</p><button class="button button--primary" :disabled="brandingSaving" type="submit"><Save :size="16" />{{ brandingSaving ? '正在保存' : '保存品牌设置' }}</button></div>
        </form>
        <aside class="brand-preview" :style="{ '--preview-color': brandingForm.primary_color }" aria-label="品牌预览">
          <span>LIVE PREVIEW</span>
          <div class="brand-preview-lockup">
            <img v-if="branding.logo_url" :src="branding.logo_url" alt="" />
            <span v-else class="preview-mark"><i></i><i></i><i></i></span>
            <div><strong>{{ brandingForm.product_name || 'DataManager' }}</strong><small>{{ brandingForm.product_subtitle }}</small></div>
          </div>
          <div class="brand-preview-signature"><strong>{{ brandingForm.organization_name }}</strong><p>{{ brandingForm.slogan }}</p><small>{{ brandingForm.slogan_secondary }}</small></div>
        </aside>
      </div>
    </section>

    <div v-if="loading" class="state-panel" role="status" aria-live="polite"><span class="loader-ring"></span><p>正在读取账号设置…</p></div>
    <div v-else-if="error && !accounts.length" class="state-panel state-panel--error" role="alert"><CircleAlert :size="28" /><strong>无法读取账号</strong><p>{{ error }}</p><button class="button button--outline" @click="load">重试</button></div>
    <template v-else>
      <section class="settings-summary accounts-summary">
        <div><ShieldCheck :size="19" /><span><strong>{{ activeCount }}</strong><small>启用管理员</small></span></div>
        <div><UserRound :size="19" /><span><strong>{{ accounts.length }}</strong><small>已预置账号</small></span></div>
        <p>账号名同时用于生成 SCP 上传命令中的服务器用户名。</p>
      </section>
      <p v-if="error" class="settings-error" role="alert">{{ error }}</p>
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
      <form ref="createDialog" class="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="create-account-title" @submit.prevent="createAccount">
        <button class="settings-close" type="button" :disabled="creating" aria-label="关闭" @click="closeCreate"><X :size="18" /></button>
        <p class="eyebrow">PREPROVISION ACCOUNT</p>
        <h2 id="create-account-title">新增管理员账号</h2>
        <p>账号名应与服务器 SSH 用户名一致；创建后可立即用统一初始密码登录。</p>
        <label>账号名<input v-model="form.username" required autofocus autocomplete="off" pattern="[a-z0-9]+" maxlength="80" placeholder="例如：newmember" /></label>
        <label>显示名称<input v-model="form.name" required maxlength="80" placeholder="例如：张三" /></label>
        <label>邮箱<input v-model="form.email" required type="email" maxlength="255" placeholder="例如：newmember@sage.lab" /></label>
        <p v-if="createError" class="settings-error" role="alert">{{ createError }}</p>
        <footer><button class="button button--outline" type="button" :disabled="creating" @click="closeCreate">取消</button><button class="button button--primary" :disabled="creating || !form.username.trim() || !form.name.trim() || !form.email.trim()" type="submit"><Plus :size="16" />{{ creating ? '正在创建' : '创建管理员' }}</button></footer>
      </form>
    </div>
  </div>
</template>

<style scoped>
.settings-heading { align-items: center; }.settings-actions { display: flex; gap: 9px; }.branding-panel { margin-bottom: 18px; background: rgba(252,253,249,.94); border: 1px solid var(--line); border-radius: 8px; }.branding-panel > header { display: flex; min-height: 70px; padding: 16px 20px; align-items: center; gap: 12px; border-bottom: 1px solid var(--line); }.branding-panel h2, .accounts-panel h2, .settings-dialog h2 { margin: 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 21px; font-weight: 500; }.branding-panel header p, .accounts-panel header p, .settings-dialog > p { margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.55; }.branding-panel > header > span:last-child { margin-left: auto; color: #89968e; font-size: 9px; letter-spacing: .12em; }.settings-section-icon { display: grid; width: 34px; height: 34px; color: var(--sage); place-items: center; background: var(--sage-soft); border-radius: 5px; }.branding-workspace { display: grid; grid-template-columns: minmax(0, 1fr) 260px; }.branding-form { display: grid; padding: 20px; gap: 15px; border-right: 1px solid var(--line); }.branding-fields { display: grid; gap: 11px; }.branding-fields--names { grid-template-columns: .8fr 1fr 1fr; }.branding-fields--slogans { grid-template-columns: 1fr 1fr; }.branding-form label, .logo-control > span { display: grid; color: #526056; font-size: 11px; font-weight: 700; gap: 6px; }.branding-form input:not([type="color"]), .settings-dialog input { width: 100%; min-width: 0; padding: 9px 10px; color: var(--ink); font: inherit; font-size: 12px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.branding-controls { display: grid; grid-template-columns: minmax(190px, .65fr) 1fr; gap: 20px; }.color-field > span { display: grid; grid-template-columns: 42px 1fr; }.color-field input[type="color"] { width: 42px; height: 36px; padding: 4px; background: #fff; border: 1px solid var(--line); border-right: 0; border-radius: 5px 0 0 5px; cursor: pointer; }.color-field input[type="text"] { border-radius: 0 5px 5px 0; }.logo-control { display: grid; gap: 6px; }.logo-control > div { display: flex; gap: 7px; }.logo-control small { color: var(--muted); font-size: 9px; }.branding-feedback { display: flex; min-height: 34px; align-items: center; justify-content: flex-end; gap: 12px; }.branding-feedback p { margin: 0 auto 0 0; }.settings-success { display: flex; color: var(--sage); align-items: center; font-size: 11px; gap: 5px; }.brand-preview { --preview-color: var(--sage); display: flex; min-width: 0; padding: 20px; flex-direction: column; color: #fff; background: var(--preview-color); }.brand-preview > span { font-size: 8px; font-weight: 800; letter-spacing: .16em; opacity: .65; }.brand-preview-lockup { display: flex; margin-top: 27px; align-items: center; gap: 11px; }.brand-preview-lockup img { width: 40px; height: 40px; object-fit: contain; filter: brightness(0) invert(1); }.brand-preview-lockup > div { display: grid; min-width: 0; gap: 2px; }.brand-preview-lockup strong { overflow: hidden; font-family: "Iowan Old Style", serif; font-size: 22px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }.brand-preview-lockup small { overflow: hidden; font-size: 7px; letter-spacing: .12em; opacity: .75; text-overflow: ellipsis; white-space: nowrap; }.preview-mark { position: relative; width: 34px; height: 40px; flex: 0 0 34px; }.preview-mark i { position: absolute; bottom: 5px; left: 16px; width: 1px; height: 28px; background: #fff; transform-origin: bottom; }.preview-mark i::after { position: absolute; top: 2px; width: 11px; height: 6px; content: ""; background: rgba(255,255,255,.72); border-radius: 8px 1px 8px 1px; transform: rotate(-25deg); }.preview-mark i:first-child { height: 22px; transform: rotate(-32deg); }.preview-mark i:last-child { height: 23px; transform: rotate(34deg); }.brand-preview-signature { margin-top: auto; padding-top: 42px; border-top: 1px solid rgba(255,255,255,.24); }.brand-preview-signature strong { font-family: "Iowan Old Style", "Songti SC", serif; font-size: 15px; }.brand-preview-signature p { margin: 5px 0 3px; font-size: 10px; }.brand-preview-signature small { display: block; overflow: hidden; font-size: 8px; opacity: .7; text-overflow: ellipsis; white-space: nowrap; }.settings-summary { display: flex; margin-bottom: 16px; padding: 16px 20px; align-items: center; gap: 28px; background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 8px; }.settings-summary > div { display: flex; min-width: 120px; align-items: center; gap: 9px; }.settings-summary svg { color: var(--sage); }.settings-summary span { display: grid; gap: 2px; }.settings-summary strong { font-family: "Iowan Old Style", "Songti SC", serif; font-size: 20px; font-weight: 500; }.settings-summary small, .settings-summary p { color: var(--muted); font-size: 11px; }.settings-summary p { margin: 0 0 0 auto; }.accounts-panel { background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 8px; }.accounts-panel > header { display: flex; padding: 19px 20px; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); }.accounts-panel header > span { color: #89968e; font-size: 10px; }.account-row { display: grid; min-height: 68px; padding: 10px 20px; align-items: center; grid-template-columns: 34px minmax(180px, 1fr) 90px 80px auto; gap: 12px; border-bottom: 1px solid #edf0eb; }.account-row:last-child { border-bottom: 0; }.account-row--inactive { opacity: .6; }.account-avatar { display: grid; width: 32px; height: 32px; color: #fff; place-items: center; background: var(--sage); border-radius: 50%; font-size: 12px; font-weight: 800; }.account-copy { display: grid; min-width: 0; gap: 3px; }.account-copy strong { font-size: 12px; }.account-copy small { overflow: hidden; color: #7c887f; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.account-role { color: #56705f; font-size: 10px; font-weight: 700; text-transform: uppercase; }.account-status { color: var(--sage); font-size: 10px; font-weight: 700; }.account-status--inactive { color: #a6633b; }.account-toggle { min-width: 72px; justify-content: center; }.settings-error { margin: 0 0 13px; color: #a6633b; font-size: 12px; }.settings-backdrop { position: fixed; z-index: 40; inset: 0; display: grid; padding: 20px; place-items: center; background: rgba(23,34,26,.48); }.settings-dialog { position: relative; display: grid; width: min(100%, 500px); padding: 28px; background: #fdfefb; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 20px 50px rgba(24,37,29,.22); gap: 12px; }.settings-dialog label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.settings-dialog footer { display: flex; margin-top: 7px; justify-content: flex-end; gap: 9px; }.settings-close { position: absolute; top: 12px; right: 12px; display: grid; width: 31px; height: 31px; color: #68776d; place-items: center; background: transparent; border: 0; border-radius: 50%; cursor: pointer; }.settings-close:hover { background: #eef2ed; } @media (max-width: 900px) { .branding-workspace { grid-template-columns: 1fr; }.branding-form { border-right: 0; }.brand-preview { min-height: 190px; }.brand-preview-signature { padding-top: 22px; } } @media (max-width: 720px) { .settings-heading { align-items: flex-start; }.settings-actions { margin-top: 3px; }.branding-panel > header > span:last-child { display: none; }.branding-fields--names, .branding-fields--slogans, .branding-controls { grid-template-columns: 1fr; }.settings-summary { align-items: flex-start; flex-wrap: wrap; gap: 17px; }.settings-summary p { width: 100%; margin-left: 0; }.account-row { padding: 12px 14px; grid-template-columns: 34px minmax(0, 1fr) auto; }.account-role { display: none; }.account-status { grid-column: 2; }.account-toggle { grid-column: 3; grid-row: 1 / 3; } } @media (max-width: 460px) { .settings-actions { width: 100%; }.settings-actions .button { min-width: 0; flex: 1; white-space: nowrap; }.branding-form, .brand-preview { padding: 16px; }.branding-feedback { align-items: stretch; flex-direction: column; }.branding-feedback .button { width: 100%; justify-content: center; }.logo-control > div { align-items: stretch; flex-direction: column; } }
</style>
