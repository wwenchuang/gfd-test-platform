import { computed, readonly, ref } from 'vue'

import { apiClient } from '../api/client'

export interface TestApplicationBusinessLine {
  id: string
  name: string
  enabled: boolean
}

export interface TestApplication {
  package: string
  name: string
  enabled: boolean
  business_lines: TestApplicationBusinessLine[]
}

const applications = ref<TestApplication[]>([])
const active = computed(() => applications.value.filter(item => item.enabled))
let loading: Promise<void> | null = null

function normalizeBusinessLines(value: unknown): TestApplicationBusinessLine[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(raw => {
    if (!raw || typeof raw !== 'object') return []
    const item = raw as Record<string, unknown>
    const id = String(item.id || '').trim()
    const name = String(item.name || '').trim()
    return id && name ? [{ id, name, enabled: item.enabled !== false }] : []
  })
}

export function normalizeTestApplications(value: unknown): TestApplication[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(raw => {
    if (!raw || typeof raw !== 'object') return []
    const item = raw as Record<string, unknown>
    const packageName = String(item.package || '').trim()
    const name = String(item.name || '').trim()
    return packageName && name
      ? [{ package: packageName, name, enabled: item.enabled !== false, business_lines: normalizeBusinessLines(item.business_lines) }]
      : []
  })
}

export function replaceTestApplications(value: unknown): void {
  applications.value = normalizeTestApplications(value)
}

export function useTestApplications() {
  return { all: readonly(applications), active }
}

export function testApplicationFor(appPackage: unknown): TestApplication | undefined {
  const packageName = String(appPackage || '').trim()
  return applications.value.find(item => item.package === packageName)
}

export function activeBusinessLinesFor(appPackage: unknown): TestApplicationBusinessLine[] {
  return testApplicationFor(appPackage)?.business_lines.filter(item => item.enabled) || []
}

export async function loadTestApplications(force = false): Promise<void> {
  if (!sessionStorage.getItem('sessionToken')) return
  if (loading && !force) return loading
  loading = (async () => {
    try {
      const response = await apiClient.get<unknown>('/api/task-apps')
      const root = response as { apps?: unknown[]; data?: { apps?: unknown[] } }
      replaceTestApplications(root.apps || root.data?.apps || [])
    } catch {
      replaceTestApplications([])
    }
  })()
  try {
    await loading
  } finally {
    loading = null
  }
}
