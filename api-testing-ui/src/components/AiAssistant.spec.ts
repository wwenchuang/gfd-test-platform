// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { AiJob, CaseVersion } from '../api/contracts'
import AiAssistant from './AiAssistant.vue'

const job: AiJob = {
  id: 'job-1', state: 'completed', endpoint_ids: ['endpoint-1'], requested_model: 'qwen-plus', actual_model: 'qwen-plus',
  fallback_used: false, summary: {},
  batches: [{
    id: 'batch-1', sequence: 1, state: 'completed', endpoint_ids: ['endpoint-1'], requested_model: 'qwen-plus', actual_model: 'qwen-plus',
    fallback_used: false, fallback_reason: '', generated_draft_ids: ['version-1', 'version-2'], validation_errors: [],
  }],
}

const generatedCases = [
  { id: 'version-1', endpoint_id: 'endpoint-1', name: '正常分页', request: { method: 'GET', path: '/list' } },
  { id: 'version-2', endpoint_id: 'endpoint-1', name: '页码边界', request: { method: 'GET', path: '/list' } },
] as CaseVersion[]

describe('AiAssistant', () => {
  it('explains queue submission failure without blaming the model', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: {
      ...job, state: 'failed_gateway', batches: [{ ...job.batches[0], state: 'failed_gateway', generated_draft_ids: [], error_code: 'enqueue_failed' }],
    } } })
    expect(wrapper.text()).toContain('任务提交失败')
    expect(wrapper.text()).toContain('未发起模型调用')
    expect(wrapper.text()).not.toContain('模型调用失败')
    expect(wrapper.text()).not.toContain('模型失败')
  })

  it('explains timeout without showing provider details', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: {
      ...job, state: 'failed_gateway', batches: [{ ...job.batches[0], state: 'failed_gateway', generated_draft_ids: [], error_code: 'gateway_timeout' }],
    } } })
    expect(wrapper.text()).toContain('模型服务或连接等待超时')
    expect(wrapper.text()).toContain('没有生成新草稿')
  })

  it('uses the assertion contract instead of a saved misleading AI operator diagnosis', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: {
      ...job,
      batches: [{ ...job.batches[0], validation_errors: [{
        message: 'assertions[1].operator is not supported',
        diagnosis: { model: 'qwen-plus', analysis: {
          summary: '错误的旧建议', root_cause: '把 operator 改为 code == 200',
          recommendations: ['jsonpath:$.code==0', '调用未核实的前置接口'],
        } },
      }] }],
    } } })
    expect(wrapper.text()).toContain('第 2 条断言')
    expect(wrapper.text()).toContain('equals')
    expect(wrapper.text()).toContain('expected')
    expect(wrapper.text()).toContain('HTTP 200 不代表业务成功')
    expect(wrapper.text()).not.toContain('错误的旧建议')
    expect(wrapper.text()).not.toContain('code == 200')
    expect(wrapper.text()).not.toContain('jsonpath:$.code==0')
    expect(wrapper.find('[data-testid="diagnose-validation-batch-1"]').exists()).toBe(false)
  })

  it.each([0, 1])('explains an imprecise business-code assertion at index %s without calling AI', (index) => {
    const original = `deterministic case validation failed: business_assertion_imprecise:assertions[${index}].operator`
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: {
      ...job, state: 'partial', batches: [{ ...job.batches[0], state: 'partial', validation_errors: [{
        code: 'candidate_validation_error', message: original,
        diagnosis: { model: 'qwen-plus', analysis: {
          summary: '旧的错误建议', root_cause: '随便填写 code 值', recommendations: ['使用 200'],
        } },
      }] }],
    } } })
    expect(wrapper.text()).toContain(`第 ${index + 1} 条断言`)
    expect(wrapper.text()).toContain('业务码')
    expect(wrapper.text()).toContain('等于')
    expect(wrapper.text()).toContain('属于')
    expect(wrapper.text()).toContain('接口合同')
    expect(wrapper.text()).toContain('未保存')
    expect(wrapper.text()).toContain('草稿')
    expect(wrapper.find('[data-testid="validation-original-message"]').text()).toBe(original)
    expect(wrapper.find('[data-testid="diagnose-validation-batch-1"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('旧的错误建议')
    expect(wrapper.emitted('diagnose-validation')).toBeUndefined()
  })

  it('defaults to business and parameter cases instead of runtime header cases', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: null } })
    const textarea = wrapper.get('textarea').element as HTMLTextAreaElement

    expect(textarea.value).toContain('请求体')
    expect(textarea.value).toContain('环境自动注入')
    expect(textarea.value).not.toContain('覆盖正常流程、鉴权')
    expect(textarea.value).not.toContain('生成正常、边界与鉴权')
  })

  it('shows the actionable validation reason for a failed batch', () => {
    const wrapper = mount(AiAssistant, {
      props: {
        selectedCount: 7,
        job: {
          ...job,
          state: 'failed_validation',
          batches: [{ ...job.batches[0], state: 'failed_validation', generated_draft_ids: [], validation_errors: [{ message: 'AI Gateway content is not strict JSON' }] }],
        },
      },
    })

    expect(wrapper.text()).toContain('AI 返回内容格式不正确')
    expect(wrapper.text()).toContain('重新生成当前范围')
  })

  it('translates a literal runtime value error and explains both repair paths', () => {
    const wrapper = mount(AiAssistant, {
      props: {
        selectedCount: 7,
        job: {
          ...job,
          state: 'partial',
          batches: [{
            ...job.batches[0],
            state: 'partial',
            generated_draft_ids: ['version-1'],
            validation_errors: [{
              code: 'candidate_validation_error',
              message: 'literal credential is not allowed at case.request.body.actionRequestId; use a variable placeholder',
            }],
          }],
        },
      },
    })

    expect(wrapper.text()).toContain('检测到写死的敏感值或运行时标识')
    expect(wrapper.text()).toContain('主体请求 → 请求体 → actionRequestId')
    expect(wrapper.text()).toContain('环境设置')
    expect(wrapper.text()).toContain('{{actionRequestId}}')
    expect(wrapper.text()).toContain('前置步骤')
    expect(wrapper.text()).toContain('查看英文原文')
    expect(wrapper.find('[data-testid="validation-original-message"]').text()).toContain('literal credential is not allowed')
  })

  it('offers configured Qwen analysis for an unknown rule and renders the saved diagnosis', async () => {
    const validationError = {
      code: 'candidate_validation_error',
      message: 'must constrain response fields',
    }
    const wrapper = mount(AiAssistant, {
      props: {
        selectedCount: 1,
        job: {
          ...job,
          state: 'failed_validation',
          batches: [{
            ...job.batches[0],
            state: 'failed_validation',
            generated_draft_ids: [],
            validation_errors: [validationError],
          }],
        },
      },
    })

    expect(wrapper.text()).toContain('可使用当前配置的千问分析')
    await wrapper.get('[data-testid="diagnose-validation-batch-1"]').trigger('click')
    expect(wrapper.emitted('diagnose-validation')).toEqual([['batch-1', 0]])

    await wrapper.setProps({
      job: {
        ...job,
        state: 'failed_validation',
        batches: [{
          ...job.batches[0],
          state: 'failed_validation',
          generated_draft_ids: [],
          validation_errors: [{
            ...validationError,
            diagnosis: {
              analyzer: 'ai_gateway',
              model: 'qwen3.7-plus',
              analysis: {
                summary: '响应断言范围不明确',
                root_cause: 'Schema 断言没有限定需要校验的响应字段。',
                recommendations: ['改为断言明确的 JSON 字段路径。'],
                evidence: ['平台校验规则返回 must constrain response fields'],
              },
            },
          }],
        }],
      },
    })

    expect(wrapper.text()).toContain('千问分析 · qwen3.7-plus')
    expect(wrapper.text()).toContain('响应断言范围不明确')
    expect(wrapper.text()).toContain('改为断言明确的 JSON 字段路径')
    expect(wrapper.find('[data-testid="diagnose-validation-batch-1"]').exists()).toBe(false)
  })

  it('emits a separate event for deterministic basic positive case generation', async () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 2, job: null } })

    await wrapper.get('[data-testid="generate-basic-positive"]').trigger('click')

    expect(wrapper.emitted('generate-basic')).toEqual([[]])
  })

  it('labels intent presets clearly and exposes every generated case', async () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job, generatedCases } })

    expect(wrapper.text()).toContain('意图模板')
    expect(wrapper.text()).toContain('接口合同与风险')
    expect(wrapper.text()).toContain('已生成用例 2')
    expect(wrapper.text()).toContain('正常分页')
    expect(wrapper.text()).toContain('页码边界')

    await wrapper.get('[data-testid="ai-generated-open-version-2"]').trigger('click')
    expect(wrapper.emitted('open-generated')).toEqual([[generatedCases[1]]])
  })

  it('separates an AI result restored from another endpoint scope', async () => {
    const wrapper = mount(AiAssistant, {
      props: {
        selectedCount: 2,
        job: null,
        historicalScopeMessage: '最近一次 AI 生成属于其他接口范围，已与当前 2 个接口分开显示。',
      },
    })

    expect(wrapper.text()).toContain('当前接口范围暂无对应生成结果')
    expect(wrapper.text()).toContain('已与当前 2 个接口分开显示')
    expect(wrapper.text()).not.toContain('已完成')

    await wrapper.get('[data-testid="manage-historical-ai-cases"]').trigger('click')
    expect(wrapper.emitted('manage-generated')).toEqual([[]])
  })
})

describe('AI generation advanced options', () => {
  it('keeps default requests unchanged and displays the cost notice', async () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: null, scopeKey: 'task-a' } })
    expect(wrapper.text()).toContain('可能产生费用')
    expect(wrapper.get('details[data-testid="ai-advanced"]').attributes('open')).toBeUndefined()
    await wrapper.get('[data-testid="generate-ai"]').trigger('click')
    expect(wrapper.emitted('generate')?.[0]).toHaveLength(1)
  })

  it('transmits only explicit no-thinking selection and resets on scope changes without losing intent', async () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: null, scopeKey: 'task-a' } })
    await wrapper.get('textarea').setValue('用户的测试意图')
    await wrapper.get('[data-testid="ai-thinking-mode"]').setValue('off')
    await wrapper.get('[data-testid="generate-ai"]').trigger('click')
    expect(wrapper.emitted('generate')?.[0]).toEqual(['用户的测试意图', 'off'])
    await wrapper.setProps({ scopeKey: 'task-b' })
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('用户的测试意图')
    expect((wrapper.get('[data-testid="ai-thinking-mode"]').element as HTMLSelectElement).value).toBe('default')
    await wrapper.get('[data-testid="generate-ai"]').trigger('click')
    expect(wrapper.emitted('generate')?.[1]).toEqual(['用户的测试意图'])
  })

  it('uses the selected mode only for an explicit retry', async () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: { ...job, state: 'failed_gateway', batches: [{ ...job.batches[0], state: 'failed_gateway' }] } } })
    await wrapper.get('[data-testid="ai-thinking-mode"]').setValue('off')
    const retry = wrapper.findAll('button').find(button => button.text().includes('重新生成当前范围'))!
    await retry.trigger('click')
    expect(wrapper.emitted('retry')?.[0]?.[1]).toBe('off')
    wrapper.unmount()
  })

  it('does not carry advanced mode to a different signed-in user', async () => {
    sessionStorage.setItem('sessionToken', 'account-a')
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: null, scopeKey: 'same-task' } })
    await wrapper.get('[data-testid="ai-thinking-mode"]').setValue('off')
    sessionStorage.setItem('sessionToken', 'account-b')
    await wrapper.get('[data-testid="generate-ai"]').trigger('click')
    expect(wrapper.emitted('generate')).toBeUndefined()
    expect(wrapper.text()).toContain('登录状态已变化')
    sessionStorage.removeItem('sessionToken')
    wrapper.unmount()
  })

  it('explains unsupported routes and unconfirmed modes without promising no charge', () => {
    for (const [code, expected] of [['thinking_route_unsupported','不支持关闭思考'], ['thinking_mode_unconfirmed','无法确认']]) {
      const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: {
        ...job, state: 'failed_gateway', batches: [{ ...job.batches[0], state: 'failed_gateway', error_code: code }],
      } } })
      expect(wrapper.text()).toContain(expected)
      if (code === 'thinking_route_unsupported') expect(wrapper.text()).not.toContain('模型调用失败')
      wrapper.unmount()
    }
  })
})

describe('version-protected generation diagnostics', () => {
  it('explains compatible worker timeout without calling it a model failure', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: {
      ...job, state: 'failed_gateway', batches: [{ ...job.batches[0], state: 'failed_gateway', error_code: 'compatible_worker_timeout' }],
    } } })
    expect(wrapper.text()).toContain('等待兼容执行器超时')
    expect(wrapper.text()).toContain('未发起模型调用')
    expect(wrapper.text()).not.toContain('模型调用失败')
  })
  it('warns that an interrupted generation result is uncertain and may have cost money', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: {
      ...job, state: 'failed_gateway', batches: [{ ...job.batches[0], state: 'failed_gateway', error_code: 'generation_uncertain' }],
    } } })
    expect(wrapper.text()).toContain('结果不确定')
    expect(wrapper.text()).toContain('可能产生费用')
    expect(wrapper.text()).toContain('先核对')
    expect(wrapper.text()).not.toContain('本次模型调用没有生成新草稿')
  })
  it('does not describe fallback for explicit no-thinking mode', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: null } })
    expect(wrapper.text()).toContain('不使用备用模型')
    expect(wrapper.text()).not.toContain('兼容的备用渠道')
  })
})

describe('truthful AI queue progress', () => {
  it('shows queued as waiting for a worker, not model generation', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, polling: true, job: { ...job, state: 'queued', batches: [] } } })
    expect(wrapper.text()).toContain('尚未确认执行器接收')
    expect(wrapper.text()).toContain('可能持续排队')
    expect(wrapper.text()).not.toContain('AI 正在生成')
    expect(wrapper.text()).not.toContain('生成会在后台继续')
  })
  it('does not label an unknown task status as generating', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, polling: true, job: null } })
    expect(wrapper.text()).toContain('正在查询任务状态')
    expect(wrapper.text()).not.toContain('AI 正在生成')
  })
  it('disables another generation while a pending job can be resumed', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, canResume: true, job: { ...job, state: 'queued', batches: [] } } })
    expect(wrapper.get('[data-testid="generate-ai"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('继续查看')
  })
})
