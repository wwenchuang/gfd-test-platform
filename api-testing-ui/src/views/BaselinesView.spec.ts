// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import { useContextStore } from '../stores/context'
import BaselinesView from './BaselinesView.vue'

describe('BaselinesView fixed project assets', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('keeps project baselines visible and selected when source or environment changes', async () => {
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
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      {
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
      },
    ] } })

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'baselines', component: BaselinesView }],
    })
    await router.push('/')
    await router.isReady()
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
})
