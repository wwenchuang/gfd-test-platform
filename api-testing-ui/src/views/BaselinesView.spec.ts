// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import { useBaselinesStore } from '../stores/baselines'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'
import { useTasksStore } from '../stores/tasks'
import { replaceTestApplications } from '../utils/testApplications'
import BaselinesView from './BaselinesView.vue'

function mountWithContext(): ReturnType<typeof mount> {
  return mountWithContextAndRouter().wrapper
}

function mountWithContextAndRouter() {
  const context = useContextStore()
  Object.assign(context, {
    projectId: 'project-1',
    sourceRevisionId: 'source-v2',
    environmentRevisionId: 'env-v9',
    projects: [{ id: 'project-1', name: '3D 家用' }],
    sourceRevisions: [
      { id: 'source-v1', project_id: 'project-1', name: '默认模块', revision_number: 1, endpoint_count: 962 },
      { id: 'source-v2', project_id: 'project-1', name: '默认模块', revision_number: 2, endpoint_count: 999 },
    ],
    environmentRevisions: [
      { id: 'env-v6', project_id: 'project-1', name: '生产环境（新）- 腾讯云', revision: 6 },
      { id: 'env-v9', project_id: 'project-1', name: '生产环境（新）- 腾讯云', revision: 9 },
    ],
  })
  vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
  vi.spyOn(context, 'loadOptions').mockResolvedValue()
  vi.spyOn(context, 'saveContext').mockResolvedValue()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'baselines', component: BaselinesView },
      { path: '/runs', name: 'runs', component: { template: '<div />' } },
      { path: '/workbench', name: 'workbench', component: { template: '<div />' } },
    ],
  })

  const wrapper = mount(BaselinesView, {
    global: {
      plugins: [router],
      stubs: {
        ContextBar: {
          emits: ['update:sourceRevisionId', 'update:environmentRevisionId'],
          template: `
            <div>
              <button data-testid="switch-source" @click="$emit('update:sourceRevisionId', 'source-v1')">切接口版本</button>
              <button data-testid="switch-environment" @click="$emit('update:environmentRevisionId', 'env-v6')">切执行环境</button>
            </div>
          `,
        },
      },
    },
  })
  return { wrapper, router }
}

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find(item => item.text().includes(text))
  expect(button, `button ${text}`).toBeTruthy()
  return button!
}

function baselineFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'baseline-1',
    project_id: 'project-1',
    case_id: 'case-1',
    case_version_id: 'version-1',
    environment_revision_id: 'env-v6',
    source_revision_id: 'source-v1',
    endpoint_id: 'endpoint-1',
    status: 'active',
    case_name: '添加收藏 - 正常流程',
    case_version: 2,
    priority: 'P0',
    app_package: 'com.example.school',
    app_name: '校园应用旧名称',
    business: 'shared',
    origin: 'ai',
    method: 'POST',
    path: '/print3d/api/v1/collection/add',
    endpoint_summary: '添加修改收藏',
    tags: ['我的收藏'],
    group_name: '我的收藏',
    adoption_reason: 'passing debug evidence',
    adopted_at: '2026-08-12T08:16:43Z',
    ...overrides,
  }
}

describe('BaselinesView fixed project assets', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    replaceTestApplications([{
      package: 'com.example.school', name: '校园应用', enabled: true,
      business_lines: [{ id: 'shared', name: '校园共享', enabled: true }],
    }])
  })

  it('keeps project baselines visible and selected when source or environment changes', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture(),
    ] } })

    const wrapper = mountWithContext()
    await flushPromises()

    expect(wrapper.text()).toContain('添加收藏 - 正常流程')
    expect(wrapper.text()).toContain('校园应用 · 校园共享')
    expect(wrapper.text()).not.toContain('com.example.school')
    expect(wrapper.text()).toContain('已通过调试并采纳')
    expect(wrapper.text()).not.toContain('passing debug evidence')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('[data-testid="switch-source"]').trigger('click')
    await wrapper.get('[data-testid="switch-environment"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('添加收藏 - 正常流程')
    expect(wrapper.text()).toContain('来源版本')
    expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(true)
    expect(vi.mocked(apiClient.get).mock.calls.filter(([path]) => path.includes('/baselines'))).toHaveLength(1)
  })

  it('opens a historical baseline in its own source and environment context', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture({
        endpoint_id: 'endpoint-v1',
        source_revision_id: 'source-v1',
        environment_revision_id: 'env-v6',
        case_version_id: 'case-version-v1',
      }),
    ] } })
    const { wrapper, router } = mountWithContextAndRouter()
    await flushPromises()

    await wrapper.get('button[title="编辑用例"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value).toMatchObject({
      name: 'workbench',
      query: {
        projectId: 'project-1',
        sourceRevisionId: 'source-v1',
        environmentRevisionId: 'env-v6',
        endpointId: 'endpoint-v1',
        caseVersionId: 'case-version-v1',
      },
    })
  })

  it('renames and deletes a custom baseline group without hiding its cases', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture({ id: 'baseline-1', group_name: '收藏链路', case_name: '添加收藏 - 正常流程' }),
      baselineFixture({ id: 'baseline-2', endpoint_id: 'endpoint-2', group_name: '收藏链路', case_name: '取消收藏 - 正常流程' }),
      baselineFixture({ id: 'baseline-3', endpoint_id: 'endpoint-3', group_name: '登录鉴权', case_name: '登录 - 正常流程' }),
    ] } })
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { baselines: [
        { id: 'baseline-1', group_name: '发版冒烟' },
        { id: 'baseline-2', group_name: '发版冒烟' },
      ] } })
      .mockResolvedValueOnce({ data: { baselines: [
        { id: 'baseline-1', group_name: '未分组' },
        { id: 'baseline-2', group_name: '未分组' },
      ] } })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mountWithContext()
    await flushPromises()

    await buttonByText(wrapper, '收藏链路').trigger('click')
    await wrapper.get('.baseline-group-editor input').setValue('发版冒烟')
    await buttonByText(wrapper, '重命名分组').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/baselines/bulk-group', {
      baseline_ids: ['baseline-1', 'baseline-2'],
      group_name: '发版冒烟',
    })
    expect(wrapper.text()).toContain('发版冒烟')
    expect(wrapper.text()).toContain('添加收藏 - 正常流程')

    await buttonByText(wrapper, '删除分组').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledWith('删除分组“发版冒烟”？分组内基线会保留，并移回“未分组”。')
    expect(post).toHaveBeenLastCalledWith('/api/api-testing/v1/baselines/bulk-group', {
      baseline_ids: ['baseline-1', 'baseline-2'],
      group_name: '未分组',
    })
    expect(wrapper.text()).toContain('未分组')
    expect(wrapper.text()).toContain('添加收藏 - 正常流程')
  })

  it('moves selected baselines to an existing group', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture({ id: 'baseline-1', group_name: '未分组', case_name: '添加收藏 - 正常流程' }),
      baselineFixture({ id: 'baseline-2', endpoint_id: 'endpoint-2', group_name: '收藏链路', case_name: '取消收藏 - 正常流程' }),
      baselineFixture({ id: 'baseline-3', endpoint_id: 'endpoint-3', group_name: '登录鉴权', case_name: '登录 - 正常流程' }),
    ] } })
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { baselines: [
      { id: 'baseline-1', group_name: '登录鉴权' },
    ] } })

    const wrapper = mountWithContext()
    await flushPromises()

    await buttonByText(wrapper, '未分组').trigger('click')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('[data-testid="baseline-move-target"]').setValue('登录鉴权')
    await wrapper.get('[data-testid="baseline-move-selected"]').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/baselines/bulk-group', {
      baseline_ids: ['baseline-1'],
      group_name: '登录鉴权',
    })
    expect(wrapper.text()).toContain('已将 1 条基线移动到“登录鉴权”')
  })

  it('saves selected baselines as a regression task without saving workspace context', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture(),
    ] } })

    const wrapper = mountWithContext()
    const context = useContextStore()
    const tasks = useTasksStore()
    const createSelection = vi.spyOn(tasks, 'createSelection').mockResolvedValue({ id: 'task-1' } as never)
    const saveSelection = vi.spyOn(tasks, 'saveSelection')
    await flushPromises()

    await wrapper.get('input[type="checkbox"]').setValue(true)
    await buttonByText(wrapper, '保存为基线回归任务').trigger('click')
    await flushPromises()

    expect(context.saveContext).not.toHaveBeenCalled()
    expect(createSelection).toHaveBeenCalledWith({
      projectId: 'project-1',
      sourceRevisionId: 'source-v1',
      environmentRevisionId: 'env-v9',
    }, ['endpoint-1'], '3D 家用基线回归')
    expect(saveSelection).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('已保存基线回归任务：1 条基线')
  })

  it('runs selected baselines with current environment without saving workspace context', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture(),
    ] } })

    const wrapper = mountWithContext()
    const context = useContextStore()
    const executions = useExecutionsStore()
    const runBaselines = vi.spyOn(executions, 'runBaselines').mockResolvedValue({ id: 'execution-1' } as never)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await flushPromises()

    await wrapper.get('input[type="checkbox"]').setValue(true)
    await buttonByText(wrapper, '按当前环境执行所选基线').trigger('click')
    await flushPromises()

    expect(runBaselines).not.toHaveBeenCalled()
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/生产环境.*1 条.*真实发送/))

    confirm.mockReturnValue(true)
    await buttonByText(wrapper, '按当前环境执行所选基线').trigger('click')
    await flushPromises()

    expect(context.saveContext).not.toHaveBeenCalled()
    expect(runBaselines).toHaveBeenCalledWith({
      projectId: 'project-1',
      sourceRevisionId: 'source-v1',
      environmentRevisionId: 'env-v9',
      baselineIds: ['baseline-1'],
    })
  })

  it('shows readable error when selected baselines come from multiple source revisions', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture({ id: 'baseline-1', endpoint_id: 'endpoint-1', source_revision_id: 'source-v1' }),
      baselineFixture({ id: 'baseline-2', endpoint_id: 'endpoint-2', source_revision_id: 'source-v2', case_name: '取消收藏 - 正常流程' }),
    ] } })

    const wrapper = mountWithContext()
    const executions = useExecutionsStore()
    const runBaselines = vi.spyOn(executions, 'runBaselines').mockResolvedValue({ id: 'execution-1' } as never)
    await flushPromises()

    const boxes = wrapper.findAll('input[type="checkbox"]')
    await boxes[0].setValue(true)
    await boxes[1].setValue(true)
    await buttonByText(wrapper, '按当前环境执行所选基线').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('所选基线来自多个接口版本，请按来源版本分批保存或执行')
    expect(runBaselines).not.toHaveBeenCalled()
  })

  it('does not show stale workbench task errors on the baseline page', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture(),
    ] } })

    const wrapper = mountWithContext()
    const tasks = useTasksStore()
    tasks.error = '测试任务范围与当前请求不一致'
    await flushPromises()

    expect(wrapper.text()).not.toContain('测试任务范围与当前请求不一致')
  })

  it('combines baseline type, method, priority and origin filters without disabling one-time cases', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture({ id: 'baseline-regular-ai', method: 'POST', priority: 'P0', origin: 'ai', group_name: '收藏链路' }),
      baselineFixture({ id: 'baseline-one-time', method: 'POST', priority: 'P1', origin: 'manual', group_name: 'API Test / 一次性', case_name: '重新打印 - 一次性验证' }),
      baselineFixture({ id: 'baseline-get', method: 'GET', priority: 'P1', origin: 'imported', group_name: '查询链路', case_name: '收藏列表' }),
    ] } })
    const wrapper = mountWithContext()
    await flushPromises()

    await wrapper.get('[data-testid="baseline-filter-type"]').setValue('one-time')
    expect(wrapper.text()).toContain('重新打印 - 一次性验证')
    expect(wrapper.text()).not.toContain('收藏列表')
    expect(wrapper.get('[data-testid="baseline-one-time-baseline-one-time"]').text()).toBe('一次性')

    await wrapper.get('[data-testid="baseline-filter-method"]').setValue('POST')
    await wrapper.get('[data-testid="baseline-filter-priority"]').setValue('P1')
    await wrapper.get('[data-testid="baseline-filter-origin"]').setValue('manual')
    await buttonByText(wrapper, '全选当前筛选').trigger('click')

    const oneTimeCheckbox = wrapper.get('[data-testid="baseline-select-baseline-one-time"]')
    expect((oneTimeCheckbox.element as HTMLInputElement).checked).toBe(true)
    expect(oneTimeCheckbox.attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-testid="baseline-filter-origin"]').setValue('ai')
    expect(wrapper.text()).toContain('当前筛选下没有匹配基线')
  })

  it('does not classify an ordinary API Test group as one-time', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture({ id: 'baseline-api-test', group_name: 'API Test / 收藏回归', case_name: '收藏接口常规回归' }),
    ] } })
    const wrapper = mountWithContext()
    await flushPromises()

    expect(wrapper.find('[data-testid="baseline-one-time-baseline-api-test"]').exists()).toBe(false)
    await wrapper.get('[data-testid="baseline-filter-type"]').setValue('regular')
    expect(wrapper.text()).toContain('收藏接口常规回归')
  })

  it('checks stored response evidence on demand and selects only safe review candidates', async () => {
    const get = vi.spyOn(apiClient, 'get').mockImplementation(async path => {
      if (path.includes('assertion-audit')) {
        return { data: {
          summary: {
            total: 4,
            verified: 1,
            upgrade_available: 2,
            http_failure: 0,
            business_failure: 0,
            domain_assertion_required: 0,
            evidence_missing: 1,
            needs_review: 3,
            safe_review: 2,
          },
          items: [
            {
              baseline_id: 'baseline-safe', case_id: 'case-safe', case_version_id: 'version-safe', endpoint_id: 'endpoint-safe',
              case_name: '收藏查询成功', method: 'GET', path: '/collection/page', group_name: '收藏链路', environment_revision_id: 'env-v9',
              evidence_execution_case_id: 'execution-safe', evidence_captured_at: '2026-08-27T10:00:00Z',
              status: 'upgrade_available', status_label: '可补精确断言', reason: '实际响应为业务成功，可在新版本补充精确业务断言后重新调试',
              actual_http_status: 200, business_path: '$.code', business_value: 0,
              suggested_assertions: [{ type: 'json_path', operator: 'equals', expected: 0, path: '$.code', enabled: true }],
              execution: { level: 'direct', label: '可直接复核', selectable: true, reason: '只读接口，可安全批量复核' },
            },
            {
              baseline_id: 'baseline-other-env', case_id: 'case-other-env', case_version_id: 'version-other-env', endpoint_id: 'endpoint-other-env',
              case_name: '旧环境列表查询', method: 'GET', path: '/collection/page', group_name: '收藏链路', environment_revision_id: 'env-v6',
              evidence_execution_case_id: 'execution-other-env', evidence_captured_at: '2026-08-27T10:00:30Z',
              status: 'upgrade_available', status_label: '可补精确断言', reason: '实际响应为业务成功，可在新版本补充精确业务断言后重新调试',
              actual_http_status: 200, business_path: '$.code', business_value: 0,
              suggested_assertions: [{ type: 'json_path', operator: 'equals', expected: 0, path: '$.code', enabled: true }],
              execution: { level: 'direct', label: '可直接复核', selectable: true, reason: '只读接口，可安全批量复核' },
            },
            {
              baseline_id: 'baseline-verified', case_id: 'case-verified', case_version_id: 'version-verified', endpoint_id: 'endpoint-verified',
              case_name: '列表查询', method: 'GET', path: '/collection/page', group_name: '收藏链路', environment_revision_id: 'env-v6',
              evidence_execution_case_id: 'execution-verified', evidence_captured_at: '2026-08-27T10:01:00Z',
              status: 'verified', status_label: '断言已精确', reason: '精确业务断言与实际响应一致',
              actual_http_status: 200, business_path: '$.code', business_value: 0, suggested_assertions: [],
              execution: { level: 'direct', label: '可直接复核', selectable: true, reason: '只读接口，可安全批量复核' },
            },
            {
              baseline_id: 'baseline-manual', case_id: 'case-manual', case_version_id: 'version-manual', endpoint_id: 'endpoint-manual',
              case_name: '重新打印 - 一次性验证', method: 'POST', path: '/print/retry', group_name: '一次性', environment_revision_id: 'env-v6',
              evidence_execution_case_id: null, evidence_captured_at: null,
              status: 'evidence_missing', status_label: '证据不足', reason: '缺少可解析的历史调试响应，需要重新执行后判断',
              actual_http_status: null, business_path: '', business_value: null, suggested_assertions: [],
              execution: { level: 'manual', label: '一次性人工复核', selectable: false, reason: '一次性基线不得进入批量连续执行' },
            },
          ],
        } } as never
      }
      return { data: { baselines: [
        baselineFixture({ id: 'baseline-safe', endpoint_id: 'endpoint-safe', case_id: 'case-safe', case_version_id: 'version-safe', case_name: '收藏查询成功', method: 'GET', environment_revision_id: 'env-v9' }),
        baselineFixture({ id: 'baseline-other-env', endpoint_id: 'endpoint-other-env', case_id: 'case-other-env', case_version_id: 'version-other-env', case_name: '旧环境列表查询', method: 'GET', environment_revision_id: 'env-v6' }),
        baselineFixture({ id: 'baseline-verified', endpoint_id: 'endpoint-verified', case_id: 'case-verified', case_version_id: 'version-verified', case_name: '列表查询', method: 'GET' }),
        baselineFixture({ id: 'baseline-manual', endpoint_id: 'endpoint-manual', case_id: 'case-manual', case_version_id: 'version-manual', case_name: '重新打印 - 一次性验证', group_name: '一次性' }),
      ] } } as never
    })
    const wrapper = mountWithContext()
    await flushPromises()

    expect(get.mock.calls.filter(([path]) => path.includes('assertion-audit'))).toHaveLength(0)
    await buttonByText(wrapper, '检查断言').trigger('click')
    await flushPromises()

    expect(get).toHaveBeenCalledWith('/api/api-testing/v1/baselines/assertion-audit?project_id=project-1')
    expect(wrapper.text()).toContain('需要复核 3 条')
    expect(wrapper.text()).toContain('当前环境可安全复核 1 条')
    expect(wrapper.text()).toContain('可补精确断言 2 条')
    expect(wrapper.text()).toContain('HTTP 失败 0 条')
    expect(wrapper.text()).toContain('业务失败 0 条')
    expect(wrapper.text()).toContain('缺少领域断言 0 条')
    expect(wrapper.text()).toContain('证据不足 1 条')
    expect(wrapper.text()).toContain('可补精确断言')
    expect(wrapper.text()).toContain('实际响应：HTTP 200 · $.code = 0')
    expect(wrapper.text()).toContain('一次性人工复核')

    await buttonByText(wrapper, '选择可安全复核项').trigger('click')
    expect(useBaselinesStore().selectedIds).toEqual(['baseline-safe'])

    await wrapper.get('[data-testid="switch-environment"]').trigger('click')
    await flushPromises()
    expect(buttonByText(wrapper, '保存为基线回归任务').attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '按当前环境执行所选基线').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('所选复核基线的审计证据环境与当前执行环境不一致')
  })

  it('creates an assertion review draft and opens it in the original baseline context', async () => {
    vi.spyOn(apiClient, 'get').mockImplementation(async path => {
      if (path.includes('assertion-audit')) return { data: {
        summary: {
          total: 1, verified: 0, upgrade_available: 1, http_failure: 0, business_failure: 0,
          domain_assertion_required: 0, evidence_missing: 0, needs_review: 1, safe_review: 1,
        },
        items: [{
          baseline_id: 'baseline-1', case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1',
          case_name: '添加收藏 - 正常流程', method: 'POST', path: '/collection/add', group_name: '我的收藏',
          environment_revision_id: 'env-v6', evidence_execution_case_id: 'execution-1', evidence_captured_at: '2026-08-27T10:00:00Z',
          status: 'upgrade_available', status_label: '可补精确断言', reason: '可补充精确业务断言',
          actual_http_status: 200, business_path: '$.code', business_value: 0,
          suggested_assertions: [{ type: 'json_path', operator: 'equals', expected: 0, path: '$.code', enabled: true }],
          upgrade_draft_case_version_id: null,
          execution: { level: 'manual', label: '需人工复核', selectable: false, reason: '写操作需逐条复核' },
        }],
      } } as never
      return { data: { baselines: [baselineFixture()] } } as never
    })
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {
      case_version: { id: 'version-review', case_id: 'case-1', version: 3 },
      source_baseline_id: 'baseline-1', source_case_version_id: 'version-1', suggestion_count: 1,
    } })
    const { wrapper, router } = mountWithContextAndRouter()
    await router.isReady()
    await flushPromises()

    await buttonByText(wrapper, '检查断言').trigger('click')
    await flushPromises()
    await buttonByText(wrapper, '生成待复核版本').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/baselines/baseline-1/assertion-upgrade-draft', {})
    expect(router.currentRoute.value).toMatchObject({
      name: 'workbench',
      query: {
        projectId: 'project-1', sourceRevisionId: 'source-v1', environmentRevisionId: 'env-v6',
        endpointId: 'endpoint-1', caseVersionId: 'version-review',
      },
    })
  })

  it('clears an audit-only filter when baselines are refreshed', async () => {
    const baseline = baselineFixture({ id: 'baseline-review' })
    vi.spyOn(apiClient, 'get').mockImplementation(async path => {
      if (path.includes('assertion-audit')) return { data: {
        summary: {
          total: 1, verified: 0, upgrade_available: 1, http_failure: 0, business_failure: 0,
          domain_assertion_required: 0, evidence_missing: 0, needs_review: 1, safe_review: 1,
        },
        items: [{
          baseline_id: 'baseline-review', case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1',
          case_name: '添加收藏 - 正常流程', method: 'POST', path: '/collection/add', group_name: '我的收藏', environment_revision_id: 'env-v6',
          evidence_execution_case_id: 'execution-1', evidence_captured_at: '2026-08-27T10:00:00Z',
          status: 'upgrade_available', status_label: '可补精确断言', reason: '需要补充业务断言',
          actual_http_status: 200, business_path: '$.code', business_value: 0, suggested_assertions: [],
          execution: { level: 'direct', label: '可直接复核', selectable: true, reason: '只读接口' },
        }],
      } } as never
      if (path.includes('/baselines?')) return { data: { baselines: [baseline] } } as never
      return { data: { tasks: [] } } as never
    })
    const wrapper = mountWithContext()
    await flushPromises()

    await buttonByText(wrapper, '检查断言').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="baseline-filter-audit"]').setValue('needs-review')
    await wrapper.get('button[title="重新读取基线"]').trigger('click')
    await flushPromises()

    expect((wrapper.get('[data-testid="baseline-filter-audit"]').element as HTMLSelectElement).value).toBe('all')
    expect(wrapper.text()).toContain('添加收藏 - 正常流程')
  })

  it('keeps disabled application baselines manageable but blocks new task and execution actions', async () => {
    replaceTestApplications([{
      package: 'com.example.school', name: '校园应用', enabled: false,
      business_lines: [{ id: 'shared', name: '校园共享', enabled: true }],
    }])
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [baselineFixture()] } })

    const wrapper = mountWithContext()
    await flushPromises()
    await wrapper.get('[data-testid="baseline-select-baseline-1"]').setValue(true)

    expect(wrapper.text()).toContain('应用“校园应用”已停用')
    expect(buttonByText(wrapper, '保存为基线回归任务').attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '按当前环境执行所选基线').attributes('disabled')).toBeDefined()
    await wrapper.get('input[placeholder^="例如：发版冒烟"]').setValue('历史归档')
    expect(wrapper.get('[data-testid="baseline-move-selected"]').attributes('disabled')).toBeUndefined()
  })

  it('paginates large baseline collections without changing filtered selection semantics', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: Array.from({ length: 51 }, (_, index) => baselineFixture({
      id: `baseline-${index + 1}`,
      endpoint_id: `endpoint-${index + 1}`,
      case_name: `基础用例 ${index + 1}`,
    })) } })

    const wrapper = mountWithContext()
    await flushPromises()

    expect(wrapper.findAll('.baseline-row')).toHaveLength(25)
    expect(wrapper.text()).toContain('第 1 / 3 页')

    await buttonByText(wrapper, '全选当前筛选').trigger('click')
    expect(useBaselinesStore().selectedIds).toHaveLength(51)

    await wrapper.get('[data-testid="baseline-page-next"]').trigger('click')
    expect(wrapper.findAll('.baseline-row')).toHaveLength(25)
    expect(wrapper.text()).toContain('基础用例 26')

    await wrapper.get('[data-testid="baseline-page-next"]').trigger('click')
    expect(wrapper.findAll('.baseline-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('基础用例 51')
  })
})
