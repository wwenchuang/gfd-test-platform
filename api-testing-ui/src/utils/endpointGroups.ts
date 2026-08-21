import type { ApiEndpoint } from '../api/contracts'

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
