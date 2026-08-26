export interface ApiExecutionConfirmation {
  action: string
  environmentName: string
  targetName?: string
  caseCount?: number
}

export function isProductionLikeEnvironment(name: string): boolean {
  return name.includes('生产') || /\bprod(?:uction)?\b/i.test(name)
}

export function confirmApiExecution(input: ApiExecutionConfirmation): boolean {
  const target = input.targetName ? `“${input.targetName}”` : ''
  const count = input.caseCount == null ? '' : `（${input.caseCount} 条用例）`
  if (isProductionLikeEnvironment(input.environmentName)) {
    return window.confirm(
      `当前目标是生产环境“${input.environmentName}”。确认${input.action}${target}${count}？请求将真实发送到该环境。`,
    )
  }
  return window.confirm(`确认在“${input.environmentName}”${input.action}${target}${count}？`)
}
