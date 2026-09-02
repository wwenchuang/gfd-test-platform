const TEXT_INPUT_TYPES = new Set(['', 'text', 'search', 'email', 'url', 'tel', 'password'])

function isEditableTextTarget(target: EventTarget | null, root: Document): target is HTMLInputElement | HTMLTextAreaElement {
  const view = root.defaultView
  if (!view) return false
  if (target instanceof view.HTMLTextAreaElement) return true
  return target instanceof view.HTMLInputElement && TEXT_INPUT_TYPES.has(target.type.toLocaleLowerCase())
}

export function installImeCompositionGuard(root: Document = document): () => void {
  const installedRoot = root as Document & { __midsceneImeCompositionGuardInstalled?: boolean }
  if (installedRoot.__midsceneImeCompositionGuardInstalled) return () => {}
  const composing = new WeakSet<EventTarget>()
  const pendingCommit = new WeakMap<EventTarget, number>()
  let commitSequence = 0
  let active = true
  const onCompositionStart = (event: Event): void => {
    if (isEditableTextTarget(event.target, root)) composing.add(event.target)
  }
  const onCompositionEnd = (event: Event): void => {
    if (!isEditableTextTarget(event.target, root)) return
    const target = event.target
    const committedValue = target.value
    const token = ++commitSequence
    composing.delete(target)
    pendingCommit.set(target, token)
    root.defaultView?.setTimeout(() => {
      if (!active || pendingCommit.get(target) !== token || target.value !== committedValue) return
      pendingCommit.delete(target)
      target.dispatchEvent(new root.defaultView!.InputEvent('input', {
        bubbles: true,
        composed: true,
        data: (event as CompositionEvent).data || null,
        inputType: 'insertCompositionText',
        isComposing: false,
      }))
    }, 0)
  }
  const onInput = (event: Event): void => {
    if (!isEditableTextTarget(event.target, root)) return
    if ((event as InputEvent).isComposing || composing.has(event.target)) event.stopImmediatePropagation()
    else pendingCommit.delete(event.target)
  }

  root.addEventListener('compositionstart', onCompositionStart, true)
  root.addEventListener('compositionend', onCompositionEnd, true)
  root.addEventListener('input', onInput, true)
  installedRoot.__midsceneImeCompositionGuardInstalled = true
  return () => {
    active = false
    root.removeEventListener('compositionstart', onCompositionStart, true)
    root.removeEventListener('compositionend', onCompositionEnd, true)
    root.removeEventListener('input', onInput, true)
    delete installedRoot.__midsceneImeCompositionGuardInstalled
  }
}
