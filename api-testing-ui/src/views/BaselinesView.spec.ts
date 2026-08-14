// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'
import { useTasksStore } from '../stores/tasks'
import BaselinesView from './BaselinesView.vue'

function mountWithContext(): ReturnType<typeof mount> {
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
    ],
  })

  return mount(BaselinesView, {
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
    case_name: '添加收藏 - 正常流程',
    case_version: 2,
    priority: 'P0',
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
  })

  it('keeps project baselines visible and selected when source or environment changes', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      baselineFixture(),
    ] } })

    const wrapper = mountWithContext()
    await flushPromises()

    expect(wrapper.text()).toContain('添加收藏 - 正常流程')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('[data-testid="switch-source"]').trigger('click')
    await wrapper.get('[data-testid="switch-environment"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('添加收藏 - 正常流程')
    expect(wrapper.text()).toContain('来源版本')
    expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(true)
    expect(vi.mocked(apiClient.get).mock.calls.filter(([path]) => path.includes('/baselines'))).toHaveLength(1)
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
    await flushPromises()

    await wrapper.get('input[type="checkbox"]').setValue(true)
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
})
