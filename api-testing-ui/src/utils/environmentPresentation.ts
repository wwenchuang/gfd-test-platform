import type { EnvironmentServiceSnapshot } from '../api/contracts'

export interface EnvironmentServiceGroup {
  id: string
  baseUrl: string
  configured: boolean
  serviceKeys: string[]
  labels: string[]
}

export interface EnvironmentServicePresentation {
  effectiveAddressCount: number
  serviceKeyCount: number
  unconfiguredKeyCount: number
  groups: EnvironmentServiceGroup[]
}

export function environmentServicePresentation(
  services: Record<string, EnvironmentServiceSnapshot>,
): EnvironmentServicePresentation {
  const groups = new Map<string, EnvironmentServiceGroup>()
  let unconfiguredKeyCount = 0

  for (const [key, service] of Object.entries(services)) {
    const baseUrl = normalizeBaseUrl(service.base_url)
    const configured = Boolean(baseUrl)
    const id = configured ? baseUrl : '__unconfigured__'
    const group = groups.get(id) || {
      id,
      baseUrl,
      configured,
      serviceKeys: [],
      labels: [],
    }
    const label = serviceLabel(service, key)

    group.serviceKeys.push(key)
    if (!group.labels.includes(label)) group.labels.push(label)
    groups.set(id, group)
    if (!configured) unconfiguredKeyCount += 1
  }

  const result = Array.from(groups.values())
  return {
    effectiveAddressCount: result.filter(group => group.configured).length,
    serviceKeyCount: Object.keys(services).length,
    unconfiguredKeyCount,
    groups: result,
  }
}

function normalizeBaseUrl(value: string | null): string {
  return (value || '').trim().replace(/\/+$/, '')
}

function serviceLabel(service: EnvironmentServiceSnapshot, key: string): string {
  if (service.module_name?.trim() && !isOpaqueId(service.module_name)) return service.module_name.trim()
  if (service.name?.trim() && service.name !== 'default' && !isOpaqueId(service.name)) return service.name.trim()
  return key === 'default' || service.name === 'default' ? '默认服务' : key
}

function isOpaqueId(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(value)
}
