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
