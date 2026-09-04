// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import LoadReportsView from './LoadReportsView.vue'

const run = { id: 'r1', project_id: 'p1', scenario_version_id: 'v1', environment_revision_id: 'e1', load_model: 'constant-arrival-rate' as const, queue_priority: 'normal' as const, configuration: { scenario: { name: '登录到模型详情' }, agents: [{ id: 'a1' }] }, state: 'finished' as const, verdict: 'failed' as const, stop_reason: '', ai_analysis_state: 'completed', summary: {}, created_at: '', started_at: '', finished_at: '', updated_at: '' }
const report = { run_id: 'r1', verdict: 'failed' as const, verdict_label: '未通过', verdict_explanation: '目标负载已达到，但有必选性能阈值未通过。', load_goal: { reached: true }, transport: { requests: 1000, requests_per_second: 99.8, http_error_rate: 0.01 }, business: { failure_rate: 0.02 }, workflow: { failure_rate: 0.03 }, latency: { p50_ms: 80, p90_ms: 120, p95_ms: 240, p99_ms: 600, max_ms: 1000 }, evidence: { complete: true, finished_shards: 1, total_shards: 1, missing_windows: 0 }, thresholds: [{ key: 'p95_ms', label: 'P95响应时间', operator_label: '小于等于', expected: 200, actual: 240, passed: false }], series: [{ started_at: '08:00:00', requests: 100, p95_ms: 240 }], steps: [], agents: [{ id: 'a1', name: '专用节点', state: 'finished', state_label: '已完成', allocation: { vus: 8, rate: 100, scheduling_tier: 'preferred', vu_shortfall: 0 }, summary: { exit_code: 0, metric_bucket_count: 12 }, error: { message: '' } }], samples: [], comparison: { compatible: false, reason: '最近历史运行使用了不同的负载参数' } }

describe('LoadReportsView', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })
  it('puts deterministic evidence before AI and keeps target attainment separate from thresholds', async () => {
    const context = useContextStore(); Object.assign(context, { projectId: 'p1' }); vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    const store = useLoadTestingStore(); store.runs = [run]
    vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs); vi.spyOn(store, 'loadRun').mockResolvedValue(run)
    vi.spyOn(store, 'loadReport').mockResolvedValue(report); vi.spyOn(store, 'loadAiAnalysis').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LoadReportsView }] }); await router.push('/?run_id=r1'); await router.isReady()
    const wrapper = mount(LoadReportsView, { global: { plugins: [router] } }); await flushPromises()
    expect(wrapper.text()).toContain('负载目标')
    expect(wrapper.text()).toContain('已达到')
    expect(wrapper.text()).toContain('P95响应时间')
    expect(wrapper.text()).toContain('未通过')
    expect(wrapper.text()).toContain('P50')
    expect(wrapper.text()).toContain('HTTP 错误率')
    expect(wrapper.text()).toContain('业务失败率')
    expect(wrapper.text()).toContain('完整链路失败率')
    expect(wrapper.text()).toContain('历史运行不可直接对比')
    expect(wrapper.text()).toContain('分配压力')
    expect(wrapper.text()).toContain('8 VU · 100 次/秒')
    expect(wrapper.text()).toContain('指标窗口')
    expect(wrapper.text()).toContain('12')
    expect(wrapper.text()).toContain('管理层摘要')
    expect(wrapper.get('[data-testid="report-run-r1"]').text()).toContain('已完成')
    expect(wrapper.get('[data-testid="report-run-r1"]').text()).not.toContain('finished')
    expect(wrapper.text()).not.toContain('节点执行失败')
    expect(wrapper.find('[aria-label="AI性能诊断"]').exists()).toBe(true)
    expect(wrapper.element.compareDocumentPosition(wrapper.get('[aria-label="AI性能诊断"]').element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shows the live console for a running task and opens the SSE workflow', async () => {
    const active = { ...run, state: 'running' as const, verdict: null }
    const context = useContextStore(); Object.assign(context, { projectId: 'p1' }); vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    const store = useLoadTestingStore(); store.runs = [active]
    vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs); vi.spyOn(store, 'loadRun').mockResolvedValue(active); vi.spyOn(store, 'connectRunEvents').mockResolvedValue()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LoadReportsView }] }); await router.push('/?run_id=r1'); await router.isReady()
    const wrapper = mount(LoadReportsView, { global: { plugins: [router] } }); await flushPromises()
    expect(wrapper.find('[aria-label="压测实时控制台"]').exists()).toBe(true)
    expect(store.connectRunEvents).toHaveBeenCalledWith('r1')
  })
})
