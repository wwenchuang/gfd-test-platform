import { computed, readonly, ref } from 'vue'

import { apiClient } from '../api/client'

export interface BusinessLine {
  id: string
  name: string
  enabled: boolean
}

const DEFAULT_LINES: BusinessLine[] = [
  { id: 'home', name: '家用', enabled: true },
  { id: 'shared', name: '共享', enabled: true },
]
const lines = ref<BusinessLine[]>(DEFAULT_LINES.map(item => ({ ...item })))
const active = computed(() => lines.value.filter(item => item.enabled))
let loading: Promise<void> | null = null

export function normalizeBusinessLines(value: unknown): BusinessLine[] {
  if (!Array.isArray(value) || !value.length) return DEFAULT_LINES.map(item => ({ ...item }))
  const normalized = value.flatMap(raw => {
    if (!raw || typeof raw !== 'object') return []
    const item = raw as Record<string, unknown>
    const id = String(item.id || '').trim()
    const name = String(item.name || '').trim()
    if (!id || !name) return []
    return [{ id, name, enabled: item.enabled !== false }]
  })
  return normalized.length ? normalized : DEFAULT_LINES.map(item => ({ ...item }))
}

export function replaceBusinessLines(value: unknown): void {
  lines.value = normalizeBusinessLines(value)
}

export function useBusinessLines() {
  return { all: readonly(lines), active }
}

export function businessLineLabel(value: unknown): string {
  const raw = String(value || '').trim()
  if (!raw) return '未标注业务'
  const matched = lines.value.find(item => raw === item.id || raw === item.name)
  if (matched) return matched.name
  if (raw === 'home') return '家用'
  if (raw === 'shared') return '共享'
  return raw
}

export function preferredBusinessLineId(values: unknown[] = []): string {
  const joined = values.map(value => String(value || '')).join(' ')
  if (joined.includes('共享')) {
    const shared = active.value.find(item => item.name === '共享')
    if (shared) return shared.id
  }
  return active.value[0]?.id || ''
}

export async function loadBusinessLines(force = false): Promise<void> {
  if (!sessionStorage.getItem('sessionToken')) return
  if (loading && !force) return loading
  loading = (async () => {
    try {
      const response = await apiClient.get<unknown>('/api/task-apps')
      const root = response as unknown as { apps?: unknown[]; data?: { apps?: unknown[] } }
      const apps = root.apps || root.data?.apps || []
      const app = apps.find(item => (
        item && typeof item === 'object' && String((item as Record<string, unknown>).package || '') === 'com.kfb.model'
      )) as Record<string, unknown> | undefined
      replaceBusinessLines(app?.business_lines)
    } catch {
      replaceBusinessLines(DEFAULT_LINES)
    }
  })()
  try {
    await loading
  } finally {
    loading = null
  }
}
