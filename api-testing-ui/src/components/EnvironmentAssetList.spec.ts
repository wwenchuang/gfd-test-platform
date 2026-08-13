// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EnvironmentAssetList from './EnvironmentAssetList.vue'

const projects = [
  { id: 'project-1', name: '3D 家用' },
  { id: 'project-2', name: '打印后台' },
]

const environments = [
  {
    id: 'environment-1', project_id: 'project-1', source_id: 'source-1',
    active_revision_id: 'revision-2', source_revision_id: 'source-revision-2',
    revision: 2, name: '生产环境（新）- 腾讯云', description: '线上回归', status: 'active' as const,
    service_count: 2, public_variable_count: 3, secret_count: 1,
    created_at: '2026-08-10T10:00:00Z', updated_at: '2026-08-13T10:00:00Z',
  },
  {
    id: 'environment-2', project_id: 'project-1', source_id: 'source-1',
    active_revision_id: 'revision-1', source_revision_id: 'source-revision-2',
    revision: 1, name: '灰度环境', description: '灰度联调', status: 'active' as const,
    service_count: 1, public_variable_count: 2, secret_count: 1,
    created_at: '2026-08-10T10:00:00Z', updated_at: '2026-08-12T10:00:00Z',
  },
]

describe('EnvironmentAssetList', () => {
  it('switches projects and selects a stable environment asset', async () => {
    const wrapper = mount(EnvironmentAssetList, {
      props: {
        projects,
        environments,
        selectedProjectId: 'project-1',
        selectedEnvironmentId: '',
        status: 'active',
      },
    })

    await wrapper.get('[data-project-id="project-2"]').trigger('click')
    await wrapper.get('[data-environment-id="environment-1"]').trigger('click')

    expect(wrapper.emitted('select-project')?.[0]).toEqual(['project-2'])
    expect(wrapper.emitted('select-environment')?.[0]).toEqual(['environment-1'])
    expect(wrapper.text()).toContain('2 个服务')
    expect(wrapper.text()).toContain('v2')
  })

  it('supports active and archived filters with archive and restore commands', async () => {
    const wrapper = mount(EnvironmentAssetList, {
      props: {
        projects,
        environments,
        selectedProjectId: 'project-1',
        selectedEnvironmentId: 'environment-1',
        status: 'active',
      },
    })

    await wrapper.get('[data-status="archived"]').trigger('click')
    await wrapper.get('[data-action="archive"]').trigger('click')
    await wrapper.setProps({
      status: 'archived',
      environments: [{ ...environments[0], status: 'archived' }],
    })
    await wrapper.get('[data-action="restore"]').trigger('click')

    expect(wrapper.emitted('update:status')?.[0]).toEqual(['archived'])
    expect(wrapper.emitted('archive')?.[0]).toEqual(['environment-1'])
    expect(wrapper.emitted('restore')?.[0]).toEqual(['environment-1'])
  })

  it('shows project environment counts and filters saved environments by search', async () => {
    const wrapper = mount(EnvironmentAssetList, {
      props: {
        projects,
        environments,
        selectedProjectId: 'project-1',
        selectedEnvironmentId: '',
        status: 'active',
        projectStats: {
          'project-1': { environmentCount: 2, activeCount: 2, archivedCount: 0, updatedAt: '2026-08-13T10:00:00Z' },
          'project-2': { environmentCount: 0, activeCount: 0, archivedCount: 0, updatedAt: null },
        },
      },
    })

    expect(wrapper.get('[data-project-id="project-1"]').text()).toContain('2 个环境')
    expect(wrapper.get('[data-project-id="project-2"]').text()).toContain('暂无环境')

    await wrapper.get('[data-environment-search]').setValue('腾讯云')

    expect(wrapper.text()).toContain('1 / 2 个')
    expect(wrapper.text()).toContain('生产环境（新）- 腾讯云')
    expect(wrapper.text()).not.toContain('灰度环境')
  })
})
