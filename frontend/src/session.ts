import { readonly, ref } from 'vue'

import type { AccountLoginResponse, AccountSummary } from '@/types'

const sessionTokenKey = 'sage-session-token'
const account = ref<AccountSummary | null>(null)
const restoring = ref(true)

export function getSessionToken() {
  return window.localStorage.getItem(sessionTokenKey)
}

export function establishSession(response: AccountLoginResponse) {
  window.localStorage.setItem(sessionTokenKey, response.session_token)
  account.value = response
  restoring.value = false
}

export function completeSessionRestoration(restoredAccount: AccountSummary | null) {
  account.value = restoredAccount
  restoring.value = false
}

export function expireSession() {
  window.localStorage.removeItem(sessionTokenKey)
  account.value = null
  restoring.value = false
}

export function useSession() {
  return {
    account: readonly(account),
    restoring: readonly(restoring),
    establishSession,
    completeSessionRestoration,
    expireSession,
  }
}
