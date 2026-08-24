import type { ApiEndpoint, CaseDependencyOption, CaseVersion } from '../api/contracts'

export function buildCaseDependencyOptions(
  versions: CaseVersion[],
  endpoints: ApiEndpoint[],
  currentVersionId = '',
): CaseDependencyOption[] {
  const endpointById = new Map(endpoints.map(endpoint => [endpoint.id, endpoint]))
  return versions
    .filter(version => version.id !== currentVersionId)
    .map(version => {
      const endpoint = endpointById.get(version.endpoint_id)
      const directory = (endpoint?.tags || []).slice(1).filter(Boolean).join(' / ')
      return {
        id: version.id,
        name: version.name,
        group: version.group_name || directory || '未分组',
        method: endpoint?.method || version.request.method,
        path: endpoint?.path || version.request.path,
        version: version.version,
        exports: version.extractions
          .map(item => typeof item.target === 'string' ? item.target.trim() : '')
          .filter(Boolean),
      }
    })
    .sort((left, right) => (
      left.group.localeCompare(right.group, 'zh-CN')
      || left.name.localeCompare(right.name, 'zh-CN')
      || right.version - left.version
    ))
}
