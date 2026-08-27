import type { ApiEndpoint, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import { hasExplicitOneTimeMarker } from './caseClassification'
import { compareGroupNames } from './endpointGroups'

export type CaseListItem =
  | {
    kind: 'version'
    id: string
    endpoint: ApiEndpoint
    name: string
    meta: string
    groupName: string
    version: CaseVersion
  }
  | {
    kind: 'preview'
    id: string
    endpoint: ApiEndpoint
    name: string
    meta: string
    groupName: string
    preview: GeneratedCasePreview
  }

export type CaseWorkView = 'all' | 'regular' | 'debugged' | 'baseline' | 'task' | 'orchestrated' | 'one-time' | 'candidate'

export interface CaseGroupNode {
  id: string
  label: string
  fullPath: string
  count: number
  items: CaseListItem[]
  children: CaseGroupNode[]
}

interface MutableCaseGroupNode extends Omit<CaseGroupNode, 'children'> {
  childMap: Map<string, MutableCaseGroupNode>
}

export function buildCaseGroupTree(items: CaseListItem[]): CaseGroupNode[] {
  const roots = new Map<string, MutableCaseGroupNode>()
  for (const item of items) {
    const parts = groupParts(item.groupName)
    let siblings = roots
    let parentPath = ''
    let node: MutableCaseGroupNode | null = null
    for (const label of parts) {
      const fullPath = parentPath ? `${parentPath} / ${label}` : label
      node = siblings.get(label) || {
        id: fullPath,
        label,
        fullPath,
        count: 0,
        items: [],
        childMap: new Map(),
      }
      siblings.set(label, node)
      siblings = node.childMap
      parentPath = fullPath
    }
    node?.items.push(item)
  }
  return finalizeNodes(roots)
}

export function matchesCaseWorkView(
  item: CaseListItem,
  view: CaseWorkView,
  selectedEndpointIds: Set<string>,
): boolean {
  if (view === 'all') return true
  if (view === 'task') return selectedEndpointIds.has(item.endpoint.id)
  if (view === 'candidate') return item.kind === 'preview'
  if (view === 'one-time') return isOneTimeCase(item)
  if (view === 'regular') return item.kind === 'version'
    && !item.version.lifecycle?.debug_status
    && item.version.lifecycle?.baseline_status !== 'active'
  if (view === 'debugged') return item.kind === 'version' && Boolean(item.version.lifecycle?.debug_status)
  if (view === 'baseline') return item.kind === 'version' && item.version.lifecycle?.baseline_status === 'active'
  return item.kind === 'version' && hasWorkflowSteps(item.version)
}

export function caseSearchText(item: CaseListItem): string {
  return [
    item.groupName,
    item.name,
    item.endpoint.method,
    item.endpoint.path,
    item.endpoint.summary,
    item.endpoint.tags.join(' '),
    item.meta,
  ].join(' ')
}

export function caseGroupAncestorIds(fullPath: string): string[] {
  const parts = groupParts(fullPath)
  return parts.map((_, index) => parts.slice(0, index + 1).join(' / '))
}

export function caseGroupNodeIds(nodes: CaseGroupNode[]): string[] {
  return nodes.flatMap(node => [node.id, ...caseGroupNodeIds(node.children)])
}

function groupParts(groupName: string): string[] {
  const parts = groupName.split('/').map(part => part.trim()).filter(Boolean)
  return parts.length ? parts : ['未分组用例']
}

function finalizeNodes(nodes: Map<string, MutableCaseGroupNode>): CaseGroupNode[] {
  return [...nodes.values()]
    .sort((left, right) => compareCaseGroupNames(left.label, right.label))
    .map(node => {
      const children = finalizeNodes(node.childMap)
      return {
        id: node.id,
        label: node.label,
        fullPath: node.fullPath,
        items: node.items,
        children,
        count: node.items.length + children.reduce((total, child) => total + child.count, 0),
      }
    })
}

function compareCaseGroupNames(left: string, right: string): number {
  if (left === '未分组用例') return 1
  if (right === '未分组用例') return -1
  return compareGroupNames(left, right)
}

function hasWorkflowSteps(version: CaseVersion): boolean {
  const processing = version.processing
  return Boolean(
    processing.pre.length
    || processing.post.length
    || processing.setup_steps?.some(step => step.enabled)
    || processing.cleanup_steps?.some(step => step.enabled),
  )
}

function isOneTimeCase(item: CaseListItem): boolean {
  return hasExplicitOneTimeMarker([item.groupName, item.name, ...item.endpoint.tags])
}
