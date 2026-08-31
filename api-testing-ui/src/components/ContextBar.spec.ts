// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ContextBar from './ContextBar.vue'

const OPTIONS = {
  projects: [{ id: 'project-uuid', name: '3D 项目' }],
  sourceRevisions: [{ id: 'source-uuid', source_id: 'source-record-uuid', project_id: 'project-uuid', name: '3D 接口', revision_number: 3, endpoint_count: 986 }],
  environmentRevisions: [{ id: 'env-uuid', environment_id: 'environment-record-uuid', project_id: 'project-uuid', name: '生产环境（腾讯云）', revision: 7 }],
}

describe('ContextBar', () => {
  it('shows saved names and emits selection changes without exposing UUIDs', async () => {
    const wrapper = mount(ContextBar, {
      props: {
        ...OPTIONS,
        projectId: 'project-uuid', sourceRevisionId: 'source-uuid', environmentRevisionId: 'env-uuid',
      },
    })

    expect(wrapper.text()).toContain('3D 项目')
    expect(wrapper.text()).toContain('3D 接口')
    expect(wrapper.text()).toContain('生产环境（腾讯云）')
    expect(wrapper.get('[data-testid="production-environment-warning"]').text()).toContain('生产环境')
    expect(wrapper.get('[data-testid="production-environment-warning"]').text()).toContain('执行前确认')
    expect(wrapper.text()).not.toContain('project-uuid')

    await wrapper.get('[data-testid="context-environment"]').setValue('env-uuid')
    expect(wrapper.emitted('update:environmentRevisionId')?.at(-1)?.[0]).toBe('env-uuid')
  })

  it('shows a direct next action when no saved source exists', () => {
    const wrapper = mount(ContextBar, {
      props: { projects: OPTIONS.projects, sourceRevisions: [], environmentRevisions: [], projectId: 'project-uuid', sourceRevisionId: null, environmentRevisionId: null },
    })
    expect(wrapper.text()).toContain('先保存接口来源')
  })

  it('does not claim an empty workspace has been saved', () => {
    const wrapper = mount(ContextBar, {
      props: { projects: [], sourceRevisions: [], environmentRevisions: [], projectId: null, sourceRevisionId: null, environmentRevisionId: null, saved: true },
    })

    expect(wrapper.text()).toContain('先创建项目')
    expect(wrapper.text()).not.toContain('范围已保存')
  })

  it('labels range saving as an explicit save action', () => {
    const wrapper = mount(ContextBar, {
      props: {
        ...OPTIONS,
        projectId: 'project-uuid', sourceRevisionId: 'source-uuid', environmentRevisionId: 'env-uuid',
      },
    })

    expect(wrapper.text()).toContain('保存测试范围')
    expect(wrapper.text()).not.toContain('应用范围')
  })

  it('keeps historical task revisions visible when option lists are stale', () => {
    const wrapper = mount(ContextBar, {
      props: {
        projects: OPTIONS.projects,
        sourceRevisions: [],
        environmentRevisions: [],
        projectId: 'project-uuid',
        sourceRevisionId: 'task-source-revision',
        environmentRevisionId: 'task-runtime-env',
      },
    })

    const source = wrapper.get('[data-testid="context-source"]').element as HTMLSelectElement
    const environment = wrapper.get('[data-testid="context-environment"]').element as HTMLSelectElement
    expect(source.value).toBe('task-source-revision')
    expect(environment.value).toBe('task-runtime-env')
    expect(wrapper.text()).toContain('当前任务接口版本')
    expect(wrapper.text()).toContain('当前执行环境')
  })

  it('provides a compact range summary with expandable controls for narrow screens', async () => {
    const wrapper = mount(ContextBar, {
      props: {
        ...OPTIONS,
        projectId: 'project-uuid', sourceRevisionId: 'source-uuid', environmentRevisionId: 'env-uuid',
      },
    })

    expect(wrapper.get('[data-testid="context-summary"]').text()).toContain('3D 项目')
    expect(wrapper.get('[data-testid="context-summary"]').text()).toContain('v3 · 986 个接口')
    expect(wrapper.get('[data-testid="context-summary"]').text()).toContain('v7')
    expect(wrapper.get('[data-testid="context-summary"]').text()).toContain('生产环境（腾讯云）')
    expect(wrapper.get('[data-testid="context-toggle"]').attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('.context-bar').classes()).not.toContain('context-expanded')

    await wrapper.get('[data-testid="context-toggle"]').trigger('click')
    expect(wrapper.get('[data-testid="context-toggle"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.context-bar').classes()).toContain('context-expanded')
  })
})
