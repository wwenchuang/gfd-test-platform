// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ApiTestTask } from '../api/contracts'
import TaskListPanel from './TaskListPanel.vue'

const TASK: ApiTestTask = {
  id: 'task-1',
  project_id: 'project-1',
  source_revision_id: 'source-1',
  environment_revision_id: 'environment-1',
  name: '收藏链路回归',
  state: 'ready',
  selected_endpoint_ids: ['endpoint-1', 'endpoint-2'],
  runnable_baseline_count: 1,
  latest_ai_job_id: null,
  latest_execution_id: null,
  summary: {},
  created_at: '',
  updated_at: '',
}

describe('TaskListPanel', () => {
  it('selects, runs, deletes, and creates tasks from the dedicated list', async () => {
    const wrapper = mount(TaskListPanel, {
      props: {
        tasks: [TASK, { ...TASK, id: 'task-2', name: '待设计接口', state: 'draft', runnable_baseline_count: 0 }],
        activeTaskId: 'task-1',
      },
    })

    expect(wrapper.get('[data-testid="task-list-item-task-1"]').classes()).toContain('active')
    await wrapper.get('[data-testid="task-list-new"]').trigger('click')
    await wrapper.get('[data-testid="task-list-edit-task-2"]').trigger('click')
    await wrapper.get('[data-testid="task-list-run-task-1"]').trigger('click')
    await wrapper.get('[data-testid="task-list-delete-task-1"]').trigger('click')

    expect(wrapper.emitted('new')).toHaveLength(1)
    expect(wrapper.emitted('select')?.at(-1)).toEqual(['task-2'])
    expect(wrapper.emitted('run')).toEqual([['task-1']])
    expect(wrapper.emitted('delete')).toEqual([[TASK]])
    expect(wrapper.get('[data-testid="task-list-run-task-2"]').attributes('disabled')).toBeDefined()
  })

  it('filters saved tasks by name', async () => {
    const wrapper = mount(TaskListPanel, {
      props: {
        tasks: [TASK, { ...TASK, id: 'task-2', name: '设备巡检' }],
      },
    })

    await wrapper.get('[data-testid="task-list-search"]').setValue('设备')

    expect(wrapper.text()).toContain('设备巡检')
    expect(wrapper.text()).not.toContain('收藏链路回归')
  })
})
