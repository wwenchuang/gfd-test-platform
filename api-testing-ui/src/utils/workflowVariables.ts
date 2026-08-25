import type { CaseDependencyOption, CaseDraft, InlineWorkflowStep, WorkflowVariableOption } from '../api/contracts'

export type WorkflowVariableStage = 'setup' | 'main' | 'cleanup'

export function workflowVariableOptions(
  draft: CaseDraft,
  stage: WorkflowVariableStage,
  index: number,
  environmentNames: string[],
  dependencyOptions: CaseDependencyOption[],
): WorkflowVariableOption[] {
  const result: WorkflowVariableOption[] = []
  const positions = new Map<string, number>()
  const add = (name: unknown, source: string, sourceKind: WorkflowVariableOption['sourceKind']): void => {
    if (typeof name !== 'string' || !name.trim()) return
    const normalized = name.trim()
    const option = { name: normalized, source, sourceKind, available: true }
    const position = positions.get(normalized)
    if (position === undefined) {
      positions.set(normalized, result.length)
      result.push(option)
    } else {
      result[position] = option
    }
  }

  for (const name of [...new Set(environmentNames)].sort((a, b) => a.localeCompare(b, 'zh-CN'))) {
    add(name, '当前执行环境', 'environment')
  }

  const dependencyById = new Map(dependencyOptions.map(option => [option.id, option]))
  for (const dependency of draft.dependencies || []) {
    const id = typeof dependency.case_version_id === 'string' ? dependency.case_version_id : ''
    const option = dependencyById.get(id)
    const exports = Array.isArray(dependency.exports) ? dependency.exports : option?.exports || []
    for (const name of exports) add(name, `共享前置用例 · ${option?.name || '历史依赖'}`, 'dependency')
  }

  const setupSteps = draft.processing?.setup_steps || []
  const setupLimit = stage === 'setup' ? Math.max(0, index) : setupSteps.length
  for (let stepIndex = 0; stepIndex < setupLimit; stepIndex += 1) {
    addStepExtractions(setupSteps[stepIndex], `前置步骤 ${stepIndex + 1} · ${setupSteps[stepIndex].name}`, 'setup', add)
  }

  if (stage === 'cleanup') {
    for (const extraction of draft.extractions || []) add(extraction.target, '主体响应', 'main')
    const cleanupSteps = draft.processing?.cleanup_steps || []
    for (let stepIndex = 0; stepIndex < Math.max(0, index); stepIndex += 1) {
      addStepExtractions(cleanupSteps[stepIndex], `清理步骤 ${stepIndex + 1} · ${cleanupSteps[stepIndex].name}`, 'setup', add)
    }
  }

  return result
}

export function withLegacyVariables(options: WorkflowVariableOption[], selected: string[]): WorkflowVariableOption[] {
  const known = new Set(options.map(option => option.name))
  return [
    ...options,
    ...selected
      .filter(name => name && !known.has(name))
      .map(name => ({ name, source: '未找到来源', sourceKind: 'unknown' as const, available: false })),
  ]
}

function addStepExtractions(
  step: InlineWorkflowStep | undefined,
  source: string,
  sourceKind: WorkflowVariableOption['sourceKind'],
  add: (name: unknown, source: string, sourceKind: WorkflowVariableOption['sourceKind']) => void,
): void {
  if (!step?.enabled) return
  for (const extraction of step.extractions || []) add(extraction.target, source, sourceKind)
}
