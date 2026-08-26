// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest'

import { confirmApiExecution, isProductionLikeEnvironment } from './executionConfirmation'

describe('execution confirmation', () => {
  it('recognizes Chinese and English production environment names without matching product names', () => {
    expect(isProductionLikeEnvironment('生产环境（新）-腾讯云')).toBe(true)
    expect(isProductionLikeEnvironment('prod-us-east')).toBe(true)
    expect(isProductionLikeEnvironment('Production')).toBe(true)
    expect(isProductionLikeEnvironment('产品验收环境')).toBe(false)
    expect(isProductionLikeEnvironment('测试环境')).toBe(false)
  })

  it('explains the real effect before executing against production', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)

    const accepted = confirmApiExecution({
      action: '执行任务',
      environmentName: '生产环境（新）-腾讯云',
      targetName: '每日发版回归',
      caseCount: 12,
    })

    expect(accepted).toBe(false)
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/生产环境.*每日发版回归.*12 条.*真实发送/))
  })

  it('uses a concise confirmation for non-production execution', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    expect(confirmApiExecution({
      action: '调试用例',
      environmentName: '测试环境',
      targetName: '登录成功',
      caseCount: 1,
    })).toBe(true)
    expect(confirm).toHaveBeenCalledWith('确认在“测试环境”调试用例“登录成功”（1 条用例）？')
  })
})
