import type { CaseDraft } from '../api/contracts'

export function validateCaseDraftLocally(draft: CaseDraft): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!String(draft.app_package || '').trim()) errors.app_package = '请选择应用'
  if (!String(draft.app_name || '').trim()) errors.app_name = '请选择应用'
  if (!String(draft.business || '').trim()) errors.business = '请选择所属业务'
  draft.assertions.forEach((assertion, index) => {
    if (assertion.type !== 'status_code') return
    const expected = assertion.expected
    const values = assertion.operator === 'in' ? expected : [expected]
    const valid = Array.isArray(values)
      && values.length > 0
      && values.every(value => Number.isInteger(value) && Number(value) >= 100 && Number(value) <= 599)
    if (!valid) {
      errors[`assertions[${index}].expected`] = 'HTTP 状态码只能是 100 到 599；业务码请改用“响应 JSON 字段”，路径填写 $.code'
    }
  })
  return errors
}
