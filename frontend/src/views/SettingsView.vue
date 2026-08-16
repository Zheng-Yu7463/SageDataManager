<script setup lang="ts">
import { Bot, Check, CircleAlert, Clipboard, ExternalLink, GitMerge, ImageUp, KeyRound, Link2, Palette, Plus, RefreshCw, RotateCcw, Save, ServerCog, ShieldCheck, Trash2, UserRound, UserRoundX, X } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

import { ApiError, applySystemUpdate, checkSystemUpdate, createAccessToken, createAdminAccountInvitation, getAccessTokens, getAdminAccounts, getInstanceBranding, getSystemUpdateStatus, removeInstanceLogo, renewAdminAccountInvitation, revokeAccessToken, updateAdminAccount, updateInstanceBranding, uploadInstanceLogo } from '@/api/client'
import { useBranding } from '@/composables/useBranding'
import { useOverlayFocus } from '@/composables/useOverlayFocus'
import { useSession } from '@/session'
import type { AccessTokenCreated, AccessTokenSummary, AccountInvitationCreated, AccountSummary, AgentScope, InstanceBranding, InstanceBrandingInput, SystemUpdateStatus } from '@/types'
import { copyText } from '@/utils/textFiles'

const accounts = ref<AccountSummary[]>([])
const accessTokens = ref<AccessTokenSummary[]>([])
const accountsLoading = ref(true)
const tokensLoading = ref(true)
const updatingUsernames = ref(new Set<string>())
const issuingInvitationUsername = ref<string | null>(null)
const accountActionErrors = ref<Record<string, string>>({})
const accountsError = ref('')
const tokenLoadError = ref('')
const createOpen = ref(false)
const createDialog = ref<HTMLElement | null>(null)
const creating = ref(false)
const createError = ref('')
const form = ref({ username: '' })
const accountInvitation = ref<AccountInvitationCreated | null>(null)
const accountInvitationCopied = ref(false)
const tokenDialogOpen = ref(false)
const tokenDialog = ref<HTMLElement | null>(null)
const tokenCreating = ref(false)
const tokenError = ref('')
const tokenCopied = ref(false)
const createdToken = ref<AccessTokenCreated | null>(null)
const defaultTokenScopes: AgentScope[] = [
  'assets:read',
  'files:read',
  'metadata:write',
  'files:upload',
  'citations:export',
]
const tokenForm = ref({
  name: '',
  expiresInDays: 90,
  scopes: [...defaultTokenScopes],
})
const revokeTarget = ref<AccessTokenSummary | null>(null)
const revokeDialog = ref<HTMLElement | null>(null)
const revoking = ref(false)
const revokeError = ref('')
const tokenHistoryOpen = ref(false)
const brandingOperation = ref<'saving' | 'updating-logo' | null>(null)
const brandingUpdating = computed(() => brandingOperation.value !== null)
const brandingSaving = computed(() => brandingOperation.value === 'saving')
const logoUpdating = computed(() => brandingOperation.value === 'updating-logo')
const brandingLoading = ref(true)
const brandingMessage = ref('')
const brandingError = ref('')
const brandingLoadError = ref('')
const logoInput = ref<HTMLInputElement | null>(null)
const { branding, applyBranding, pageEyebrow } = useBranding()
const { account } = useSession()
const currentUsername = computed(() => account.value?.username ?? '')
const loading = computed(() => accountsLoading.value || tokensLoading.value || brandingLoading.value || systemUpdateLoading.value)
const accountsUnavailable = computed(() => accountsLoading.value || Boolean(accountsError.value))
const tokensUnavailable = computed(() => tokensLoading.value || Boolean(tokenLoadError.value))
const brandingUnavailable = computed(() => brandingLoading.value || Boolean(brandingLoadError.value))
const settingsMutationInProgress = computed(() => (
  creating.value
  || tokenCreating.value
  || revoking.value
  || brandingUpdating.value
  || updateSubmitting.value
  || updatingUsernames.value.size > 0
))
const savedBranding = ref<InstanceBranding>({ ...branding })
const brandingForm = ref<InstanceBrandingInput>(brandingInput(savedBranding.value))
const pendingLogoFile = ref<File | null>(null)
const pendingLogoUrl = ref<string | null>(null)
const pendingLogoRemoval = ref(false)
const brandingDirty = computed(() => JSON.stringify(brandingForm.value) !== JSON.stringify(brandingInput(savedBranding.value)))
const logoDirty = computed(() => pendingLogoFile.value !== null || pendingLogoRemoval.value)
const brandingHasUnsavedChanges = computed(() => brandingDirty.value || logoDirty.value)
const previewLogoUrl = computed(() => pendingLogoRemoval.value ? null : pendingLogoUrl.value || savedBranding.value.logo_url)
const brandingContrastRatio = computed(() => colorContrastRatio(brandingForm.value.primary_color))
const brandingFormValid = computed(() => (
  Object.entries(brandingForm.value)
    .filter(([key]) => key !== 'primary_color')
    .every(([, value]) => value.trim().length > 0)
  && /^#[0-9A-Fa-f]{6}$/.test(brandingForm.value.primary_color)
  && brandingContrastRatio.value >= 4.5
))

const systemUpdate = ref<SystemUpdateStatus | null>(null)
const systemUpdateLoading = ref(true)
const systemUpdateError = ref('')
const updateDialogOpen = ref(false)
const updateDialog = ref<HTMLElement | null>(null)
const updatePassword = ref('')
const updateSubmitting = ref(false)
const updateSubmitError = ref('')
const UPDATE_POLL_INTERVAL_MS = 2_000
const UPDATE_POLL_MAX_INTERVAL_MS = 30_000
let updatePollTimer: ReturnType<typeof setTimeout> | null = null
let updatePollFailureCount = 0
let settingsDisposed = false
let systemUpdateController: AbortController | undefined
let systemUpdateActionController: AbortController | undefined
let accountsController: AbortController | undefined
let tokensController: AbortController | undefined
let brandingController: AbortController | undefined
useOverlayFocus(createOpen, createDialog, closeCreate)
useOverlayFocus(tokenDialogOpen, tokenDialog, closeTokenDialog)
const revokeDialogOpen = computed(() => revokeTarget.value !== null)
useOverlayFocus(revokeDialogOpen, revokeDialog, closeRevokeDialog)
useOverlayFocus(updateDialogOpen, updateDialog, closeUpdateDialog)

const activeCount = computed(() => accounts.value.filter((item) => item.is_active && item.is_registered).length)
const accountFormValid = computed(() => Boolean(form.value.username.trim()))
const accountDialogHasUnsavedChanges = computed(() => (
  createOpen.value
  && (Boolean(form.value.username.trim()) || accountInvitation.value !== null)
))
const tokenFormValid = computed(() => (
  tokenForm.value.name.trim().length >= 2
  && tokenForm.value.name.trim().length <= 100
  && tokenForm.value.scopes.length > 0
))
const tokenDialogHasUnsavedChanges = computed(() => (
  tokenDialogOpen.value
  && (
    createdToken.value !== null
    || Boolean(tokenForm.value.name.trim())
    || tokenForm.value.expiresInDays !== 90
    || [...tokenForm.value.scopes].sort().join(',') !== [...defaultTokenScopes].sort().join(',')
  )
))
const settingsHasUnsavedChanges = computed(() => (
  brandingHasUnsavedChanges.value
  || accountDialogHasUnsavedChanges.value
  || tokenDialogHasUnsavedChanges.value
))
const accountInvitationUrl = computed(() => accountInvitation.value
  ? `${window.location.origin}${accountInvitation.value.registration_path}`
  : '')
const activeTokens = computed(() => accessTokens.value.filter((token) => tokenStatus(token) === 'active'))
const historicalTokens = computed(() => accessTokens.value.filter((token) => tokenStatus(token) !== 'active'))

const isInstanceOwner = computed(() => account.value?.is_instance_owner === true)
const canManageAccount = (target: AccountSummary) => (
  !target.is_instance_owner || isInstanceOwner.value
)
const systemUpdateBusy = computed(() => {
  if (systemUpdate.value?.backup_in_progress) return true
  const state = systemUpdate.value?.state
  return state === 'checking'
    || state === 'recovering'
    || state === 'backing_up'
    || state === 'pulling'
    || state === 'building'
    || state === 'restarting'
    || state === 'verifying'
})
const systemUpdateUnavailable = computed(() => (
  systemUpdateLoading.value
  || systemUpdateBusy.value
  || Boolean(systemUpdateError.value)
  || !systemUpdate.value?.enabled
  || !systemUpdate.value.update_available
  || !systemUpdate.value.checked_at
  || !systemUpdate.value.latest_commit
))
const currentCommitShort = computed(() => systemUpdate.value?.current_commit?.slice(0, 8) || '—')
const latestCommitShort = computed(() => systemUpdate.value?.latest_commit?.slice(0, 8) || '—')
const systemUpdateProgress = computed(() => {
  if (systemUpdate.value?.backup_in_progress) return 15
  const state = systemUpdate.value?.state
  if (!state) return 0
  const progress: Partial<Record<SystemUpdateStatus['state'], number>> = {
    checking: 8,
    recovering: 12,
    backing_up: 20,
    pulling: 35,
    building: 62,
    restarting: 82,
    verifying: 94,
    succeeded: 100,
  }
  return progress[state] ?? 0
})

function withoutPlaintextToken({ token: _token, ...summary }: AccessTokenCreated): AccessTokenSummary {
  return summary
}

function brandingInput(value: InstanceBranding): InstanceBrandingInput {
  return {
    product_name: value.product_name,
    product_subtitle: value.product_subtitle,
    organization_name: value.organization_name,
    slogan: value.slogan,
    slogan_secondary: value.slogan_secondary,
    primary_color: value.primary_color,
  }
}

function colorContrastRatio(hex: string) {
  if (!/^#[0-9A-Fa-f]{6}$/.test(hex)) return 0
  const channels = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255)
  const linear = channels.map((channel) => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
  const luminance = 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!
  return 1.05 / (luminance + 0.05)
}

function applyBrandingSnapshot(next: InstanceBranding, resetTextDraft: boolean) {
  savedBranding.value = { ...next }
  applyBranding(next)
  if (resetTextDraft) brandingForm.value = brandingInput(next)
}

function clearPendingLogo() {
  if (pendingLogoUrl.value) URL.revokeObjectURL(pendingLogoUrl.value)
  pendingLogoFile.value = null
  pendingLogoUrl.value = null
  pendingLogoRemoval.value = false
  if (logoInput.value) logoInput.value.value = ''
}

function resetBrandingDraft() {
  brandingForm.value = brandingInput(savedBranding.value)
  clearPendingLogo()
  brandingError.value = ''
  brandingMessage.value = ''
}

function preventUnsavedSettingsExit(event: BeforeUnloadEvent) {
  if (!settingsHasUnsavedChanges.value) return
  event.preventDefault()
  event.returnValue = ''
}

async function loadBrandingSettings() {
  brandingController?.abort()
  const controller = new AbortController()
  brandingController = controller
  brandingLoading.value = true
  brandingLoadError.value = ''
  brandingError.value = ''
  brandingMessage.value = ''
  try {
    const latest = await getInstanceBranding(controller.signal)
    if (brandingController !== controller) return
    applyBrandingSnapshot(latest, true)
    clearPendingLogo()
  } catch (reason) {
    if (brandingController !== controller) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    brandingLoadError.value = reason instanceof Error ? reason.message : '无法读取品牌设置'
  } finally {
    if (brandingController === controller) {
      brandingController = undefined
      brandingLoading.value = false
    }
  }
}

async function retryBrandingSettings() {
  if (
    brandingHasUnsavedChanges.value
    && !window.confirm('重新读取会丢弃尚未保存的品牌更改，是否继续？')
  ) return
  await loadBrandingSettings()
}

async function loadAccounts() {
  accountsController?.abort()
  const controller = new AbortController()
  accountsController = controller
  accountsLoading.value = true
  accountsError.value = ''
  accountActionErrors.value = {}
  try {
    const result = await getAdminAccounts(controller.signal)
    if (accountsController !== controller) return
    accounts.value = result
  } catch (reason) {
    if (accountsController !== controller) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    accountsError.value = reason instanceof Error ? reason.message : '无法读取管理员账号'
  } finally {
    if (accountsController === controller) {
      accountsController = undefined
      accountsLoading.value = false
    }
  }
}

async function loadTokens() {
  tokensController?.abort()
  const controller = new AbortController()
  tokensController = controller
  tokensLoading.value = true
  tokenLoadError.value = ''
  try {
    const result = await getAccessTokens(controller.signal)
    if (tokensController !== controller) return
    accessTokens.value = result
  } catch (reason) {
    if (tokensController !== controller) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    tokenLoadError.value = reason instanceof Error ? reason.message : '无法读取 AI 访问令牌'
  } finally {
    if (tokensController === controller) {
      tokensController = undefined
      tokensLoading.value = false
    }
  }
}

async function loadSystemUpdate(silent = false): Promise<boolean> {
  systemUpdateController?.abort()
  const controller = new AbortController()
  systemUpdateController = controller
  if (!silent) systemUpdateLoading.value = true
  if (!silent) systemUpdateError.value = ''
  try {
    const result = await getSystemUpdateStatus(controller.signal)
    if (systemUpdateController !== controller) return false
    systemUpdate.value = result
    updatePollFailureCount = 0
    systemUpdateError.value = ''
    if (systemUpdateBusy.value) startUpdatePolling()
    else stopUpdatePolling()
    return true
  } catch (reason) {
    if (systemUpdateController !== controller) return false
    if (reason instanceof DOMException && reason.name === 'AbortError') return false
    const message = reason instanceof Error ? reason.message : '无法读取系统版本'
    if (silent) {
      updatePollFailureCount += 1
      const retrySeconds = Math.round(updatePollDelay() / 1000)
      systemUpdateError.value = `更新状态暂时不可用：${message}；将在 ${retrySeconds} 秒后重试。`
    } else {
      systemUpdateError.value = message
    }
    return false
  } finally {
    if (systemUpdateController === controller) {
      systemUpdateController = undefined
      if (!silent) systemUpdateLoading.value = false
    }
  }
}

async function load() {
  await Promise.all([loadAccounts(), loadTokens(), loadBrandingSettings(), loadSystemUpdate()])
}

async function refreshSettings() {
  if (brandingHasUnsavedChanges.value && !window.confirm('刷新会丢弃尚未保存的品牌更改，是否继续？')) return
  if (settingsMutationInProgress.value) return
  systemUpdateActionController?.abort()
  systemUpdateActionController = undefined
  brandingMessage.value = ''
  await load()
}

const scopeOptions: { value: AgentScope; label: string; description: string }[] = [
  { value: 'assets:read', label: '查询资产', description: '搜索并读取资产元数据与文件索引' },
  { value: 'files:read', label: '读取文件', description: '预览或下载已索引的归档文件' },
  { value: 'metadata:write', label: '登记元数据', description: '创建论文、文献及其他资产记录' },
  { value: 'files:upload', label: '上传文件', description: '创建隔离任务并写入文件' },
  { value: 'archive:finalize', label: '正式入库', description: '将同一令牌上传的校验文件移入正式归档' },
  { value: 'citations:export', label: '导出引用', description: '读取并导出 BibTeX' },
]

function openTokenDialog() {
  if (tokensUnavailable.value) return
  tokenForm.value = {
    name: '',
    expiresInDays: 90,
    scopes: [...defaultTokenScopes],
  }
  tokenError.value = ''
  tokenCopied.value = false
  createdToken.value = null
  tokenDialogOpen.value = true
}

function closeTokenDialog() {
  if (tokenCreating.value || createdToken.value) return
  if (
    tokenDialogHasUnsavedChanges.value
    && !window.confirm('访问令牌草稿尚未创建，确定关闭吗？')
  ) return
  tokenDialogOpen.value = false
  createdToken.value = null
}

function acknowledgeCreatedToken() {
  createdToken.value = null
  tokenDialogOpen.value = false
}

function toggleScope(scope: AgentScope) {
  const scopes = new Set(tokenForm.value.scopes)
  if (scopes.has(scope)) {
    scopes.delete(scope)
    if (scope === 'files:upload') scopes.delete('archive:finalize')
  } else {
    scopes.add(scope)
    if (scope === 'archive:finalize') scopes.add('files:upload')
  }
  tokenForm.value.scopes = scopeOptions
    .map((option) => option.value)
    .filter((value) => scopes.has(value))
  tokenError.value = ''
}

async function submitToken() {
  if (tokensUnavailable.value || tokenCreating.value || !tokenFormValid.value) return
  tokensController?.abort()
  tokensController = undefined
  tokensLoading.value = false
  tokenCreating.value = true
  tokenError.value = ''
  try {
    const token = await createAccessToken({
      name: tokenForm.value.name.trim(),
      scopes: tokenForm.value.scopes,
      expires_in_days: tokenForm.value.expiresInDays,
    })
    createdToken.value = token
    accessTokens.value = [withoutPlaintextToken(token), ...accessTokens.value]
  } catch (reason) {
    tokenError.value = reason instanceof Error ? reason.message : '无法创建访问令牌'
  } finally {
    tokenCreating.value = false
  }
}

async function copyCreatedToken() {
  if (!createdToken.value) return
  try {
    await copyText(createdToken.value.token)
    tokenCopied.value = true
  } catch {
    tokenError.value = '浏览器无法写入剪贴板，请手动复制令牌。'
  }
}

async function confirmRevokeToken() {
  if (!revokeTarget.value || tokensUnavailable.value) return
  tokensController?.abort()
  tokensController = undefined
  tokensLoading.value = false
  revoking.value = true
  revokeError.value = ''
  try {
    const revoked = await revokeAccessToken(revokeTarget.value.id)
    accessTokens.value = accessTokens.value.map((token) => token.id === revoked.id ? revoked : token)
    revokeTarget.value = null
  } catch (reason) {
    revokeError.value = reason instanceof Error ? reason.message : '无法撤销访问令牌'
  } finally {
    revoking.value = false
  }
}

function openRevokeDialog(token: AccessTokenSummary) {
  revokeError.value = ''
  revokeTarget.value = token
}

function closeRevokeDialog() {
  if (revoking.value) return
  revokeTarget.value = null
  revokeError.value = ''
}

function tokenStatus(token: AccessTokenSummary): 'active' | 'expired' | 'revoked' {
  if (token.revoked_at) return 'revoked'
  return new Date(token.expires_at).getTime() <= Date.now() ? 'expired' : 'active'
}

function formatTokenDate(value: string | null) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function backupScheduleLabel(intervalSeconds: number) {
  if (intervalSeconds <= 0) return '自动备份已关闭'
  if (intervalSeconds % 86400 === 0) return `每 ${intervalSeconds / 86400} 天自动备份`
  if (intervalSeconds % 3600 === 0) return `每 ${intervalSeconds / 3600} 小时自动备份`
  return `每 ${intervalSeconds} 秒自动备份`
}

function openCreate() {
  if (accountsUnavailable.value || issuingInvitationUsername.value) return
  form.value = { username: '' }
  createError.value = ''
  accountInvitation.value = null
  accountInvitationCopied.value = false
  createOpen.value = true
}

function closeCreate() {
  if (creating.value) return
  if (
    accountInvitation.value
    && !window.confirm('当前邀请链接关闭后将不再显示，如未保存需要重新生成。确定关闭吗？')
  ) return
  if (
    !accountInvitation.value
    && form.value.username.trim()
    && !window.confirm('管理员账号草稿尚未提交，确定关闭吗？')
  ) return
  createOpen.value = false
  accountInvitation.value = null
}

function acknowledgeAccountInvitation() {
  accountInvitation.value = null
  accountInvitationCopied.value = false
  createError.value = ''
  createOpen.value = false
}

function storeInvitation(invitation: AccountInvitationCreated) {
  accountInvitation.value = invitation
  accountInvitationCopied.value = false
  const existing = accounts.value.some((item) => item.id === invitation.account.id)
  accounts.value = (existing
    ? accounts.value.map((item) => item.id === invitation.account.id ? invitation.account : item)
    : [...accounts.value, invitation.account]
  ).sort((left, right) => left.username.localeCompare(right.username))
}

async function createAccount() {
  if (!accountFormValid.value || accountsUnavailable.value) return
  accountsController?.abort()
  accountsController = undefined
  accountsLoading.value = false
  creating.value = true
  createError.value = ''
  try {
    storeInvitation(await createAdminAccountInvitation(form.value.username.trim().toLowerCase()))
  } catch (reason) {
    createError.value = reason instanceof Error ? reason.message : '无法创建注册链接'
  } finally {
    creating.value = false
  }
}

async function issueAccountInvitation(account: AccountSummary) {
  if (
    accountsUnavailable.value
    || updatingUsernames.value.has(account.username)
    || issuingInvitationUsername.value !== null
    || !canManageAccount(account)
  ) return
  accountsController?.abort()
  accountsController = undefined
  accountsLoading.value = false
  issuingInvitationUsername.value = account.username
  updatingUsernames.value = new Set(updatingUsernames.value).add(account.username)
  accountActionErrors.value = { ...accountActionErrors.value, [account.username]: '' }
  try {
    const purpose = account.is_registered ? 'recovery' : 'registration'
    storeInvitation(await renewAdminAccountInvitation(account.username, purpose))
    createOpen.value = true
  } catch (reason) {
    accountActionErrors.value = {
      ...accountActionErrors.value,
      [account.username]: reason instanceof Error ? reason.message : '无法生成邀请链接',
    }
  } finally {
    const next = new Set(updatingUsernames.value)
    next.delete(account.username)
    updatingUsernames.value = next
    if (issuingInvitationUsername.value === account.username) {
      issuingInvitationUsername.value = null
    }
  }
}

function accountInvitationActionLabel(account: AccountSummary) {
  const identity = account.name || account.username
  if (issuingInvitationUsername.value === account.username) return `正在生成邀请链接：${identity}`
  if (issuingInvitationUsername.value) return `请等待当前邀请链接生成完成：${identity}`
  const action = account.is_registered ? '生成密码恢复链接' : '重新生成注册链接'
  return `${action}：${identity}`
}

function accountInvitationActionTitle(account: AccountSummary) {
  if (issuingInvitationUsername.value === account.username) return '正在生成邀请链接'
  if (issuingInvitationUsername.value) return '请等待当前邀请链接生成完成'
  if (!canManageAccount(account)) return '只有实例所有者可以管理实例所有者账号'
  if (!account.is_active) return '停用账号不能生成邀请链接'
  return account.is_registered ? '生成密码恢复链接' : '重新生成注册链接'
}

async function copyAccountInvitation() {
  if (!accountInvitationUrl.value) return
  try {
    await copyText(accountInvitationUrl.value)
    accountInvitationCopied.value = true
  } catch {
    createError.value = '浏览器无法写入剪贴板，请手动复制链接。'
  }
}

async function toggleAccount(account: AccountSummary) {
  if (
    accountsUnavailable.value
    || account.username === currentUsername.value
    || updatingUsernames.value.has(account.username)
    || !canManageAccount(account)
  ) return
  accountsController?.abort()
  accountsController = undefined
  accountsLoading.value = false
  updatingUsernames.value = new Set(updatingUsernames.value).add(account.username)
  const nextErrors = { ...accountActionErrors.value }
  delete nextErrors[account.username]
  accountActionErrors.value = nextErrors
  try {
    const updated = await updateAdminAccount(account.username, { is_active: !account.is_active })
    accounts.value = accounts.value.map((item) => item.username === updated.username ? updated : item)
  } catch (reason) {
    accountActionErrors.value = {
      ...accountActionErrors.value,
      [account.username]: reason instanceof Error ? reason.message : '无法更新账号状态',
    }
  } finally {
    const nextUpdating = new Set(updatingUsernames.value)
    nextUpdating.delete(account.username)
    updatingUsernames.value = nextUpdating
  }
}

async function saveBranding() {
  if (brandingUnavailable.value || brandingUpdating.value || !brandingDirty.value || !brandingFormValid.value) return
  brandingController?.abort()
  brandingController = undefined
  brandingLoading.value = false
  brandingOperation.value = 'saving'
  brandingError.value = ''
  brandingMessage.value = ''
  try {
    const updated = await updateInstanceBranding(brandingForm.value, savedBranding.value.revision)
    applyBrandingSnapshot(updated, true)
    brandingMessage.value = '品牌设置已应用'
  } catch (reason) {
    brandingError.value = reason instanceof Error ? reason.message : '无法保存品牌设置'
  } finally {
    brandingOperation.value = null
  }
}

function selectLogo(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file || brandingUpdating.value || brandingUnavailable.value) return
  brandingError.value = ''
  brandingMessage.value = ''

  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
    brandingError.value = '仅支持 PNG、JPEG 或 WebP 图片。'
    if (logoInput.value) logoInput.value.value = ''
    return
  }
  if (file.size > 1_000_000) {
    brandingError.value = 'Logo 文件不能超过 1 MB。'
    if (logoInput.value) logoInput.value.value = ''
    return
  }

  clearPendingLogo()
  pendingLogoFile.value = file
  pendingLogoUrl.value = URL.createObjectURL(file)
  brandingMessage.value = '已选择新 Logo，请确认预览后应用'
}

function stageDefaultLogo() {
  if (brandingUpdating.value || brandingUnavailable.value) return
  clearPendingLogo()
  brandingError.value = ''
  brandingMessage.value = ''
  if (savedBranding.value.logo_url) {
    pendingLogoRemoval.value = true
    brandingMessage.value = '已预览默认标志，应用后生效'
  }
}

async function applyLogoChange() {
  if (brandingUnavailable.value || brandingUpdating.value || !logoDirty.value) return
  brandingController?.abort()
  brandingController = undefined
  brandingLoading.value = false
  brandingOperation.value = 'updating-logo'
  brandingError.value = ''
  brandingMessage.value = ''
  try {
    const updated = pendingLogoFile.value
      ? await uploadInstanceLogo(pendingLogoFile.value, savedBranding.value.revision)
      : await removeInstanceLogo(savedBranding.value.revision)
    applyBrandingSnapshot(updated, false)
    clearPendingLogo()
    brandingMessage.value = 'Logo 已应用'
  } catch (reason) {
    brandingError.value = reason instanceof Error ? reason.message : '无法更新 Logo'
  } finally {
    brandingOperation.value = null
  }
}

function cancelLogoChange() {
  clearPendingLogo()
  brandingError.value = ''
  brandingMessage.value = ''
}

function systemUpdateStateLabel(status: SystemUpdateStatus | null) {
  if (!status) return '读取中'
  if (status.backup_in_progress) return '正在备份'
  const labels: Record<SystemUpdateStatus['state'], string> = {
    unavailable: '未配置',
    idle: '已是最新',
    available: '有可用更新',
    checking: '正在检查',
    recovering: '正在恢复',
    backing_up: '正在备份',
    pulling: '正在拉取',
    building: '正在构建',
    restarting: '正在重启',
    verifying: '正在验证',
    succeeded: '更新成功',
    failed: '更新失败',
  }
  return labels[status.state] || status.state
}

function updatePollDelay() {
  return Math.min(
    UPDATE_POLL_INTERVAL_MS * 2 ** updatePollFailureCount,
    UPDATE_POLL_MAX_INTERVAL_MS,
  )
}

function stopUpdatePolling() {
  if (updatePollTimer) {
    clearTimeout(updatePollTimer)
    updatePollTimer = null
  }
  updatePollFailureCount = 0
}

function startUpdatePolling() {
  if (settingsDisposed || updatePollTimer) return
  updatePollTimer = setTimeout(async () => {
    updatePollTimer = null
    await loadSystemUpdate(true)
    if (!settingsDisposed && systemUpdateBusy.value) startUpdatePolling()
  }, updatePollDelay())
}

async function checkForSystemUpdate() {
  if (systemUpdateBusy.value || systemUpdateLoading.value) return
  stopUpdatePolling()
  systemUpdateActionController?.abort()
  const controller = new AbortController()
  systemUpdateActionController = controller
  systemUpdateController?.abort()
  systemUpdateController = undefined
  systemUpdateLoading.value = true
  systemUpdateError.value = ''
  try {
    const result = await checkSystemUpdate(controller.signal)
    if (systemUpdateActionController !== controller) return
    systemUpdate.value = result
    if (systemUpdateBusy.value) startUpdatePolling()
  } catch (reason) {
    if (systemUpdateActionController !== controller) return
    if (reason instanceof DOMException && reason.name === 'AbortError') return
    const message = reason instanceof Error ? reason.message : '检查更新失败'
    await loadSystemUpdate(true)
    const operationAlreadyRunning = reason instanceof ApiError
      && reason.code === 'update_operation_running'
    if (systemUpdateBusy.value || operationAlreadyRunning) {
      systemUpdateError.value = ''
      startUpdatePolling()
    } else {
      systemUpdateError.value = message
    }
  } finally {
    if (systemUpdateActionController === controller) {
      systemUpdateActionController = undefined
      systemUpdateLoading.value = false
    }
  }
}

function openUpdateDialog() {
  if (systemUpdateUnavailable.value || !isInstanceOwner.value) return
  updatePassword.value = ''
  updateSubmitError.value = ''
  updateDialogOpen.value = true
}

function closeUpdateDialog() {
  if (updateSubmitting.value) return
  updateDialogOpen.value = false
  updatePassword.value = ''
  updateSubmitError.value = ''
}

async function submitSystemUpdate() {
  if (!updatePassword.value || updateSubmitting.value) return
  if (systemUpdateUnavailable.value) {
    updateSubmitError.value = '更新状态已失效，请关闭窗口并重新检查更新'
    return
  }
  stopUpdatePolling()
  systemUpdateController?.abort()
  systemUpdateController = undefined
  updateSubmitting.value = true
  updateSubmitError.value = ''
  try {
    const targetCommit = systemUpdate.value?.latest_commit
    if (!targetCommit) throw new Error('缺少已检查的目标 Commit，请重新检查更新')
    systemUpdate.value = await applySystemUpdate(updatePassword.value, targetCommit)
    updateDialogOpen.value = false
    updatePassword.value = ''
    startUpdatePolling()
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : '无法启动系统更新'
    await loadSystemUpdate(true)
    if (systemUpdateBusy.value) {
      updateDialogOpen.value = false
      updatePassword.value = ''
      startUpdatePolling()
    } else {
      updateSubmitError.value = message
    }
  } finally {
    updateSubmitting.value = false
  }
}

function reloadApplication() {
  window.location.reload()
}

onBeforeRouteLeave(() => {
  if (!settingsHasUnsavedChanges.value) return true
  if (createdToken.value) {
    return window.confirm('新访问令牌只显示一次，离开后将无法再次查看。确定离开吗？')
  }
  if (accountInvitation.value) {
    return window.confirm('当前邀请链接离开后将不再显示，如未保存需要重新生成。确定离开吗？')
  }
  return window.confirm('设置页仍有尚未保存的更改，确定离开吗？')
})

onMounted(() => {
  window.addEventListener('beforeunload', preventUnsavedSettingsExit)
  void load()
})
onBeforeUnmount(() => {
  settingsDisposed = true
  accountsController?.abort()
  accountsController = undefined
  tokensController?.abort()
  tokensController = undefined
  brandingController?.abort()
  brandingController = undefined
  systemUpdateActionController?.abort()
  systemUpdateActionController = undefined
  stopUpdatePolling()
  systemUpdateController?.abort()
  systemUpdateController = undefined
  window.removeEventListener('beforeunload', preventUnsavedSettingsExit)
  clearPendingLogo()
})
</script>

<template>
  <div class="page settings-page">
    <header class="page-heading settings-heading">
      <div>
        <p class="eyebrow">{{ pageEyebrow('ADMINISTRATION') }}</p>
        <h1>系统设置</h1>
        <p>配置当前 DataManager 实例的品牌、访问权限与系统版本。</p>
      </div>
      <div class="settings-actions"><button class="button button--outline" :disabled="settingsMutationInProgress" @click="refreshSettings"><RefreshCw :size="16" />刷新</button><button class="button button--primary" :disabled="accountsUnavailable || creating || issuingInvitationUsername !== null" @click="openCreate"><Plus :size="16" />新增管理员</button></div>
    </header>

    <nav class="settings-section-nav" aria-label="设置分区">
      <a href="#settings-branding"><Palette :size="15" /><span>品牌与外观</span></a>
      <a href="#settings-agent-access"><Bot :size="15" /><span>AI 访问令牌</span></a>
      <a href="#settings-accounts"><UserRound :size="15" /><span>管理员账号</span></a>
      <a href="#settings-system-update"><ServerCog :size="15" /><span>系统与更新</span></a>
    </nav>

    <section id="settings-branding" class="branding-panel settings-anchor" aria-labelledby="branding-title">
      <header>
        <span class="settings-section-icon"><Palette :size="18" /></span>
        <div><h2 id="branding-title">品牌与外观</h2><p>设置名称、组织标语、主题主色和实例 Logo，保存后全站立即生效。</p></div>
        <span>INSTANCE IDENTITY</span>
      </header>
      <div class="branding-workspace">
        <form class="branding-form" @submit.prevent="saveBranding">
          <div class="branding-fields branding-fields--names">
            <label>产品名称<input v-model="brandingForm.product_name" :disabled="brandingUpdating || brandingUnavailable" required maxlength="80" placeholder="例如：SAGE" /></label>
            <label>产品副标题<input v-model="brandingForm.product_subtitle" :disabled="brandingUpdating || brandingUnavailable" required maxlength="120" placeholder="例如：RESEARCH ARCHIVE" /></label>
            <label>组织名称<input v-model="brandingForm.organization_name" :disabled="brandingUpdating || brandingUnavailable" required maxlength="120" placeholder="例如：SAGE Lab" /></label>
          </div>
          <div class="branding-fields branding-fields--slogans">
            <label>主标语<input v-model="brandingForm.slogan" :disabled="brandingUpdating || brandingUnavailable" required maxlength="160" placeholder="例如：科学 · 数据 · 成长 · 卓越" /></label>
            <label>辅助标语<input v-model="brandingForm.slogan_secondary" :disabled="brandingUpdating || brandingUnavailable" required maxlength="160" placeholder="例如：Science · Archive · Growth · Excellence" /></label>
          </div>
          <div class="branding-controls">
            <label class="color-field">
              品牌主色
              <span><input v-model="brandingForm.primary_color" type="color" :disabled="brandingUpdating || brandingUnavailable" aria-label="选择品牌主色" /><input v-model="brandingForm.primary_color" :disabled="brandingUpdating || brandingUnavailable" required maxlength="7" pattern="#[0-9A-Fa-f]{6}" aria-label="品牌主色色值" /></span>
              <small :class="{ 'color-contrast--invalid': brandingContrastRatio < 4.5 }">与白色文字对比度 {{ brandingContrastRatio.toFixed(2) }}:1 · 最低 4.5:1</small>
            </label>
            <div class="logo-control">
              <span>实例 Logo</span>
              <div>
                <input ref="logoInput" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" aria-label="选择实例 Logo 图片" :disabled="brandingUpdating || brandingUnavailable" @change="selectLogo" />
                <button class="button button--outline" type="button" :disabled="brandingUpdating || brandingUnavailable" @click="logoInput?.click()"><ImageUp :size="15" />选择图片</button>
                <button v-if="savedBranding.logo_url && !pendingLogoRemoval" class="button button--quiet" type="button" :disabled="brandingUpdating || brandingUnavailable" @click="stageDefaultLogo"><RotateCcw :size="15" />恢复默认</button>
              </div>
              <small>PNG、JPEG 或 WebP，最大 1 MB；选择后先在右侧预览</small>
              <div v-if="logoDirty" class="logo-pending-actions">
                <button class="button button--primary" type="button" :disabled="brandingUpdating || brandingUnavailable" @click="applyLogoChange"><Save :size="15" />{{ logoUpdating ? '正在应用' : '应用 Logo' }}</button>
                <button class="button button--quiet" type="button" :disabled="brandingUpdating || brandingUnavailable" @click="cancelLogoChange">取消</button>
              </div>
            </div>
          </div>
          <div class="branding-feedback">
            <p v-if="brandingLoadError" class="settings-error branding-load-error" role="alert">{{ brandingLoadError }} <button type="button" :disabled="brandingLoading" @click="retryBrandingSettings">重试</button></p>
            <p v-else-if="brandingError" class="settings-error" role="alert">{{ brandingError }}</p>
            <p v-else-if="brandingMessage" class="settings-success" role="status"><Check :size="14" />{{ brandingMessage }}</p>
            <button v-if="brandingHasUnsavedChanges" class="button button--quiet" type="button" :disabled="brandingUpdating || brandingUnavailable" @click="resetBrandingDraft"><RotateCcw :size="15" />撤销更改</button>
            <button class="button button--primary" :disabled="brandingUpdating || brandingUnavailable || !brandingDirty || !brandingFormValid" type="submit"><Save :size="16" />{{ brandingSaving ? '正在保存' : '保存文字与主色' }}</button>
          </div>
        </form>
        <aside class="brand-preview" :style="{ '--preview-color': brandingForm.primary_color }" aria-label="品牌预览">
          <span>SIDEBAR PREVIEW</span>
          <div class="brand-preview-lockup">
            <img v-if="previewLogoUrl" :src="previewLogoUrl" alt="" />
            <span v-else class="preview-mark"><i></i><i></i><i></i></span>
            <div><strong>{{ brandingForm.product_name || 'DataManager' }}</strong><small>{{ brandingForm.product_subtitle }}</small></div>
          </div>
          <div class="brand-preview-signature"><strong>{{ brandingForm.organization_name }}</strong><p>{{ brandingForm.slogan }}</p><small>{{ brandingForm.slogan_secondary }}</small></div>
        </aside>
      </div>
    </section>

    <section id="settings-agent-access" class="agent-access-panel settings-anchor" aria-labelledby="agent-access-title">
      <header class="agent-access-header">
        <span class="settings-section-icon"><Bot :size="18" /></span>
        <div>
          <h2 id="agent-access-title">AI 访问令牌</h2>
          <p>为可信 AI 客户端创建独立凭证，并按任务授予最小权限。</p>
        </div>
        <div class="agent-access-actions">
          <a class="button button--outline" href="/agent.md" target="_blank" rel="noopener">
            <ExternalLink :size="15" />Agent 说明
          </a>
          <button class="button button--primary" type="button" :disabled="tokensUnavailable || tokenCreating" @click="openTokenDialog">
            <KeyRound :size="15" />新建令牌
          </button>
        </div>
      </header>
      <div v-if="tokensLoading" class="settings-inline-loading" role="status" aria-live="polite"><span class="tiny-spinner"></span>正在读取访问令牌…</div>
      <p v-else-if="tokenLoadError" class="agent-access-error settings-error" role="alert">{{ tokenLoadError }} <button type="button" :disabled="tokensLoading" @click="loadTokens">重试</button></p>
      <p v-if="tokenError && !tokenDialogOpen" class="agent-access-error settings-error" role="alert">{{ tokenError }}</p>
      <div v-if="!tokensLoading && !tokenLoadError && activeTokens.length" class="token-list">
        <article v-for="token in activeTokens" :key="token.id" class="token-row">
          <span class="token-key-icon"><KeyRound :size="16" /></span>
          <div class="token-identity">
            <strong>{{ token.name }}</strong>
            <code>{{ token.token_prefix }}…</code>
          </div>
          <div class="token-scopes" aria-label="令牌权限">
            <span v-for="scope in token.scopes" :key="scope">{{ scopeOptions.find((option) => option.value === scope)?.label || scope }}</span>
          </div>
          <div class="token-dates">
            <small>到期 {{ formatTokenDate(token.expires_at) }}</small>
            <small>{{ token.last_used_at ? `最近使用 ${formatTokenDate(token.last_used_at)}` : '尚未使用' }}</small>
          </div>
          <button class="token-revoke" type="button" :aria-label="`撤销令牌：${token.name}`" :title="`撤销令牌：${token.name}`" @click="openRevokeDialog(token)">
            <Trash2 :size="16" />
          </button>
        </article>
      </div>
      <div v-else-if="!tokensLoading && !tokenLoadError" class="token-empty" :class="{ 'token-empty--compact': historicalTokens.length }">
        <KeyRound :size="23" />
        <div><strong>没有有效的 AI 访问令牌</strong><p>创建后，AI 可按授权范围调用专用接口。</p></div>
      </div>
      <div v-if="!tokensLoading && !tokenLoadError && historicalTokens.length" class="token-history">
        <button type="button" :aria-expanded="tokenHistoryOpen" @click="tokenHistoryOpen = !tokenHistoryOpen">
          <span>历史令牌</span><small>{{ historicalTokens.length }} 个已失效</small>
        </button>
        <div v-if="tokenHistoryOpen" class="token-list token-list--history">
          <article v-for="token in historicalTokens" :key="token.id" class="token-row token-row--inactive">
            <span class="token-key-icon"><KeyRound :size="16" /></span>
            <div class="token-identity"><strong>{{ token.name }}</strong><code>{{ token.token_prefix }}…</code></div>
            <div class="token-scopes" aria-label="令牌权限"><span v-for="scope in token.scopes" :key="scope">{{ scopeOptions.find((option) => option.value === scope)?.label || scope }}</span></div>
            <div class="token-dates"><small>到期 {{ formatTokenDate(token.expires_at) }}</small><small v-if="token.revoked_at">撤销 {{ formatTokenDate(token.revoked_at) }}</small></div>
            <span class="token-revoked">{{ tokenStatus(token) === 'revoked' ? '已撤销' : '已过期' }}</span>
          </article>
        </div>
      </div>
    </section>

    <div v-if="accountsLoading" id="settings-accounts" class="state-panel settings-anchor" role="status" aria-live="polite"><span class="loader-ring"></span><p>正在读取账号设置…</p></div>
    <div v-else-if="accountsError && !accounts.length" id="settings-accounts" class="state-panel state-panel--error settings-anchor" role="alert"><CircleAlert :size="28" /><strong>无法读取账号</strong><p>{{ accountsError }}</p><button class="button button--outline" @click="loadAccounts">重试</button></div>
    <template v-else>
      <section id="settings-accounts" class="settings-summary accounts-summary settings-anchor">
        <div><ShieldCheck :size="19" /><span><strong>{{ activeCount }}</strong><small>启用管理员</small></span></div>
        <div><UserRound :size="19" /><span><strong>{{ accounts.length }}</strong><small>管理员账号</small></span></div>
        <p>账号名同时用于生成 SCP 上传命令中的服务器用户名。</p>
      </section>
      <p v-if="accountsError" class="accounts-load-error agent-access-error settings-error" role="alert">{{ accountsError }} <button type="button" :disabled="accountsLoading" @click="loadAccounts">重试</button></p>
      <section class="accounts-panel" aria-labelledby="accounts-title">
        <header><div><h2 id="accounts-title">管理员账号</h2><p>新增账号时只预留 SSH 用户名；姓名、邮箱和密码由受邀者本人填写。</p></div><span>{{ accounts.length }} accounts</span></header>
        <div class="accounts-table">
          <div v-for="account in accounts" :key="account.id" class="account-row" :class="{ 'account-row--inactive': !account.is_active }">
            <span class="account-avatar">{{ account.username.slice(0, 1).toUpperCase() }}</span>
            <div class="account-copy"><strong>{{ account.name || '等待本人注册' }}</strong><small>{{ account.username }}<template v-if="account.email"> · {{ account.email }}</template></small></div>
            <span class="account-role">{{ account.is_instance_owner ? '实例所有者' : account.role }}</span>
            <span class="account-status" :class="{ 'account-status--pending': !account.is_registered, 'account-status--inactive': !account.is_active }">{{ !account.is_registered ? '待注册' : account.is_active ? '已启用' : '已停用' }}</span>
            <button class="button button--outline account-toggle" :disabled="accountsUnavailable || updatingUsernames.has(account.username) || account.username === currentUsername || !account.is_registered || !canManageAccount(account)" :aria-label="`${updatingUsernames.has(account.username) ? '正在处理' : account.is_active ? '停用管理员' : '启用管理员'}：${account.name || account.username}`" :title="!canManageAccount(account) ? '只有实例所有者可以管理实例所有者账号' : !account.is_registered ? '待注册账号不能切换状态' : account.username === currentUsername ? '当前登录账号不可自行停用' : ''" @click="toggleAccount(account)"><UserRoundX v-if="account.is_active" :size="15" /><Check v-else :size="15" />{{ updatingUsernames.has(account.username) ? '处理中' : account.is_active ? '停用' : '启用' }}</button>
            <button class="account-invitation-button" type="button" :disabled="accountsUnavailable || issuingInvitationUsername !== null || updatingUsernames.has(account.username) || !account.is_active || !canManageAccount(account)" :aria-label="accountInvitationActionLabel(account)" :title="accountInvitationActionTitle(account)" @click="issueAccountInvitation(account)"><RefreshCw v-if="issuingInvitationUsername === account.username" :size="16" class="spin-icon" /><Link2 v-else :size="16" /></button>
            <p v-if="accountActionErrors[account.username]" class="account-row-error" role="alert">{{ accountActionErrors[account.username] }}</p>
          </div>
        </div>
      </section>
    </template>

    <section id="settings-system-update" class="system-update-panel settings-anchor" aria-labelledby="system-update-title">
      <header>
        <span class="settings-section-icon"><ServerCog :size="18" /></span>
        <div><h2 id="system-update-title">系统与更新</h2><p>从固定的 origin/main 拉取代码，备份数据库后重新构建应用容器。</p></div>
        <div class="system-update-actions">
          <button class="button button--outline" type="button" :disabled="systemUpdateLoading || systemUpdateBusy || !systemUpdate?.enabled" @click="checkForSystemUpdate"><RefreshCw :size="15" :class="{ 'spin-icon': systemUpdateLoading || systemUpdate?.state === 'checking' }" />{{ systemUpdate?.state === 'checking' ? '正在检查' : '检查更新' }}</button>
          <button v-if="isInstanceOwner && systemUpdate?.update_available" class="button button--primary" type="button" :disabled="systemUpdateUnavailable" :title="systemUpdateError ? '更新状态不可用，请重试后再更新' : systemUpdate.checked_at ? '' : '请先检查更新以锁定目标 Commit'" @click="openUpdateDialog"><GitMerge :size="15" />立即更新</button>
        </div>
      </header>
      <div v-if="systemUpdateLoading && !systemUpdate" class="settings-inline-loading" role="status"><span class="tiny-spinner"></span>正在读取系统版本…</div>
      <div v-else-if="systemUpdate" class="system-update-content">
        <div class="system-version-grid">
          <div><small>当前 Commit</small><strong><code>{{ currentCommitShort }}</code></strong></div>
          <span class="system-version-arrow">→</span>
          <div><small>origin/main</small><strong><code>{{ latestCommitShort }}</code></strong></div>
          <span class="system-update-status" :class="systemUpdate.backup_in_progress ? 'system-update-status--backing_up' : 'system-update-status--' + systemUpdate.state">{{ systemUpdateStateLabel(systemUpdate) }}</span>
        </div>
        <div v-if="systemUpdateBusy || systemUpdate.state === 'succeeded'" class="system-update-progress" role="progressbar" :aria-label="systemUpdate.backup_in_progress ? '数据库备份进度' : '系统更新进度'" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="systemUpdateProgress">
          <span :style="{ width: `${systemUpdateProgress}%` }"></span>
        </div>
        <p class="system-update-message">{{ systemUpdate.message }}</p>
        <p v-if="systemUpdateError || systemUpdate.error" class="settings-error system-update-error" role="alert">{{ systemUpdateError || systemUpdate.error }} <button v-if="systemUpdateError" type="button" :disabled="systemUpdateLoading" @click="loadSystemUpdate()">重试</button></p>
        <div v-if="!systemUpdate.enabled" class="system-update-unavailable">
          <CircleAlert :size="17" />
          <p><strong>需要先在服务器安装更新代理</strong><code>sudo bash deploy/install-updater.sh</code></p>
        </div>
        <ol v-if="systemUpdate.commits.length" class="system-update-commits">
          <li v-for="commit in systemUpdate.commits.slice(0, 5)" :key="commit.sha">
            <code>{{ commit.short_sha }}</code><span>{{ commit.subject }}</span><small>{{ commit.author }}</small>
          </li>
        </ol>
        <div v-if="systemUpdate.logs.length && (systemUpdateBusy || systemUpdate.state === 'failed')" class="system-update-log">
          <code v-for="line in systemUpdate.logs.slice(-8)" :key="line">{{ line }}</code>
        </div>
        <div v-if="systemUpdate.enabled" class="system-backup-status">
          <div><strong>PostgreSQL 自动备份</strong><span>{{ systemUpdate.backup_in_progress ? '正在创建并校验备份' : backupScheduleLabel(systemUpdate.scheduled_backup_interval_seconds) }}</span></div>
          <span v-if="systemUpdate.last_backup_at">最近完成：{{ formatTokenDate(systemUpdate.last_backup_at) }}<code>{{ systemUpdate.last_backup_path }}</code></span>
          <span v-if="systemUpdate.next_backup_at">下次计划：{{ formatTokenDate(systemUpdate.next_backup_at) }}</span>
          <p v-if="systemUpdate.last_backup_error" class="settings-error" role="alert">最近备份失败：{{ systemUpdate.last_backup_error }}</p>
        </div>
        <div class="system-update-notes">
          <span v-if="systemUpdate.backup_path">数据库备份：<code>{{ systemUpdate.backup_path }}</code></span>
          <span v-if="!isInstanceOwner">只有实例所有者可以执行更新。</span>
          <span v-if="systemUpdate.installer_restart_required">更新代理配置有变化，请在服务器重新运行安装脚本。</span>
          <button v-if="systemUpdate.state === 'succeeded'" class="button button--outline" type="button" @click="reloadApplication"><RefreshCw :size="14" />刷新到新版本</button>
        </div>
      </div>
      <p v-else-if="systemUpdateError" class="settings-error system-update-load-error" role="alert">{{ systemUpdateError }} <button type="button" :disabled="systemUpdateLoading" @click="loadSystemUpdate()">重试</button></p>
    </section>

    <div v-if="updateDialogOpen" class="settings-backdrop" @click.self="closeUpdateDialog">
      <form ref="updateDialog" class="settings-dialog system-update-dialog" role="alertdialog" aria-modal="true" aria-labelledby="system-update-confirm-title" @submit.prevent="submitSystemUpdate">
        <button class="settings-close" type="button" :disabled="updateSubmitting" aria-label="关闭" @click="closeUpdateDialog"><X :size="18" /></button>
        <span class="revoke-dialog-icon"><GitMerge :size="20" /></span>
        <p class="eyebrow">FAST-FORWARD UPDATE</p>
        <h2 id="system-update-confirm-title">更新到 {{ latestCommitShort }}</h2>
        <p>系统将备份 PostgreSQL、拉取 {{ systemUpdate?.behind_count }} 个提交并重新构建前后端。期间网页会短暂无法访问。</p>
        <div class="system-update-confirm-version"><code>{{ currentCommitShort }}</code><span>→</span><code>{{ latestCommitShort }}</code></div>
        <div class="token-once-warning">
          <CircleAlert :size="18" />
          <p><strong>更新只接受 origin/main 的 fast-forward</strong><span>发现未提交文件、本地额外提交、构建错误或健康检查失败时会停止并尝试恢复旧应用。</span></p>
        </div>
        <label>确认当前账号密码<input v-model="updatePassword" required autofocus type="password" autocomplete="current-password" maxlength="256" placeholder="输入密码后开始更新" /></label>
        <p v-if="updateSubmitError" class="settings-error" role="alert">{{ updateSubmitError }}</p>
        <footer><button class="button button--outline" type="button" :disabled="updateSubmitting" @click="closeUpdateDialog">取消</button><button class="button button--danger" type="submit" :disabled="updateSubmitting || !updatePassword || systemUpdateUnavailable"><GitMerge :size="16" />{{ updateSubmitting ? '正在启动' : '备份并更新' }}</button></footer>
      </form>
    </div>

    <div v-if="createOpen" class="settings-backdrop" @click.self="closeCreate">
      <form ref="createDialog" class="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="create-account-title" @submit.prevent="createAccount">
        <button class="settings-close" type="button" :disabled="creating" aria-label="关闭" @click="closeCreate"><X :size="18" /></button>
        <template v-if="accountInvitation">
          <p class="eyebrow">ONE-TIME ACCOUNT LINK</p>
          <h2 id="create-account-title">{{ accountInvitation.purpose === 'registration' ? '注册链接已创建' : '密码恢复链接已创建' }}</h2>
          <div class="token-once-warning" role="alert">
            <ShieldCheck :size="18" />
            <p><strong>请将链接私下发送给 {{ accountInvitation.account.username }}</strong><span>新链接会使此前未使用的链接立即失效；服务端只保存令牌哈希。</span></p>
          </div>
          <div class="created-token-value account-invitation-value">
            <code>{{ accountInvitationUrl }}</code>
            <button type="button" :title="accountInvitationCopied ? '已复制' : '复制链接'" :aria-label="accountInvitationCopied ? '已复制' : '复制链接'" @click="copyAccountInvitation">
              <Check v-if="accountInvitationCopied" :size="17" /><Clipboard v-else :size="17" />
            </button>
          </div>
          <p class="account-invitation-expiry">有效至 {{ formatTokenDate(accountInvitation.expires_at) }}，成功使用一次后立即失效。</p>
          <p v-if="createError" class="settings-error" role="alert">{{ createError }}</p>
          <footer><button class="button button--primary" type="button" @click="acknowledgeAccountInvitation">完成</button></footer>
        </template>
        <template v-else>
          <p class="eyebrow">ADMIN INVITATION</p>
          <h2 id="create-account-title">邀请管理员</h2>
          <p>这里只预留与服务器 SSH 用户一致的账号名。受邀者会通过一次性链接填写姓名、邮箱和自己的密码。</p>
          <label>账号名<input v-model="form.username" required autofocus autocomplete="off" pattern="[a-z0-9]+" maxlength="80" placeholder="例如：newmember" /></label>
          <p v-if="createError" class="settings-error" role="alert">{{ createError }}</p>
          <footer><button class="button button--outline" type="button" :disabled="creating" @click="closeCreate">取消</button><button class="button button--primary" :disabled="creating || !accountFormValid" type="submit"><Link2 :size="16" />{{ creating ? '正在创建' : '生成注册链接' }}</button></footer>
        </template>
      </form>
    </div>

    <div v-if="tokenDialogOpen" class="settings-backdrop" @click.self="closeTokenDialog">
      <section ref="tokenDialog" class="settings-dialog token-dialog" role="dialog" aria-modal="true" aria-labelledby="create-token-title" tabindex="-1">
        <button v-if="!createdToken" class="settings-close" type="button" :disabled="tokenCreating" aria-label="关闭" @click="closeTokenDialog"><X :size="18" /></button>
        <template v-if="createdToken">
          <p class="eyebrow">TOKEN CREATED</p>
          <h2 id="create-token-title">令牌已创建</h2>
          <div class="token-once-warning" role="alert">
            <ShieldCheck :size="18" />
            <p><strong>现在复制并妥善保存</strong><span>出于安全考虑，关闭后无法再次查看完整令牌。</span></p>
          </div>
          <div class="created-token-value">
            <code>{{ createdToken.token }}</code>
            <button type="button" :title="tokenCopied ? '已复制' : '复制令牌'" :aria-label="tokenCopied ? '已复制' : '复制令牌'" @click="copyCreatedToken">
              <Check v-if="tokenCopied" :size="17" /><Clipboard v-else :size="17" />
            </button>
          </div>
          <p v-if="tokenError" class="settings-error" role="alert">{{ tokenError }}</p>
          <footer><button class="button button--primary" type="button" @click="acknowledgeCreatedToken">我已安全保存</button></footer>
        </template>
        <form v-else class="token-create-form" @submit.prevent="submitToken">
          <p class="eyebrow">PERSONAL ACCESS TOKEN</p>
          <h2 id="create-token-title">创建 AI 访问令牌</h2>
          <p>使用用途清晰的名称，并只授予本次自动化需要的权限。</p>
          <label>令牌名称<input v-model="tokenForm.name" required autofocus minlength="2" maxlength="100" autocomplete="off" placeholder="例如：Codex 文献同步" /></label>
          <label>有效期
            <select v-model="tokenForm.expiresInDays">
              <option :value="7">7 天</option><option :value="30">30 天</option><option :value="90">90 天</option><option :value="180">180 天</option><option :value="365">365 天</option>
            </select>
          </label>
          <fieldset class="scope-picker">
            <legend>授权范围</legend>
            <button v-for="option in scopeOptions" :key="option.value" type="button" :class="{ selected: tokenForm.scopes.includes(option.value), sensitive: option.value === 'archive:finalize' }" :aria-pressed="tokenForm.scopes.includes(option.value)" @click="toggleScope(option.value)">
              <span class="scope-check"><Check v-if="tokenForm.scopes.includes(option.value)" :size="14" /></span>
              <span><strong>{{ option.label }}</strong><small>{{ option.description }}</small></span>
              <em v-if="option.value === 'archive:finalize'">高权限</em>
            </button>
          </fieldset>
          <p v-if="tokenError" class="settings-error" role="alert">{{ tokenError }}</p>
          <footer><button class="button button--outline" type="button" :disabled="tokenCreating" @click="closeTokenDialog">取消</button><button class="button button--primary" :disabled="tokenCreating || !tokenFormValid" type="submit"><KeyRound :size="16" />{{ tokenCreating ? '正在创建' : '创建令牌' }}</button></footer>
        </form>
      </section>
    </div>

    <div v-if="revokeTarget" class="settings-backdrop" @click.self="closeRevokeDialog">
      <section ref="revokeDialog" class="settings-dialog revoke-dialog" role="alertdialog" aria-modal="true" aria-labelledby="revoke-token-title" tabindex="-1">
        <span class="revoke-dialog-icon"><CircleAlert :size="20" /></span>
        <h2 id="revoke-token-title">撤销“{{ revokeTarget.name }}”</h2>
        <p>撤销立即生效，使用此令牌的 AI 客户端将无法继续访问。该操作不可恢复。</p>
        <p v-if="revokeError" class="settings-error" role="alert">{{ revokeError }}</p>
        <footer><button class="button button--outline" type="button" :disabled="revoking" @click="closeRevokeDialog">取消</button><button class="button button--danger" type="button" :disabled="revoking || tokensUnavailable" @click="confirmRevokeToken"><Trash2 :size="16" />{{ revoking ? '正在撤销' : '确认撤销' }}</button></footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.settings-section-nav { position: sticky; z-index: 15; top: var(--topbar-height); display: grid; margin: -8px 0 18px; padding: 5px; background: rgba(250,251,248,.94); border: 1px solid var(--line); border-radius: 7px; box-shadow: 0 7px 20px rgba(39,57,44,.06); backdrop-filter: blur(14px); grid-template-columns: repeat(4,minmax(0,1fr)); gap: 4px; }.settings-section-nav a { display: flex; min-width: 0; min-height: 38px; padding: 0 10px; color: #607067; align-items: center; justify-content: center; text-decoration: none; border-radius: 5px; gap: 7px; font-size: 11px; font-weight: 700; white-space: nowrap; }.settings-section-nav a:hover, .settings-section-nav a:focus-visible { color: #284b34; background: var(--sage-soft); outline: none; }.settings-section-nav a:focus-visible { box-shadow: inset 0 0 0 2px var(--sage); }.settings-section-nav svg { flex: 0 0 auto; }.settings-anchor { scroll-margin-top: calc(var(--topbar-height) + 62px); }.settings-anchor:target { border-color: #9fb2a2; box-shadow: 0 0 0 3px rgba(67,105,76,.1); } @media (max-width: 560px) { .settings-section-nav { display: flex; margin-right: -18px; margin-left: -18px; padding: 5px 18px; overflow-x: auto; border-right: 0; border-left: 0; border-radius: 0; scrollbar-width: none; }.settings-section-nav::-webkit-scrollbar { display: none; }.settings-section-nav a { flex: 0 0 auto; min-height: 40px; padding: 0 12px; }.settings-anchor { scroll-margin-top: calc(var(--topbar-height) + 64px); } }
.settings-heading { align-items: center; }.settings-actions { display: flex; gap: 9px; }.branding-panel { margin-bottom: 18px; background: rgba(252,253,249,.94); border: 1px solid var(--line); border-radius: 8px; }.branding-panel > header { display: flex; min-height: 70px; padding: 16px 20px; align-items: center; gap: 12px; border-bottom: 1px solid var(--line); }.branding-panel h2, .accounts-panel h2, .settings-dialog h2 { margin: 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 21px; font-weight: 500; }.branding-panel header p, .accounts-panel header p, .settings-dialog > p { margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.55; }.branding-panel > header > span:last-child { margin-left: auto; color: #89968e; font-size: 9px; letter-spacing: .12em; }.settings-section-icon { display: grid; width: 34px; height: 34px; color: var(--sage); place-items: center; background: var(--sage-soft); border-radius: 5px; }.branding-workspace { display: grid; grid-template-columns: minmax(0, 1fr) 260px; }.branding-form { display: grid; padding: 20px; gap: 15px; border-right: 1px solid var(--line); }.branding-fields { display: grid; gap: 11px; }.branding-fields--names { grid-template-columns: .8fr 1fr 1fr; }.branding-fields--slogans { grid-template-columns: 1fr 1fr; }.branding-form label, .logo-control > span { display: grid; color: #526056; font-size: 11px; font-weight: 700; gap: 6px; }.branding-form input:not([type="color"]), .settings-dialog input { width: 100%; min-width: 0; padding: 9px 10px; color: var(--ink); font: inherit; font-size: 12px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }.branding-controls { display: grid; grid-template-columns: minmax(190px, .65fr) 1fr; gap: 20px; }.color-field > span { display: grid; grid-template-columns: 42px 1fr; }.color-field input[type="color"] { width: 42px; height: 36px; padding: 4px; background: #fff; border: 1px solid var(--line); border-right: 0; border-radius: 5px 0 0 5px; cursor: pointer; }.color-field input[type="text"] { border-radius: 0 5px 5px 0; }.logo-control { display: grid; gap: 6px; }.logo-control > div { display: flex; gap: 7px; }.logo-control small { color: var(--muted); font-size: 9px; }.branding-feedback { display: flex; min-height: 34px; align-items: center; justify-content: flex-end; gap: 12px; }.branding-feedback p { margin: 0 auto 0 0; }.branding-load-error button { padding: 0; color: inherit; text-decoration: underline; background: transparent; border: 0; cursor: pointer; }.settings-success { display: flex; color: var(--sage); align-items: center; font-size: 11px; gap: 5px; }.brand-preview { --preview-color: var(--sage); display: flex; min-width: 0; padding: 20px; flex-direction: column; color: #fff; background: var(--preview-color); }.brand-preview > span { font-size: 8px; font-weight: 800; letter-spacing: .16em; opacity: .65; }.brand-preview-lockup { display: flex; margin-top: 27px; align-items: center; gap: 11px; }.brand-preview-lockup img { width: 40px; height: 40px; object-fit: contain; filter: brightness(0) invert(1); }.brand-preview-lockup > div { display: grid; min-width: 0; gap: 2px; }.brand-preview-lockup strong { overflow: hidden; font-family: "Iowan Old Style", serif; font-size: 22px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }.brand-preview-lockup small { overflow: hidden; font-size: 7px; letter-spacing: .12em; opacity: .75; text-overflow: ellipsis; white-space: nowrap; }.preview-mark { position: relative; width: 34px; height: 40px; flex: 0 0 34px; }.preview-mark i { position: absolute; bottom: 5px; left: 16px; width: 1px; height: 28px; background: #fff; transform-origin: bottom; }.preview-mark i::after { position: absolute; top: 2px; width: 11px; height: 6px; content: ""; background: rgba(255,255,255,.72); border-radius: 8px 1px 8px 1px; transform: rotate(-25deg); }.preview-mark i:first-child { height: 22px; transform: rotate(-32deg); }.preview-mark i:last-child { height: 23px; transform: rotate(34deg); }.brand-preview-signature { margin-top: auto; padding-top: 42px; border-top: 1px solid rgba(255,255,255,.24); }.brand-preview-signature strong { font-family: "Iowan Old Style", "Songti SC", serif; font-size: 15px; }.brand-preview-signature p { margin: 5px 0 3px; font-size: 10px; }.brand-preview-signature small { display: block; overflow: hidden; font-size: 8px; opacity: .7; text-overflow: ellipsis; white-space: nowrap; }.settings-summary { display: flex; margin-bottom: 16px; padding: 16px 20px; align-items: center; gap: 28px; background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 8px; }.settings-summary > div { display: flex; min-width: 120px; align-items: center; gap: 9px; }.settings-summary svg { color: var(--sage); }.settings-summary span { display: grid; gap: 2px; }.settings-summary strong { font-family: "Iowan Old Style", "Songti SC", serif; font-size: 20px; font-weight: 500; }.settings-summary small, .settings-summary p { color: var(--muted); font-size: 11px; }.settings-summary p { margin: 0 0 0 auto; }.accounts-panel { background: rgba(252,253,249,.92); border: 1px solid var(--line); border-radius: 8px; }.accounts-panel > header { display: flex; padding: 19px 20px; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); }.accounts-panel header > span { color: #89968e; font-size: 10px; }.account-row { display: grid; min-height: 68px; padding: 10px 20px; align-items: center; grid-template-columns: 34px minmax(180px, 1fr) 90px 80px auto 34px; gap: 12px; border-bottom: 1px solid #edf0eb; }.account-row:last-child { border-bottom: 0; }.account-row--inactive { opacity: .6; }.account-avatar { display: grid; width: 32px; height: 32px; color: #fff; place-items: center; background: var(--sage); border-radius: 50%; font-size: 12px; font-weight: 800; }.account-copy { display: grid; min-width: 0; gap: 3px; }.account-copy strong { font-size: 12px; }.account-copy small { overflow: hidden; color: #7c887f; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.account-role { color: #56705f; font-size: 10px; font-weight: 700; text-transform: uppercase; }.account-status { color: var(--sage); font-size: 10px; font-weight: 700; }.account-status--inactive { color: #a6633b; }.account-toggle { min-width: 72px; justify-content: center; }.account-invitation-button { display: grid; width: 32px; height: 32px; color: #5d7464; place-items: center; background: #f5f7f3; border: 1px solid var(--line); border-radius: 5px; cursor: pointer; }.account-invitation-button:hover { color: #31563b; background: #edf3ed; }.settings-error { margin: 0 0 13px; color: #a6633b; font-size: 12px; }.settings-backdrop { position: fixed; z-index: 40; inset: 0; display: grid; padding: 20px; place-items: center; background: rgba(23,34,26,.48); }.settings-dialog { position: relative; display: grid; width: min(100%, 500px); padding: 28px; background: #fdfefb; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 20px 50px rgba(24,37,29,.22); gap: 12px; }.settings-dialog label { display: grid; color: #526056; font-size: 12px; font-weight: 700; gap: 6px; }.settings-dialog footer { display: flex; margin-top: 7px; justify-content: flex-end; gap: 9px; }.settings-close { position: absolute; top: 12px; right: 12px; display: grid; width: 31px; height: 31px; color: #68776d; place-items: center; background: transparent; border: 0; border-radius: 50%; cursor: pointer; }.settings-close:hover { background: #eef2ed; } @media (max-width: 900px) { .branding-workspace { grid-template-columns: 1fr; }.branding-form { border-right: 0; }.brand-preview { min-height: 190px; }.brand-preview-signature { padding-top: 22px; } } @media (max-width: 720px) { .settings-heading { align-items: flex-start; }.settings-actions { margin-top: 3px; }.branding-panel > header > span:last-child { display: none; }.branding-fields--names, .branding-fields--slogans, .branding-controls { grid-template-columns: 1fr; }.settings-summary { align-items: flex-start; flex-wrap: wrap; gap: 17px; }.settings-summary p { width: 100%; margin-left: 0; }.account-row { padding: 12px 14px; grid-template-columns: 34px minmax(0, 1fr) 36px auto; }.account-role { display: none; }.account-status { grid-column: 2; }.account-invitation-button { grid-column: 3; grid-row: 1 / 3; }.account-toggle { grid-column: 4; grid-row: 1 / 3; } } @media (max-width: 460px) { .settings-actions { width: 100%; }.settings-actions .button { min-width: 0; flex: 1; white-space: nowrap; }.branding-form, .brand-preview { padding: 16px; }.branding-feedback { align-items: stretch; flex-direction: column; }.branding-feedback .button { width: 100%; justify-content: center; }.logo-control > div { align-items: stretch; flex-direction: column; } }
.account-row-error { margin: -4px 0 4px; color: #a6633b; grid-column: 2 / -1; font-size: 10px; line-height: 1.5; }
.account-status--pending { color: #8b6936; }
.account-invitation-button:disabled { cursor: not-allowed; opacity: .45; }
.account-invitation-value code { max-height: 80px; overflow-y: auto; }
.account-invitation-expiry { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.55; }
.agent-access-panel { margin-bottom: 18px; overflow: hidden; background: rgba(252,253,249,.94); border: 1px solid var(--line); border-radius: 8px; }
.agent-access-header { display: flex; min-height: 70px; padding: 16px 20px; align-items: center; gap: 12px; border-bottom: 1px solid var(--line); }
.agent-access-header h2 { margin: 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 21px; font-weight: 500; }
.agent-access-header p { margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.55; }
.agent-access-actions { display: flex; margin-left: auto; gap: 8px; }
.settings-inline-loading { display: flex; min-height: 88px; align-items: center; justify-content: center; color: var(--muted); gap: 8px; font-size: 11px; }
.agent-access-error { padding: 13px 20px 0; }
.agent-access-error button { padding: 0; color: inherit; text-decoration: underline; background: transparent; border: 0; cursor: pointer; }
.token-list { display: grid; }
.token-row { display: grid; min-height: 78px; padding: 12px 20px; align-items: center; grid-template-columns: 34px minmax(150px,.8fr) minmax(230px,1.4fr) minmax(160px,.8fr) 34px; gap: 12px; border-bottom: 1px solid #edf0eb; }
.token-row:last-child { border-bottom: 0; }
.token-row--inactive { opacity: .65; }
.token-key-icon { display: grid; width: 32px; height: 32px; color: var(--sage); place-items: center; background: var(--sage-soft); border-radius: 5px; }
.token-identity, .token-dates { display: grid; min-width: 0; gap: 4px; }
.token-identity strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.token-identity code { overflow: hidden; color: #718077; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.token-scopes { display: flex; align-items: center; flex-wrap: wrap; gap: 5px; }
.token-scopes span { padding: 3px 6px; color: #52665a; background: #edf2ec; border-radius: 4px; font-size: 9px; font-weight: 700; }
.token-dates small { color: #7a877e; font-size: 9px; }
.token-revoked { color: #9a5b3c; font-size: 10px; font-weight: 700; text-align: center; }
.token-revoke { display: grid; width: 32px; height: 32px; padding: 0; color: #9a5b3c; place-items: center; background: transparent; border: 1px solid transparent; border-radius: 5px; cursor: pointer; }
.token-revoke:hover { background: #fff5ee; border-color: #edd3c2; }
.token-empty { display: flex; min-height: 96px; padding: 20px; align-items: center; justify-content: center; color: #819087; gap: 11px; }
.token-empty strong { display: block; color: #526056; font-size: 12px; }
.token-empty p { margin: 4px 0 0; font-size: 10px; }
.token-empty--compact { min-height: 76px; }
.token-history { border-top: 1px solid var(--line); }
.token-history > button { display: flex; width: 100%; min-height: 43px; padding: 0 20px; align-items: center; justify-content: space-between; color: #526056; background: #f7f9f5; border: 0; cursor: pointer; }
.token-history > button:hover { background: #f0f4ef; }
.token-history > button span { font-size: 11px; font-weight: 700; }
.token-history > button small { color: #7c887f; font-size: 9px; }
.token-list--history { border-top: 1px solid var(--line); }
.token-dialog { width: min(100%, 590px); max-height: calc(100vh - 40px); overflow-y: auto; }
.token-create-form { display: grid; gap: 12px; }
.token-dialog select { width: 100%; padding: 9px 10px; color: var(--ink); font: inherit; font-size: 12px; background: #fff; border: 1px solid var(--line); border-radius: 5px; outline-color: var(--sage); }
.scope-picker { display: grid; margin: 2px 0 0; padding: 0; border: 0; gap: 6px; }
.scope-picker legend { margin-bottom: 6px; color: #526056; font-size: 12px; font-weight: 700; }
.scope-picker > button { display: grid; min-height: 52px; padding: 8px 10px; align-items: center; color: var(--ink); text-align: left; background: #fff; border: 1px solid var(--line); border-radius: 5px; grid-template-columns: 20px minmax(0,1fr) auto; gap: 9px; cursor: pointer; }
.scope-picker > button:hover { border-color: #aebcb1; }
.scope-picker > button.selected { background: var(--sage-soft); border-color: #a6b7a9; }
.scope-picker > button.sensitive { border-style: dashed; }
.scope-check { display: grid; width: 18px; height: 18px; color: #fff; place-items: center; background: #fff; border: 1px solid #aab6ad; border-radius: 4px; }
.scope-picker > button.selected .scope-check { background: var(--sage); border-color: var(--sage); }
.scope-picker strong, .scope-picker small { display: block; }
.scope-picker strong { font-size: 11px; }
.scope-picker small { margin-top: 3px; color: #76837a; font-size: 9px; }
.scope-picker em { padding: 3px 5px; color: #9a5b3c; background: #fff4ec; border-radius: 4px; font-size: 8px; font-style: normal; font-weight: 800; }
.token-once-warning { display: flex; padding: 12px; align-items: flex-start; color: #8b5b39; background: #fff6ed; border: 1px solid #efd6c3; border-radius: 5px; gap: 9px; }
.token-once-warning p { display: grid; margin: 0; gap: 3px; }
.token-once-warning strong { font-size: 11px; }
.token-once-warning span { font-size: 10px; line-height: 1.5; }
.created-token-value { display: grid; padding: 10px; align-items: center; background: #f3f6f1; border: 1px solid var(--line); border-radius: 5px; grid-template-columns: minmax(0,1fr) 34px; gap: 8px; }
.created-token-value code { overflow-wrap: anywhere; color: #35463b; font-size: 11px; line-height: 1.6; }
.created-token-value button { display: grid; width: 32px; height: 32px; padding: 0; color: var(--sage); place-items: center; background: #fff; border: 1px solid var(--line); border-radius: 5px; cursor: pointer; }
.revoke-dialog { width: min(100%, 430px); text-align: center; }
.revoke-dialog-icon { display: grid; width: 40px; height: 40px; margin: 0 auto; color: #9a5b3c; place-items: center; background: #fff2e9; border-radius: 50%; }
.revoke-dialog > p { margin: 0 auto; max-width: 340px; }
.revoke-dialog footer { justify-content: center; }
.button--danger { color: #fff; background: #9a5b3c; border-color: #9a5b3c; }
.button--danger:hover:not(:disabled) { background: #7f482f; border-color: #7f482f; }
@media (max-width: 900px) { .token-row { grid-template-columns: 34px minmax(150px,.8fr) minmax(200px,1fr) 34px; }.token-dates { display: none; } }
@media (max-width: 720px) { .agent-access-header { align-items: flex-start; flex-wrap: wrap; }.agent-access-actions { width: 100%; margin-left: 46px; }.token-row { padding: 13px 14px; grid-template-columns: 32px minmax(0,1fr) 32px; }.token-scopes { grid-column: 2 / -1; }.token-revoked { grid-column: 3; grid-row: 1; }.token-revoke { grid-column: 3; grid-row: 1; } }
@media (max-width: 460px) { .agent-access-actions { margin-left: 0; }.agent-access-actions .button { min-width: 0; flex: 1; justify-content: center; }.token-dialog { padding: 22px 17px; }.scope-picker > button { grid-template-columns: 20px minmax(0,1fr); }.scope-picker em { display: none; } }
.system-update-panel { margin-top: 18px; overflow: hidden; background: rgba(252,253,249,.94); border: 1px solid var(--line); border-radius: 8px; }
.system-update-panel > header { display: flex; min-height: 72px; padding: 16px 20px; align-items: center; gap: 12px; border-bottom: 1px solid var(--line); }
.system-update-panel h2 { margin: 0; font-family: "Iowan Old Style", "Songti SC", serif; font-size: 21px; font-weight: 500; }
.system-update-panel header p { margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.55; }
.system-update-actions { display: flex; margin-left: auto; gap: 8px; }
.system-update-content { padding: 20px; }
.system-version-grid { display: grid; align-items: center; grid-template-columns: minmax(120px,1fr) auto minmax(120px,1fr) auto; gap: 18px; }
.system-version-grid > div { display: grid; gap: 5px; }
.system-version-grid small { color: var(--muted); font-size: 9px; letter-spacing: .08em; text-transform: uppercase; }
.system-version-grid strong { font-size: 15px; font-weight: 600; }
.system-version-grid code { color: #354b3c; font-family: "SFMono-Regular", Consolas, monospace; }
.system-version-arrow { color: #99a69d; }
.system-update-status { padding: 5px 8px; color: #52665a; background: #edf2ec; border-radius: 999px; font-size: 9px; font-weight: 800; white-space: nowrap; }
.system-update-status--available { color: #86592e; background: #fff1df; }
.system-update-status--failed { color: #984b38; background: #fff0eb; }
.system-update-status--succeeded { color: #315f3e; background: #e6f3e8; }
.system-update-status--recovering,
.system-update-status--checking,
.system-update-status--backing_up,
.system-update-status--pulling,
.system-update-status--building,
.system-update-status--restarting,
.system-update-status--verifying { color: #3d5f46; background: #e8f0e8; }
.system-update-progress { height: 4px; margin-top: 18px; overflow: hidden; background: #e6ebe5; border-radius: 999px; }
.system-update-progress span { display: block; height: 100%; background: var(--sage); border-radius: inherit; transition: width .45s ease; }
.system-update-message { margin: 15px 0 0; color: #65736a; font-size: 11px; line-height: 1.6; }
.system-update-error { margin-top: 10px; }
.system-update-load-error { padding: 18px 20px; }
.system-update-error button, .system-update-load-error button { padding: 0; color: inherit; text-decoration: underline; background: transparent; border: 0; cursor: pointer; }
.system-update-unavailable { display: flex; margin-top: 14px; padding: 12px; align-items: flex-start; color: #7d5a39; background: #fff8ef; border: 1px solid #eddbc6; border-radius: 6px; gap: 9px; }
.system-update-unavailable p { display: grid; margin: 0; gap: 5px; }
.system-update-unavailable strong { font-size: 11px; }
.system-update-unavailable code { font-size: 10px; user-select: all; }
.system-update-commits { display: grid; margin: 16px 0 0; padding: 0; list-style: none; border: 1px solid #e6ebe5; border-radius: 6px; }
.system-update-commits li { display: grid; min-height: 38px; padding: 8px 10px; align-items: center; grid-template-columns: 64px minmax(0,1fr) auto; gap: 10px; border-bottom: 1px solid #edf0eb; }
.system-update-commits li:last-child { border-bottom: 0; }
.system-update-commits code { color: var(--sage); font-size: 9px; }
.system-update-commits span { overflow: hidden; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.system-update-commits small { color: var(--muted); font-size: 9px; }
.system-update-log { display: grid; max-height: 150px; margin-top: 14px; padding: 11px; overflow-y: auto; color: #cbd8ce; background: #26332a; border-radius: 6px; gap: 4px; }
.system-update-log code { font-size: 9px; line-height: 1.5; white-space: pre-wrap; }
.system-backup-status { display: grid; margin-top: 14px; padding: 11px 0; border-top: 1px solid #edf0eb; border-bottom: 1px solid #edf0eb; color: var(--muted); font-size: 9px; gap: 7px; }
.system-backup-status > div { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.system-backup-status strong { color: var(--ink); font-size: 10px; }
.system-backup-status code { margin-left: 7px; color: var(--sage); overflow-wrap: anywhere; }
.system-backup-status .settings-error { margin: 0; }
.system-update-notes { display: flex; min-height: 30px; margin-top: 12px; align-items: center; color: var(--muted); font-size: 9px; gap: 12px; }
.system-update-notes .button { margin-left: auto; }
.system-update-dialog { width: min(100%, 520px); }
.system-update-dialog > h2,
.system-update-dialog > p { text-align: center; }
.system-update-confirm-version { display: grid; padding: 12px; place-items: center; color: #53645a; background: #f1f5f0; border-radius: 6px; grid-template-columns: 1fr auto 1fr; gap: 12px; }
.system-update-confirm-version code:first-child { justify-self: end; }
.system-update-confirm-version code:last-child { justify-self: start; color: var(--sage); }
.spin-icon { animation: update-spin .8s linear infinite; }
@keyframes update-spin { to { transform: rotate(360deg); } }
@media (max-width: 720px) {
  .system-update-panel > header { align-items: flex-start; flex-wrap: wrap; }
  .system-update-actions { width: 100%; margin-left: 46px; }
  .system-version-grid { grid-template-columns: 1fr auto 1fr; gap: 10px; }
  .system-update-status { grid-column: 1 / -1; justify-self: start; }
  .system-update-commits li { grid-template-columns: 56px minmax(0,1fr); }
  .system-update-commits small { display: none; }
}
@media (max-width: 460px) {
  .system-update-actions { margin-left: 0; }
  .system-update-actions .button { flex: 1; justify-content: center; }
  .system-update-content { padding: 16px; }
  .system-backup-status > div { align-items: flex-start; flex-direction: column; gap: 4px; }
}
.color-field > small { color: #6f7d74; font-size: 9px; font-weight: 500; line-height: 1.4; }
.color-field > small.color-contrast--invalid { color: #a6533d; font-weight: 700; }
.logo-pending-actions { margin-top: 2px; padding-top: 8px; border-top: 1px solid var(--line); }
.brand-preview { color: var(--ink); background: #f9fbf7; border-left: 1px solid var(--line); }
.brand-preview > span { color: #7b8980; opacity: 1; }
.brand-preview-lockup img { filter: none; }
.brand-preview-lockup strong { display: -webkit-box; overflow: hidden; color: var(--preview-color); overflow-wrap: anywhere; text-overflow: initial; white-space: normal; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.brand-preview-lockup small { color: #7b8980; overflow-wrap: anywhere; opacity: 1; text-overflow: initial; white-space: normal; }
.preview-mark i { background: var(--preview-color); }
.preview-mark i::after { background: var(--preview-color); opacity: .65; }
.brand-preview-signature { min-width: 0; padding: 14px; background: rgba(252,253,249,.75); border: 1px solid var(--line); border-radius: 6px; }
.brand-preview-signature strong,
.brand-preview-signature p,
.brand-preview-signature small { overflow-wrap: anywhere; }
.brand-preview-signature strong { display: -webkit-box; overflow: hidden; color: var(--preview-color); -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.brand-preview-signature p { display: -webkit-box; overflow: hidden; color: var(--preview-color); line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
.brand-preview-signature small { display: -webkit-box; overflow: hidden; color: #7b8980; line-height: 1.4; opacity: 1; text-overflow: initial; white-space: normal; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
</style>
