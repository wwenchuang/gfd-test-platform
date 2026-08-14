// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TaskStatusStrip from './TaskStatusStrip.vue'

describe('TaskStatusStrip', () => {
  it('blocks regression until a debugged case is adopted as a baseline', async () => {
    const wrapper = mount(TaskStatusStrip, {
      props: {
        task: {
          id: 'task-1', project_id: 'project-1', source_revision_id: 'source-1',
          environment_revision_id: 'environment-1', name: '我的收藏接口回归',
          state: 'ready', selected_endpoint_ids: ['endpoint-1', 'endpoint-2'],
          runnable_baseline_count: 0,
          latest_ai_job_id: 'job-1', latest_execution_id: null, summary: {},
          created_at: '', updated_at: '',
        },
        selectedCount: 3,
        environmentName: '生产环境（新）- 腾讯云',
      },
    })

    expect(wrapper.text()).toContain('我的收藏接口回归')
    expect(wrapper.text()).toContain('已保存 2 个接口')
    expect(wrapper.text()).toContain('当前选择 3 个')
    expect(wrapper.text()).toContain('0 / 2')
    expect(wrapper.text()).toContain('可加入定时回归')
    expect(wrapper.text()).toContain('待采纳基线')
    expect(wrapper.get('[data-testid="run-task"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="new-task"]').trigger('click')
    await wrapper.get('[data-testid="save-task"]').trigger('click')
    await wrapper.get('[data-testid="run-task"]').trigger('click')
    expect(wrapper.emitted('new')).toHaveLength(1)
    expect(wrapper.emitted('save')).toHaveLength(1)
    expect(wrapper.emitted('run')).toBeUndefined()
  })

  it('runs the saved task when at least one selected baseline is active', async () => {
    const wrapper = mount(TaskStatusStrip, {
      props: {
        task: {
          id: 'task-1', project_id: 'project-1', source_revision_id: 'source-1',
          environment_revision_id: 'environment-1', name: '我的收藏接口回归',
          state: 'ready', selected_endpoint_ids: ['endpoint-1', 'endpoint-2'],
          runnable_baseline_count: 1,
          latest_ai_job_id: 'job-1', latest_execution_id: null, summary: {},
          created_at: '', updated_at: '',
        },
        selectedCount: 2,
        environmentName: '生产环境（新）- 腾讯云',
      },
    })

    expect(wrapper.text()).toContain('可执行 1 / 2')
    expect(wrapper.text()).toContain('1 / 2')
    await wrapper.get('[data-testid="run-task"]').trigger('click')
    expect(wrapper.emitted('run')).toHaveLength(1)
  })

  it('supports renaming the current task without managing saved tasks inline', async () => {
    const task = {
      id: 'task-1', project_id: 'project-1', source_revision_id: 'source-1',
      environment_revision_id: 'environment-1', name: '我的收藏接口回归任务名称很长需要被压缩显示',
      state: 'ready' as const, selected_endpoint_ids: ['endpoint-1', 'endpoint-2'],
      runnable_baseline_count: 1,
      latest_ai_job_id: 'job-1', latest_execution_id: null, summary: {},
      created_at: '', updated_at: '',
    }
    const wrapper = mount(TaskStatusStrip, {
      props: {
        task,
        selectedCount: 2,
        environmentName: '生产环境（新）- 腾讯云',
        taskNameDraft: task.name,
      },
    })

    expect(wrapper.find('[data-testid="task-selector"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('我的收藏接口回归任务名称很长需要被压缩显示')
    expect(wrapper.text()).toContain('基线')

    await wrapper.get('[data-testid="task-name-input"]').setValue('收藏冒烟')
    expect(wrapper.emitted('update:taskNameDraft')?.at(-1)).toEqual(['收藏冒烟'])

    await wrapper.get('[data-testid="rename-task"]').trigger('click')
    expect(wrapper.emitted('rename-task')).toHaveLength(1)
  })
})
