export interface ApiExecutionConfirmation {
  action: string
  environmentName: string
  targetName?: string
  caseCount?: number
  effect?: string
}

export interface ApiExecutionScope {
  projectId: string
  sourceRevisionId: string
  environmentRevisionId: string
  selectionGeneration: number
}

type CurrentExecutionScope = {
  projectId: string | null
  sourceRevisionId: string | null
  environmentRevisionId: string | null
  selectionGeneration: number
}

export function captureApiExecutionScope(context: CurrentExecutionScope): ApiExecutionScope | null {
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId) return null
  return {
    projectId: context.projectId,
    sourceRevisionId: context.sourceRevisionId,
    environmentRevisionId: context.environmentRevisionId,
    selectionGeneration: context.selectionGeneration,
  }
}

export function matchesApiExecutionScope(context: CurrentExecutionScope, scope: ApiExecutionScope): boolean {
  return context.selectionGeneration === scope.selectionGeneration
    && context.projectId === scope.projectId
    && context.sourceRevisionId === scope.sourceRevisionId
    && context.environmentRevisionId === scope.environmentRevisionId
}

export function isProductionLikeEnvironment(name: string): boolean {
  return name.includes('生产') || /\bprod(?:uction)?\b/i.test(name)
}

export function confirmApiExecution(input: ApiExecutionConfirmation): boolean {
  const target = input.targetName ? `“${input.targetName}”` : ''
  const count = input.caseCount == null ? '' : `（${input.caseCount} 条用例）`
  const effect = input.effect ? ` ${input.effect}` : ''
  if (!input.environmentName || ['任务保存环境', '未选择环境'].includes(input.environmentName)) {
    return window.confirm(
      `无法确认当前环境名称，可能是生产环境；请求将真实发送。请核实目标环境后确认${input.action}${target}${count}。${effect}`,
    )
  }
  if (isProductionLikeEnvironment(input.environmentName)) {
    return window.confirm(
      `当前目标是生产环境“${input.environmentName}”。确认${input.action}${target}${count}？请求将真实发送到该环境。${effect}`,
    )
  }
  return window.confirm(`确认在“${input.environmentName}”${input.action}${target}${count}？${effect}`)
}
