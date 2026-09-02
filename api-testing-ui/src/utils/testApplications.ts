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

export interface TestApplicationSelection {
  selectable: boolean
  reason: string
}

export interface InferredTestApplicationBusiness {
  appPackage: string
  appName: string
  business: string
  businessName: string
}

const applications = ref<TestApplication[]>([])
const active = computed(() => applications.value.filter(item => item.enabled))
let loading: Promise<void> | null = null
const CHINESE_TEXT = /[\u3400-\u9fff]/

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

export function testApplicationLabel(appPackage: unknown, snapshotName: unknown = ''): string {
  const configured = testApplicationFor(appPackage)?.name.trim()
  if (configured) return configured
  const snapshot = String(snapshotName || '').trim()
  return CHINESE_TEXT.test(snapshot) ? snapshot : '未标注应用'
}

export function applicationBusinessLabel(appPackage: unknown, appName: unknown, business: unknown): string {
  const rawBusiness = String(business || '').trim()
  const packageName = String(appPackage || '').trim()
    || (rawBusiness === 'home' || rawBusiness === 'shared' ? 'com.kfb.model' : '')
  const configured = testApplicationFor(packageName)?.business_lines
    .find(item => rawBusiness === item.id || rawBusiness === item.name)?.name
  let businessName = configured || ''
  if (!businessName && packageName === 'com.kfb.model') {
    if (rawBusiness === 'home') businessName = '家用'
    if (rawBusiness === 'shared') businessName = '共享'
  }
  if (!businessName && CHINESE_TEXT.test(rawBusiness)) businessName = rawBusiness
  return `${testApplicationLabel(packageName, appName)} · ${businessName || '未标注业务'}`
}

export function activeBusinessLinesFor(appPackage: unknown): TestApplicationBusinessLine[] {
  return testApplicationFor(appPackage)?.business_lines.filter(item => item.enabled) || []
}

export function inferTestApplicationBusiness(values: unknown[]): InferredTestApplicationBusiness | null {
  const text = values.map(value => String(value || '').trim()).filter(Boolean).join(' ')
  if (!text) return null
  const matches = active.value.flatMap(application => application.business_lines
    .filter(line => line.enabled && text.includes(line.name))
    .map(line => ({
      appPackage: application.package,
      appName: application.name,
      business: line.id,
      businessName: line.name,
    })))
  return matches.length === 1 ? matches[0] : null
}

export function applicationBusinessSelection(appPackage: unknown, business: unknown): TestApplicationSelection {
  const application = testApplicationFor(appPackage)
  if (!application) return { selectable: false, reason: '应用未配置或已移除' }
  if (!application.enabled) return { selectable: false, reason: `应用“${application.name}”已停用` }
  const rawBusiness = String(business || '').trim()
  const line = application.business_lines.find(item => rawBusiness === item.id || rawBusiness === item.name)
  if (!line) return { selectable: false, reason: '业务未配置或已移除' }
  if (!line.enabled) return { selectable: false, reason: `业务“${line.name}”已停用` }
  return { selectable: true, reason: '' }
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
