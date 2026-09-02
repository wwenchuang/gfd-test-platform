import type { ApiEndpoint } from '../api/contracts'

export interface EndpointDomainGroup {
  name: string
  groups: Array<[string, ApiEndpoint[]]>
  endpoints: ApiEndpoint[]
}

export function groupEndpoints(endpoints: ApiEndpoint[]): Array<[string, ApiEndpoint[]]> {
  const grouped = new Map<string, ApiEndpoint[]>()
  for (const endpoint of endpoints) {
    const name = endpointGroupName(endpoint)
    grouped.set(name, [...(grouped.get(name) || []), endpoint])
  }
  return [...grouped.entries()].sort(([left], [right]) => compareGroupNames(left, right))
}

export function endpointGroupName(endpoint: ApiEndpoint): string {
  const tagLabel = cleanParts(endpoint.tags).join(' / ')
  if (tagLabel) return tagLabel
  const operation = endpoint.operation || {}
  return cleanParts([
    ...folderParts(operation['x-apifox-folder']),
    ...folderParts(operation['x-apifox-folder-path']),
    ...folderParts(operation['x-apifox-folderPath']),
    ...folderParts(operation['x-apifox-folder-name']),
    ...folderParts(operation['x-apifox-folderName']),
  ]).join(' / ') || '未分组接口'
}

export function compareGroupNames(left: string, right: string): number {
  if (left === '未分组接口') return 1
  if (right === '未分组接口') return -1
  return left.localeCompare(right, 'zh-Hans-CN')
}

export function groupEndpointDomains(entries: Array<[string, ApiEndpoint[]]>): EndpointDomainGroup[] {
  const grouped = new Map<string, EndpointDomainGroup>()
  for (const entry of entries) {
    const name = endpointDomainName(entry[0])
    const domain = grouped.get(name) || { name, groups: [], endpoints: [] }
    domain.groups.push(entry)
    domain.endpoints.push(...entry[1])
    grouped.set(name, domain)
  }
  const order = ['家用', '共享', '本地', '地铁', '其他', '未分类']
  return [...grouped.values()].sort((left, right) => {
    const leftIndex = order.indexOf(left.name)
    const rightIndex = order.indexOf(right.name)
    if (leftIndex >= 0 || rightIndex >= 0) {
      if (leftIndex < 0) return 1
      if (rightIndex < 0) return -1
      return leftIndex - rightIndex
    }
    return left.name.localeCompare(right.name, 'zh-Hans-CN')
  })
}

export function endpointDomainName(group: string): string {
  if (group === '未分组接口') return '未分类'
  const parts = group.split(' / ').map(part => part.trim()).filter(Boolean)
  const first = parts[0] || ''
  if (first.includes('家用')) return '家用'
  if (first.includes('共享')) return '共享'
  if (first.includes('本地')) return '本地'
  if (first.includes('地铁')) return '地铁'
  if (parts.length === 1) return '其他'
  return first.replace(/业务$/, '') || '其他'
}

export function endpointSubgroupName(group: string, domain: string): string {
  const parts = group.split(' / ').map(part => part.trim()).filter(Boolean)
  if (parts.length > 1 && endpointDomainName(group) === domain) return parts.slice(1).join(' / ')
  return group
}

function folderParts(value: unknown): string[] {
  if (typeof value === 'string') return splitPart(value)
  if (Array.isArray(value)) return cleanParts(value.flatMap(item => folderParts(item)))
  if (!value || typeof value !== 'object') return []
  const record = value as Record<string, unknown>
  for (const key of ['path', 'paths', 'folderPath', 'folder_path', 'parentNames', 'parent_names']) {
    if (key in record) {
      const path = folderParts(record[key])
      const names = cleanParts([record.name, record.title, record.folderName, record.folder_name])
      if (names.length && path.at(-1) !== names.at(-1)) path.push(...names)
      return cleanParts(path)
    }
  }
  return cleanParts([record.name, record.title, record.folderName, record.folder_name])
}

function splitPart(value: string): string[] {
  const text = value.trim()
  if (!text) return []
  for (const separator of [' / ', '/', '>', '\\']) {
    if (text.includes(separator)) return cleanParts(text.split(separator))
  }
  return [text]
}

function cleanParts(values: unknown[]): string[] {
  const seen = new Set<string>()
  const parts: string[] = []
  for (const value of values) {
    const text = String(value || '').trim()
    if (!text || seen.has(text)) continue
    seen.add(text)
    parts.push(text)
  }
  return parts
}
