// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TaskStatusStrip from './TaskStatusStrip.vue'

describe('TaskStatusStrip', () => {
  it('shows the saved scope and emits the two explicit commands', async () => {
    const wrapper = mount(TaskStatusStrip, {
      props: {
        task: {
          id: 'task-1', project_id: 'project-1', source_revision_id: 'source-1',
          environment_revision_id: 'environment-1', name: '我的收藏接口回归',
          state: 'ready', selected_endpoint_ids: ['endpoint-1', 'endpoint-2'],
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
    await wrapper.get('[data-testid="save-task"]').trigger('click')
    await wrapper.get('[data-testid="run-task"]').trigger('click')
    expect(wrapper.emitted('save')).toHaveLength(1)
    expect(wrapper.emitted('run')).toHaveLength(1)
  })
})
