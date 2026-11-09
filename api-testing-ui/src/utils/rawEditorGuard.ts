import { inject, onMounted, onUnmounted, type Ref } from 'vue'
import { matchedRouteKey, onBeforeRouteLeave } from 'vue-router'

export interface RawEditorHandle {
  hasUnappliedRaw(): boolean
  discardUnappliedRaw(): void
}

export function useRawEditorGuard(editor: Ref<RawEditorHandle | null>) {
  const hasRaw = () => Boolean(editor.value?.hasUnappliedRaw?.())
  function confirmDiscardRaw(): boolean {
    if (!hasRaw()) return true
    if (!window.confirm('原始 JSON 中有未应用的内容，继续将丢失这些内容。确定继续吗？')) return false
    editor.value?.discardUnappliedRaw?.()
    return true
  }
  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (!hasRaw()) return
    event.preventDefault()
    event.returnValue = ''
  }
  if (inject(matchedRouteKey, null)) onBeforeRouteLeave(() => (
    !hasRaw() || window.confirm('原始 JSON 中有未应用的内容，离开将丢失这些内容。确定离开吗？')
  ))
  onMounted(() => window.addEventListener('beforeunload', beforeUnload))
  onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
  return { hasRaw, confirmDiscardRaw }
}
