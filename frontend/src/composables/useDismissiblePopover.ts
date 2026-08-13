import { nextTick, onBeforeUnmount, watch } from 'vue'
import type { Ref } from 'vue'

export function useDismissiblePopover(
  open: Ref<boolean>,
  trigger: Ref<HTMLElement | null>,
  container: Ref<HTMLElement | null>,
) {
  function handlePointerDown(event: PointerEvent) {
    const target = event.target as Node
    if (!trigger.value?.contains(target) && !container.value?.contains(target)) {
      open.value = false
    }
  }

  function handleKeydown(event: KeyboardEvent) {
    if (event.key !== 'Escape') return
    event.preventDefault()
    open.value = false
    void nextTick(() => trigger.value?.focus())
  }

  function startListening() {
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeydown)
  }

  function stopListening() {
    window.removeEventListener('pointerdown', handlePointerDown)
    window.removeEventListener('keydown', handleKeydown)
  }

  watch(open, (isOpen) => {
    if (isOpen) startListening()
    else stopListening()
  })

  onBeforeUnmount(stopListening)
}
