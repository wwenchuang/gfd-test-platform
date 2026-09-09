// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import LoadReportsView from './LoadReportsView.vue'

const run = { id: 'r1', project_id: 'p1', scenario_version_id: 'v1', environment_revision_id: 'e1', load_model: 'constant-arrival-rate' as const, queue_priority: 'normal' as const, configuration: { scenario: { name: '登录到模型详情' }, workload: { rate: 100, time_unit: '1s', duration_seconds: 120 }, test_context: { release: 'v30' }, agents: [{ id: 'a1' }] }, state: 'finished' as const, verdict: 'failed' as const, stop_reason: '', ai_analysis_state: 'completed', summary: {}, created_at: '', started_at: '', finished_at: '', updated_at: '' }
const report = { run_id: 'r1', verdict: 'failed' as const, verdict_label: '未通过', verdict_explanation: '目标负载已达到，但有必选性能阈值未通过。', load_goal: { reached: true, actual_iterations_per_second: 82.5 }, transport: { requests: 1000, requests_per_second: 99.8, http_error_rate: 0.01 }, business: { failure_rate: 0.02 }, workflow: { iterations: 990, failure_rate: 0.03 }, latency: { p50_ms: 80, p90_ms: 120, p95_ms: 240, p99_ms: 600, max_ms: 1000 }, evidence: { complete: true, finished_shards: 1, total_shards: 1, missing_windows: 0 }, thresholds: [{ key: 'p95_ms', label: 'P95响应时间', operator: 'less_than_or_equal', operator_label: '小于等于', expected: 200, actual: 240, passed: false }], series: [{ started_at: '08:00:00', requests: 100, p95_ms: 240 }], steps: [{ id: 'step-1', name: '查询模型详情', requests: 1000, p95_ms: 240, http_error_rate: .01, business_failure_rate: .02 }], agents: [{ id: 'a1', shard_id: 'shard-1', name: '专用节点', state: 'finished', state_label: '已完成', allocation: { vus: 8, rate: 100, scheduling_tier: 'preferred', vu_shortfall: 0 }, summary: { exit_code: 0, metric_bucket_count: 12 }, error: { message: '' } }], samples: [], comparison: { compatible: false, reason: '最近历史运行使用了不同的负载参数' } }

describe('LoadReportsView', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })
  it('prioritizes failure investigation over an unmet ramp target and includes starting pressure in peak', async () => {
    const rampRun = { ...run, load_model: 'ramping-vus' as const, configuration: { ...run.configuration, workload: { executor: 'ramping-vus', start_vus: 8, stages: [{ duration_seconds: 15, target: 2 }] } } }
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', projects: [{ id: 'p1', name: '演示服务' }] }); vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore(); store.runs = [rampRun]
    vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs); vi.spyOn(store, 'loadRun').mockResolvedValue(rampRun)
    vi.spyOn(store, 'loadReport').mockResolvedValue({ ...report, load_goal: { ...report.load_goal, reached: false } }); vi.spyOn(store, 'loadAiAnalysis').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LoadReportsView }] }); await router.push('/?run_id=r1'); await router.isReady()
    const wrapper = mount(LoadReportsView, { global: { plugins: [router] } }); await flushPromises()
    expect(wrapper.get('.load-executive-grid .next').text()).toContain('修复未通过项后保持相同条件复验')
    expect(wrapper.get('.load-executive-grid').text()).toContain('目标 峰值 8 VU')
    wrapper.unmount()
  })
  it('puts deterministic evidence before AI and keeps target attainment separate from thresholds', async () => {
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', projects: [{ id: 'p1', name: '3D家用' }] }); vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore(); store.runs = [run]
    vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs); vi.spyOn(store, 'loadRun').mockResolvedValue(run)
    vi.spyOn(store, 'loadReport').mockResolvedValue(report); vi.spyOn(store, 'loadAiAnalysis').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LoadReportsView }] }); await router.push('/?run_id=r1'); await router.isReady()
    const wrapper = mount(LoadReportsView, { global: { plugins: [router] } }); await flushPromises()
    expect(wrapper.text()).toContain('目标压力')
    expect(wrapper.text()).toContain('已达到')
    expect(wrapper.text()).toContain('P95响应时间')
    expect(wrapper.text()).toContain('未通过')
    expect(wrapper.text()).toContain('P50')
    expect(wrapper.text()).toContain('95% 的请求')
    expect(wrapper.text()).toContain('接口与步骤统计')
    expect(wrapper.get('.report-table-scroll tbody').text()).toContain('查询模型详情')
    expect(wrapper.get('.report-table-scroll tbody').text()).toContain('1.00%')
    expect(wrapper.text()).toContain('毫秒')
    expect(wrapper.text()).toContain('HTTP 错误率')
    expect(wrapper.text()).toContain('业务失败率')
    expect(wrapper.text()).toContain('完整链路失败率')
    expect(wrapper.text()).toContain('历史运行不可直接对比')
    expect(wrapper.text()).toContain('分配压力')
    expect(wrapper.text()).toContain('8 VU · 100 次/秒')
    expect(wrapper.text()).toContain('指标窗口')
    expect(wrapper.text()).toContain('12')
    expect(wrapper.text()).toContain('管理层摘要')
    expect(wrapper.find('.load-metric-guide').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('压测使用帮助')
    await wrapper.get('[data-testid="load-report-history-toggle"]').trigger('click')
    expect(wrapper.get('[data-testid="report-run-r1"]').text()).toContain('已完成')
    expect(wrapper.get('[data-testid="report-run-r1"]').text()).not.toContain('finished')
    expect(wrapper.text()).not.toContain('节点执行失败')
    expect(wrapper.text()).toContain('历史报告未记录采样计数校验')
    const executive = wrapper.get('[data-testid="load-report-executive-grid"]')
    expect(executive.text()).toContain('测了什么')
    expect(executive.text()).toContain('3D家用 · 登录到模型详情')
    expect(executive.text()).toContain('版本 v30')
    expect(executive.text()).toContain('环境名称未记录')
    expect(executive.text()).not.toContain('环境版本 e1')
    expect(executive.text()).toContain('怎么测的')
    expect(executive.text()).toContain('固定吞吐 · 目标 100 次/秒 · 120 秒')
    expect(executive.text()).toContain('实际完整链路 82.5 次/秒')
    expect(executive.text()).toContain('HTTP 99.8 RPS · 1000 次请求')
    expect(executive.text()).toContain('结果怎么样')
    expect(executive.text()).toContain('主要风险')
    expect(executive.text()).toContain('P95响应时间')
    expect(executive.text()).toContain('未采集被测服务资源')
    expect(executive.text()).toContain('下一步做什么')
    expect(executive.text()).toContain('修复未通过项后保持相同条件复验')
    expect(wrapper.find('[aria-label="AI性能诊断"]').exists()).toBe(true)
    expect(wrapper.element.compareDocumentPosition(wrapper.get('[aria-label="AI性能诊断"]').element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shows the live console for a running task and opens the SSE workflow', async () => {
    const active = { ...run, state: 'running' as const, verdict: null }
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', projects: [{ id: 'p1', name: '3D家用' }] }); vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore(); store.runs = [active]
    vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs); vi.spyOn(store, 'loadRun').mockResolvedValue(active); vi.spyOn(store, 'connectRunEvents').mockResolvedValue()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LoadReportsView }] }); await router.push('/?run_id=r1'); await router.isReady()
    const wrapper = mount(LoadReportsView, { global: { plugins: [router] } }); await flushPromises()
    expect(wrapper.find('[aria-label="压测实时控制台"]').exists()).toBe(true)
    expect(store.connectRunEvents).toHaveBeenCalledWith('r1')
    expect(wrapper.find('[data-testid="load-report-sample-integrity"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('采样计数不一致')
  })

  it('labels ramping VU evidence as concurrency instead of request starts', async () => {
    const rampRun = {
      ...run,
      load_model: 'ramping-vus' as const,
      configuration: {
        ...run.configuration,
        workload: { executor: 'ramping-vus', start_vus: 1, stages: [{ duration_seconds: 15, target: 2 }] },
      },
    }
    const rampReport = {
      ...report,
      load_goal: {
        model: 'ramping-vus',
        reached: true,
        explanation: '实际并发已覆盖完整阶梯。',
        vu_evidence: { sustained_seconds: null },
        stages: [{
          index: 1,
          duration_seconds: 15,
          start_vus: 1,
          target_vus: 2,
          planned_average_vus: 1.5,
          actual_average_vus: 1.6,
          reached: true,
        }],
      },
    }
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', projects: [{ id: 'p1', name: '3D家用' }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore(); store.runs = [rampRun]
    vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs); vi.spyOn(store, 'loadRun').mockResolvedValue(rampRun)
    vi.spyOn(store, 'loadReport').mockResolvedValue(rampReport); vi.spyOn(store, 'loadAiAnalysis').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LoadReportsView }] }); await router.push('/?run_id=r1'); await router.isReady()

    const wrapper = mount(LoadReportsView, { global: { plugins: [router] } }); await flushPromises()

    const stages = wrapper.get('[data-testid="load-report-stages"]')
    expect(stages.text()).toContain('阶梯并发阶段证据')
    expect(stages.text()).toContain('1 → 2 VU')
    expect(stages.text()).toContain('1.6 VU')
    expect(stages.text()).not.toContain('计划迭代')
    expect(wrapper.text()).not.toContain('实际并发达标持续时间：—')
  })

  it.each([
    { evidence: { ...report.evidence, complete: true, sample_integrity: { consistent: false, acceptable: true, tolerance: { max_count: 2, max_ratio: 0.1 }, scope: 'shard_step', mismatches: [{ shard_id: 'shard-1', step_id: 'step-1', requests: 21, latency_samples: 19 }] } }, expected: '少量采样偏差（容差内）', detail: '允许继续判定' },
    { evidence: { ...report.evidence, complete: false, sample_integrity: { consistent: false, scope: 'shard_step', mismatches: [{ shard_id: 'shard-1', step_id: 'step-1', requests: 21, latency_samples: 19 }] } }, expected: '采样计数不一致', detail: '请求 21 次 / 耗时样本 19 条' },
    { evidence: { ...report.evidence, complete: false, finished_shards: 0 }, expected: '部分节点未正常完成', detail: '' },
    { evidence: { ...report.evidence, complete: false }, expected: '证据未通过完整性检查', detail: '' },
  ])('explains incomplete evidence without inventing missing windows: $expected', async ({ evidence, expected, detail }) => {
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', projects: [{ id: 'p1', name: '3D家用' }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore(); store.runs = [run]
    vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs); vi.spyOn(store, 'loadRun').mockResolvedValue(run)
    vi.spyOn(store, 'loadReport').mockResolvedValue({ ...report, verdict: 'inconclusive', evidence }); vi.spyOn(store, 'loadAiAnalysis').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LoadReportsView }] }); await router.push('/?run_id=r1'); await router.isReady()
    const wrapper = mount(LoadReportsView, { global: { plugins: [router] } }); await flushPromises()
    expect(wrapper.get('[data-testid="load-report-decision-hero"]').text()).toContain(expected)
    expect(wrapper.text()).not.toContain('0 个时段缺失')
    expect(wrapper.text()).not.toContain('结果可用于判定')
    if (detail) {
      expect(wrapper.get('[data-testid="load-report-sample-integrity"]').text()).toContain(detail)
      expect(wrapper.get('[data-testid="load-report-sample-integrity"]').text()).toContain('查询模型详情')
      expect(wrapper.get('[data-testid="load-report-sample-integrity"]').text()).toContain('专用节点')
    }
    wrapper.unmount()
  })

  it('keeps history collapsed above the report and filters it by application on demand', async () => {
    const otherRun = { ...run, id: 'r2', project_id: 'p2', configuration: { scenario: { name: '共享商城搜索' }, agents: [] } }
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', projects: [{ id: 'p1', name: '3D家用' }, { id: 'p2', name: '共享商城' }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore(); store.runs = [run, otherRun]
    const loadRuns = vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs)
    vi.spyOn(store, 'loadRun').mockImplementation(async id => id === 'r2' ? otherRun : run)
    vi.spyOn(store, 'loadReport').mockResolvedValue(report); vi.spyOn(store, 'loadAiAnalysis').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LoadReportsView }] }); await router.push('/?run_id=r1'); await router.isReady()
    const wrapper = mount(LoadReportsView, { global: { plugins: [router] } }); await flushPromises()

    expect(loadRuns).toHaveBeenCalledWith(undefined)
    expect(wrapper.find('[data-testid="load-report-history-list"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="load-report-decision-hero"]').text()).toContain('性能决策简报')
    await wrapper.get('[data-testid="load-report-history-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="load-report-history-list"]').exists()).toBe(true)
    expect((wrapper.get('[data-testid="load-report-application"]').element as HTMLSelectElement).value).toBe('p1')
    expect(wrapper.find('[data-testid="report-run-r1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="report-run-r2"]').exists()).toBe(false)
    await wrapper.get('[data-testid="load-report-application"]').setValue('p2')
    expect(wrapper.find('[data-testid="report-run-r1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="report-run-r2"]').exists()).toBe(true)
  })

  it('keeps the selected report in the URL so refresh, copy and browser history open the same evidence', async () => {
    const otherRun = { ...run, id: 'r2', configuration: { scenario: { name: '模型详情二次验证' }, agents: [] } }
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', projects: [{ id: 'p1', name: '3D家用' }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore(); store.runs = [run, otherRun]
    vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs)
    vi.spyOn(store, 'loadRun').mockImplementation(async id => id === 'r2' ? otherRun : run)
    vi.spyOn(store, 'loadReport').mockResolvedValue(report); vi.spyOn(store, 'loadAiAnalysis').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: LoadReportsView }] }); await router.push('/?run_id=r1'); await router.isReady()
    const wrapper = mount(LoadReportsView, { global: { plugins: [router] } }); await flushPromises()

    await wrapper.get('[data-testid="load-report-history-toggle"]').trigger('click')
    await wrapper.get('[data-testid="report-run-r2"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.run_id).toBe('r2')
    expect(wrapper.text()).toContain('模型详情二次验证')
  })
})
