// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadRunConsole from './LoadRunConsole.vue'

const run = { id: 'r1', project_id: 'p1', scenario_version_id: 'v1', environment_revision_id: 'e1', load_model: 'constant-vus' as const, queue_priority: 'normal' as const, configuration: { scenario: { name: '登录链路' }, agents: [{ id: 'a1' }, { id: 'a2' }] }, state: 'running' as const, verdict: null, stop_reason: '', ai_analysis_state: 'pending', summary: {}, created_at: '', started_at: '', finished_at: null, updated_at: '' }

describe('LoadRunConsole', () => {
  it('shows planned nodes, live events, polling fallback and requires stop confirmation', async () => {
    const wrapper = mount(LoadRunConsole, { props: { run, connectionState: 'polling', events: [{ id: 3, type: 'agent.progress', payload: { message: '分片运行中' } }] } })
    expect(wrapper.text()).toContain('计划 2 台节点')
    expect(wrapper.text()).toContain('已切换轮询')
    expect(wrapper.text()).toContain('分片运行中')
    await wrapper.get('[data-testid="load-stop"]').trigger('click')
    expect(wrapper.text()).toContain('已完成的数据会保留')
    await wrapper.get('[data-testid="load-stop-confirm"]').trigger('click')
    expect(wrapper.emitted('stop')).toHaveLength(1)
  })

  it.each(['queued', 'starting', 'running', 'stopping', 'finished', 'failed', 'cancelled'])('renders a Chinese state for %s', state => {
    const wrapper = mount(LoadRunConsole, { props: { run: { ...run, state } as never, connectionState: 'open', events: [] } })
    expect(wrapper.text()).not.toContain(`· ${state} ·`)
  })
})
