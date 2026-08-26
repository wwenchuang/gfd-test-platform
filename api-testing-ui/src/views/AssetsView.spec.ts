// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import { useContextStore } from '../stores/context'
import { useSetupStore } from '../stores/setup'
import AssetsView from './AssetsView.vue'

const routeState = vi.hoisted(() => ({ query: {} as Record<string, string> }))

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
}))

describe('AssetsView Apifox actions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    routeState.query = {}
    vi.spyOn(apiClient, 'get').mockImplementation(async (path) => {
      if (path.endsWith('/workspace')) return { data: { workspace: null } } as never
      if (path.endsWith('/context-options')) {
        return { data: {
          projects: [{ id: 'project-1', name: '3D 家用' }],
          source_revisions: [], environment_revisions: [],
        } } as never
      }
      return { data: { credential: {
        provider: 'apifox', configured: true, fingerprint: 'a1b2c3d4e5f6', updated_at: null,
      } } } as never
    })
  })

  it('shows operation-specific loading text and keeps saving as a separate confirmation', async () => {
    const wrapper = mount(AssetsView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()
    const setup = useSetupStore()

    setup.busy = true
    setup.apifoxOperation = 'loading_projects'
    await nextTick()
    expect(buttonText(wrapper)).toContain('正在读取项目…')

    setup.apifoxOperation = 'loading_context'
    await nextTick()
    expect(buttonText(wrapper)).toContain('正在读取环境…')

    setup.apifoxOperation = 'checking_update'
    await nextTick()
    expect(buttonText(wrapper)).toContain('正在检查更新…')

    setup.busy = false
    setup.apifoxOperation = null
    setup.preview = {
      id: 'preview-1', project_id: 'project-1', source_id: 'source-1', previous_revision_id: null,
      candidate_revision_id: 'candidate-1', added_count: 3, changed_count: 1, removed_count: 0, changes: [],
    }
    setup.apifoxPreview = {
      source_preview: setup.preview,
      environment_candidate: { name: '生产环境', secret_placeholders: [] },
    }
    await nextTick()

    expect(buttonText(wrapper)).toContain('保存为新版本')
    expect(wrapper.text()).toContain('检查更新只生成预览，不会覆盖当前版本')
  })

  it('selects a newly created project after refreshing the project options', async () => {
    const wrapper = mount(AssetsView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()
    const context = useContextStore()
    vi.spyOn(useSetupStore(), 'createProject').mockResolvedValue('project-2')
    vi.spyOn(context, 'loadOptions').mockImplementation(async () => {
      context.projects = [
        { id: 'project-1', name: '3D 家用' },
        { id: 'project-2', name: '3D 我的收藏' },
      ]
    })

    const newProjectButton = wrapper.findAll('button').find(button => button.text() === '新建平台项目')
    expect(newProjectButton).toBeDefined()
    await newProjectButton!.trigger('click')
    await wrapper.get('input[placeholder="例如：3D 家用业务"]').setValue('3D 我的收藏')
    const createButton = wrapper.findAll('button').find(button => button.text() === '创建')
    expect(createButton).toBeDefined()
    await createButton!.trigger('click')
    await flushPromises()

    expect((wrapper.get('[data-testid="platform-project-select"]').element as HTMLSelectElement).value).toBe('project-2')
  })

  it('uses the project passed from the environment asset center instead of the saved workspace project', async () => {
    routeState.query = { projectId: 'project-2' }
    vi.mocked(apiClient.get).mockImplementation(async (path) => {
      if (path.endsWith('/workspace')) {
        return { data: { workspace: { project_id: 'project-1' } } } as never
      }
      if (path.endsWith('/context-options')) {
        return { data: {
          projects: [
            { id: 'project-1', name: '3D 家用' },
            { id: 'project-2', name: '打印后台' },
          ],
          source_revisions: [],
          environment_revisions: [],
        } } as never
      }
      return { data: { credential: {
        provider: 'apifox', configured: true, fingerprint: 'a1b2c3d4e5f6', updated_at: null,
      } } } as never
    })

    const wrapper = mount(AssetsView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect((wrapper.get('[data-testid="platform-project-select"]').element as HTMLSelectElement).value).toBe('project-2')
  })

  it('presents saved API assets by project and enters the workbench with the selected context', async () => {
    vi.mocked(apiClient.get).mockImplementation(async (path) => {
      if (path.endsWith('/workspace')) {
        return { data: {
          workspace: {
            project_id: 'project-1',
            source_revision_id: 'source-2',
            environment_revision_id: 'env-2',
          },
        } } as never
      }
      if (path.endsWith('/context-options')) {
        return { data: {
          projects: [
            { id: 'project-1', name: '3D 家用', description: '家用业务接口' },
            { id: 'project-2', name: '打印后台', description: '后台接口' },
          ],
          source_revisions: [
            {
              id: 'source-1', source_id: 'source-a', project_id: 'project-1',
              name: '默认模块', revision_number: 1, endpoint_count: 960,
              created_at: '2026-08-12T10:00:00Z',
            },
            {
              id: 'source-2', source_id: 'source-a', project_id: 'project-1',
              name: '默认模块', revision_number: 2, endpoint_count: 999,
              created_at: '2026-08-13T10:00:00Z',
            },
            {
              id: 'source-3', source_id: 'source-b', project_id: 'project-2',
              name: '后台模块', revision_number: 1, endpoint_count: 42,
              created_at: '2026-08-10T10:00:00Z',
            },
          ],
          environment_revisions: [
            { id: 'env-1', environment_id: 'environment-a', project_id: 'project-1', name: '测试环境', revision: 1 },
            { id: 'env-2', environment_id: 'environment-a', project_id: 'project-1', name: '生产环境（新）-腾讯云', revision: 6 },
            { id: 'env-3', environment_id: 'environment-b', project_id: 'project-2', name: '后台测试环境', revision: 1 },
          ],
        } } as never
      }
      return { data: { credential: {
        provider: 'apifox', configured: true, fingerprint: 'a1b2c3d4e5f6', updated_at: null,
      } } } as never
    })
    const links: unknown[] = []
    const wrapper = mount(AssetsView, {
      global: {
        stubs: {
          RouterLink: {
            props: ['to'],
            setup(props) {
              links.push(props.to)
              return {}
            },
            template: '<a data-testid="router-link"><slot /></a>',
          },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('项目资产')
    expect(wrapper.text()).toContain('3D 家用')
    expect(wrapper.text()).toContain('打印后台')
    expect(wrapper.text()).toContain('v2 · 999 个接口')
    expect(wrapper.text()).toContain('2 个环境')
    expect(wrapper.text()).toContain('同步最新接口')
    expect(wrapper.text()).toContain('编辑项目')
    expect(wrapper.text()).toContain('删除项目')
    expect(wrapper.text()).toContain('进入工作台')
    expect(links).toContainEqual({
      path: '/',
      query: {
        projectId: 'project-1',
        sourceRevisionId: 'source-2',
        environmentRevisionId: 'env-2',
      },
    })
  })

  it('allows leaving project editing without saving changes', async () => {
    const wrapper = mount(AssetsView, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()

    await wrapper.findAll('button').find(button => button.text().includes('编辑项目'))!.trigger('click')
    expect(wrapper.text()).toContain('只修改平台侧名称和备注')
    await wrapper.get('[data-testid="project-edit-cancel"]').trigger('click')

    expect(wrapper.text()).not.toContain('只修改平台侧名称和备注')
  })
})

function buttonText(wrapper: ReturnType<typeof mount>): string {
  return wrapper.findAll('button').map(item => item.text()).join('|')
}
