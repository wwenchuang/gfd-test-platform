// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadAiAnalysis from './LoadAiAnalysis.vue'

describe('LoadAiAnalysis', () => {
  it('shows evidence citations, low confidence and explains reanalysis does not rerun load', async () => {
    const analysis = { id: 'a1', run_id: 'r1', model: 'qwen-plus', prompt_version: 'v1', evidence_hash: 'abcdef1234567890', state: 'completed', error: '', created_at: '', result: { conclusion: '瓶颈在目标服务', bottleneck_category: 'target_service', evidence: ['latency.summary', 'agent.a1'], confidence: { level: 'low', reason: '窗口较少' }, recommendations: [{ priority: 'high', action: '检查慢查询', verification: '复跑相同负载' }], next_run: { load_model: 'constant-arrival-rate', target: 20, duration_seconds: 120, agent_suggestion: '继续使用专用节点' } } }
    const wrapper = mount(LoadAiAnalysis, { props: { analysis } })
    expect(wrapper.text()).toContain('不会重新执行压测')
    expect(wrapper.text()).toContain('低置信度')
    expect(wrapper.text()).toContain('latency.summary')
    expect(wrapper.text()).toContain('疑似目标服务瓶颈')
    expect(wrapper.text()).toContain('高优先级')
    expect(wrapper.text()).toContain('检查慢查询')
    expect(wrapper.text()).toContain('固定吞吐')
    expect(wrapper.text()).toContain('目标 20')
    expect(wrapper.text()).toContain('继续使用专用节点')
    await wrapper.get('[data-testid="load-reanalyze"]').trigger('click')
    expect(wrapper.emitted('reanalyze')).toHaveLength(1)
  })

  it('keeps AI failure separate from the deterministic report', () => {
    const analysis = { id: 'a1', run_id: 'r1', model: 'qwen-plus', prompt_version: 'v1', evidence_hash: 'e', state: 'failed', error: 'AI诊断超时，请稍后重试', created_at: '', result: {} }
    const wrapper = mount(LoadAiAnalysis, { props: { analysis } })
    expect(wrapper.text()).toContain('AI诊断超时')
  })
})
