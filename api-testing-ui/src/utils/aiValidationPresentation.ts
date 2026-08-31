export interface AiValidationPresentation {
  title: string
  reason: string
  actions: string[]
  originalMessage: string
  model: string
  needsAiDiagnosis: boolean
  issueIndex: number
}

const PATH_LABELS: Record<string, string> = {
  case: '',
  request: '主体请求',
  body: '请求体',
  headers: '请求头',
  cookies: 'Cookie',
  data_rows: '数据行',
  processing: '数据处理',
  pre: '前置处理',
  post: '后置处理',
  setup_steps: '前置步骤',
  cleanup_steps: '清理步骤',
}

function fieldPath(rawPath: string): string {
  return rawPath
    .split('.')
    .map(part => PATH_LABELS[part] ?? part.replace(/\[(\d+)\]/g, '第 $1 项'))
    .filter(Boolean)
    .join(' → ')
}

function variableName(rawPath: string): string {
  const name = rawPath.split('.').at(-1)?.replace(/\[\d+\]/g, '') || 'runtimeValue'
  return /^[A-Za-z_][A-Za-z0-9_.-]*$/.test(name) ? name : 'runtimeValue'
}

function stringValue(row: Record<string, unknown>, key: string): string {
  return typeof row[key] === 'string' ? row[key] as string : ''
}

export function presentAiValidationIssue(
  issues: Array<Record<string, unknown>>,
): AiValidationPresentation | null {
  const issueIndex = issues.findIndex(item => typeof item.message === 'string')
  const issue = issues[issueIndex]
  if (!issue) return null

  const originalMessage = stringValue(issue, 'original_message') || stringValue(issue, 'message')
  // The platform contract takes precedence over cached AI advice for known rules.
  const operatorIssue = originalMessage.match(/assertions\[(\d+)\](?:\.operator is not supported| operator \S+ is not supported for \S+)$/)
  if (operatorIssue) {
    return {
      title: `第 ${Number(operatorIssue[1]) + 1} 条断言的比较方式不受支持`,
      reason: 'operator 只能填写平台支持的比较方式，例如 equals（等于）或 in（属于集合），不能填写完整表达式；不同断言类型支持的比较方式不同。',
      actions: [
        '在用例编辑器选择断言类型和对应的比较方式；期望值单独填入 expected，JSON 字段路径单独填入 path。',
        '期望 HTTP 200 时使用 status_code、equals、expected=200；HTTP 200 不代表业务成功。业务断言使用 json_path，预期值需依据接口合同、实际响应和业务期望确定，不能统一设置成功码。',
        '未通过校验的候选没有保存为可执行用例。补充上述约束后重新生成并调试；已有草稿可从下方结果列表打开编辑。',
      ],
      originalMessage, model: '', needsAiDiagnosis: false, issueIndex,
    }
  }
  const structuredTitle = stringValue(issue, 'title')
  const structuredReason = stringValue(issue, 'reason')
  const structuredSolution = stringValue(issue, 'solution')
  const diagnosis = issue.diagnosis && typeof issue.diagnosis === 'object'
    ? issue.diagnosis as Record<string, unknown>
    : null
  const analysis = diagnosis?.analysis && typeof diagnosis.analysis === 'object'
    ? diagnosis.analysis as Record<string, unknown>
    : null
  const recommendations = Array.isArray(analysis?.recommendations)
    ? analysis.recommendations.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
    : []
  if (analysis && stringValue(analysis, 'summary') && stringValue(analysis, 'root_cause')) {
    return {
      title: stringValue(analysis, 'summary'),
      reason: stringValue(analysis, 'root_cause'),
      actions: recommendations,
      originalMessage,
      model: stringValue(diagnosis!, 'model'),
      needsAiDiagnosis: false,
      issueIndex,
    }
  }
  if (structuredTitle && structuredReason) {
    return {
      title: structuredTitle,
      reason: structuredReason,
      actions: structuredSolution ? [structuredSolution] : [],
      originalMessage,
      model: '',
      needsAiDiagnosis: false,
      issueIndex,
    }
  }

  if (issue.code === 'missing_endpoint_coverage') {
    return {
      title: '部分接口没有生成有效用例',
      reason: '模型返回结果中缺少部分已选接口，或对应候选未通过平台校验。',
      actions: ['先查看已生成草稿，再缩小接口范围或补充测试意图后重新生成缺失部分。'],
      originalMessage,
      model: '',
      needsAiDiagnosis: false,
      issueIndex,
    }
  }

  const literalMatch = originalMessage.match(
    /^literal credential is not allowed at ([^;]+); use a variable placeholder$/,
  )
  if (literalMatch) {
    const rawPath = literalMatch[1]
    const name = variableName(rawPath)
    return {
      title: '检测到写死的敏感值或运行时标识',
      reason: `${fieldPath(rawPath)} 使用了固定值。该值可能泄露环境数据，也可能在下次执行时失效。`,
      actions: [
        `固定环境值：进入“环境设置”新增变量 ${name}，再把该字段改为 {{${name}}}。`,
        '动态业务值：添加前置步骤调用上游接口，从响应中提取变量后再用于主体请求。',
      ],
      originalMessage,
      model: '',
      needsAiDiagnosis: false,
      issueIndex,
    }
  }

  const placeholderMatch = originalMessage.match(
    /^sensitive value at ([^;]+) must use a complete variable placeholder$/,
  )
  if (placeholderMatch) {
    const rawPath = placeholderMatch[1]
    const name = variableName(rawPath)
    return {
      title: '敏感字段必须使用完整变量占位符',
      reason: `${fieldPath(rawPath)} 不能拼接或直接保存敏感值。`,
      actions: [`进入“环境设置”新增变量 ${name}，并将字段完整填写为 {{${name}}}。`],
      originalMessage,
      model: '',
      needsAiDiagnosis: false,
      issueIndex,
    }
  }

  if (originalMessage === 'AI Gateway content is not strict JSON') {
    return {
      title: 'AI 返回内容格式不正确',
      reason: '模型返回的内容不是平台可解析的标准 JSON，因此没有创建草稿。',
      actions: ['点击“重新生成当前范围”；若连续出现，请检查模型配置或缩小单次生成的接口数量。'],
      originalMessage,
      model: '',
      needsAiDiagnosis: false,
      issueIndex,
    }
  }

  const hasChinese = /[\u4e00-\u9fff]/.test(originalMessage)
  return {
    title: hasChinese ? '生成结果未通过校验' : 'AI 返回的用例未通过平台校验',
    reason: hasChinese ? originalMessage : '平台已拦截不符合安全或可执行性规则的候选用例。',
    actions: ['可使用当前配置的千问分析英文原文，或调整测试意图后重新生成。'],
    originalMessage,
    model: '',
    needsAiDiagnosis: !hasChinese,
    issueIndex,
  }
}

export function aiValidationSummary(issues: Array<Record<string, unknown>>): string {
  const presentation = presentAiValidationIssue(issues)
  return presentation ? `${presentation.title}：${presentation.reason}` : ''
}
