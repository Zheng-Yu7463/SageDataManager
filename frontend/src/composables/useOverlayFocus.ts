import { nextTick, onBeforeUnmount, watch } from 'vue'
import type { Ref } from 'vue'

const activeOverlays: symbol[] = []
let previousBodyOverflow = ''

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function lockDocument(token: symbol) {
  if (!activeOverlays.length) {
    previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  activeOverlays.push(token)
}

function unlockDocument(token: symbol) {
  const index = activeOverlays.lastIndexOf(token)
  if (index >= 0) activeOverlays.splice(index, 1)
  if (!activeOverlays.length) document.body.style.overflow = previousBodyOverflow
}

export function useOverlayFocus(
  open: Ref<boolean>,
  container: Ref<HTMLElement | null>,
  close: () => void,
) {
  const token = Symbol('overlay')
  let previouslyFocused: HTMLElement | null = null

  function handleKeydown(event: KeyboardEvent) {
    if (activeOverlays.at(-1) !== token) return

    if (event.key === 'Escape') {
      event.preventDefault()
      close()
      return
    }

    if (event.key !== 'Tab' || !container.value) return
    const focusable = [...container.value.querySelectorAll<HTMLElement>(focusableSelector)]
      .filter((element) => !element.hidden && element.offsetParent !== null)
    if (!focusable.length) {
      event.preventDefault()
      container.value.focus()
      return
    }

    const first = focusable[0]
    const last = focusable.at(-1)!
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  async function activate() {
    previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    lockDocument(token)
    window.addEventListener('keydown', handleKeydown)
    await nextTick()
    const preferred = container.value?.querySelector<HTMLElement>('[autofocus]')
    const first = container.value?.querySelector<HTMLElement>(focusableSelector)
    ;(preferred ?? first ?? container.value)?.focus()
  }

  function deactivate(restoreFocus = true) {
    window.removeEventListener('keydown', handleKeydown)
    unlockDocument(token)
    if (restoreFocus && previouslyFocused?.isConnected) previouslyFocused.focus()
    previouslyFocused = null
  }

  watch(open, (isOpen) => {
    if (isOpen) void activate()
    else deactivate()
  })

  onBeforeUnmount(() => deactivate(false))
}
