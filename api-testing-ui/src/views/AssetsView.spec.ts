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

    expect(buttonText(wrapper)).toContain('保存并切换到新版本')
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

    const newProjectButton = wrapper.findAll('button').find(button => button.attributes('aria-label') === '打开项目创建面板')
    expect(newProjectButton).toBeDefined()
    await newProjectButton!.trigger('click')
    await wrapper.get('input[placeholder="例如：3D 家用业务"]').setValue('3D 我的收藏')
    const createButton = wrapper.findAll('button').find(button => button.text() === '创建')
    expect(createButton).toBeDefined()
    await createButton!.trigger('click')
    await flushPromises()

    expect((wrapper.get('[data-testid="platform-project-select"]').element as HTMLSelectElement).value).toBe('project-2')
  })

  it('switches the saved workspace to a newly activated JSON interface revision', async () => {
    const wrapper = mount(AssetsView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()
    const context = useContextStore()
    const setup = useSetupStore()
    setup.preview = {
      id: 'preview-json', project_id: 'project-1', source_id: 'source-a', previous_revision_id: null,
      candidate_revision_id: 'source-v2', added_count: 2, changed_count: 0, removed_count: 0, changes: [],
    }
    vi.spyOn(setup, 'activatePreview').mockResolvedValue({
      id: 'source-v2', source_id: 'source-a', project_id: 'project-1', name: '本地 OpenAPI',
      revision_number: 2, endpoint_count: 12, status: 'active',
    } as never)
    vi.spyOn(context, 'loadOptions').mockImplementation(async () => {
      context.projects = [{ id: 'project-1', name: '3D 家用' }]
      context.sourceRevisions = [{
        id: 'source-v2', source_id: 'source-a', project_id: 'project-1', name: '本地 OpenAPI',
        revision_number: 2, endpoint_count: 12,
      }]
      context.environmentRevisions = [{
        id: 'env-v3', environment_id: 'environment-1', project_id: 'project-1', name: '测试环境', revision: 3,
      }]
    })
    const saveContext = vi.spyOn(context, 'saveContext').mockImplementation(async () => {
      context.savedContextSignature = JSON.stringify(['project-1', 'source-v2', 'env-v3'])
    })
    await wrapper.vm.$nextTick()

    await buttonByText(wrapper, '确认保存接口').trigger('click')
    await flushPromises()

    expect(context.projectId).toBe('project-1')
    expect(context.sourceRevisionId).toBe('source-v2')
    expect(context.environmentRevisionId).toBe('env-v3')
    expect(saveContext).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('接口版本 v2 已保存并切换为当前测试范围')
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
    expect(wrapper.text()).toContain('同步接口更新')
    expect(wrapper.text()).toContain('进入用例管理')
    expect(wrapper.text()).toContain('编辑项目')
    expect(wrapper.text()).toContain('归档项目')
    expect(wrapper.text()).toContain('进入工作台')
    expect(links).toContainEqual({
      path: '/',
      query: {
        projectId: 'project-1',
        sourceRevisionId: 'source-2',
        environmentRevisionId: 'env-2',
      },
    })
    expect(wrapper.get('[data-testid="apifox-sync-panel"]').attributes('open')).toBeUndefined()
    await wrapper.get('[data-testid="open-apifox-sync"]').trigger('click')
    expect(wrapper.get('[data-testid="apifox-sync-panel"]').attributes('open')).toBeDefined()
  })

  it('allows leaving project editing without saving changes', async () => {
    const wrapper = mount(AssetsView, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()

    await wrapper.findAll('button').find(button => button.text().includes('编辑项目'))!.trigger('click')
    expect(wrapper.text()).toContain('只修改平台侧名称和备注')
    await wrapper.get('[data-testid="project-edit-cancel"]').trigger('click')

    expect(wrapper.text()).not.toContain('只修改平台侧名称和备注')
  })

  it('requires a choice among multiple Apifox projects and clears stale environments on project change', async () => {
    const wrapper = mount(AssetsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    const setup = useSetupStore()
    vi.spyOn(setup, 'discoverApifoxProjects').mockImplementation(async () => {
      setup.apifoxProjects = [
        { id: 'fox-a', name: '家用', description: '', team_name: '' },
        { id: 'fox-b', name: '后台', description: '', team_name: '' },
      ]
      return setup.apifoxProjects
    })
    await buttonByText(wrapper, '读取项目').trigger('click')
    await flushPromises()
    expect((wrapper.get('[data-testid="apifox-project"]').element as HTMLSelectElement).value).toBe('')
    await wrapper.get('[data-testid="apifox-project"]').setValue('fox-a')
    setup.apifoxContext = { project: setup.apifoxProjects[0], branches: [], environments: [{ id: 'env-a', name: '开发', services: [], variables: [] }], cli_version: '' }
    await nextTick()
    await wrapper.get('[data-testid="apifox-environment"]').setValue('env-a')
    await wrapper.get('[data-testid="apifox-project"]').setValue('fox-b')
    expect(setup.apifoxContext).toBeNull()
    expect(buttonByText(wrapper, '检查更新').attributes('disabled')).toBeDefined()
  })

  it('keeps the saved environment by unique name and never silently chooses the first environment', async () => {
    const wrapper = mount(AssetsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    const setup = useSetupStore()
    const context = useContextStore()
    context.environmentRevisions = [{ id: 'saved-env', environment_id: 'env', project_id: 'project-1', name: '生产', revision: 4 }]
    await nextTick()
    await wrapper.get('[data-testid="saved-environment"]').setValue('saved-env')
    setup.apifoxProjects = [{ id: 'fox-a', name: '家用', description: '', team_name: '' }]
    await nextTick()
    await wrapper.get('[data-testid="apifox-project"]').setValue('fox-a')
    const result = { project: setup.apifoxProjects[0], branches: [{ id: 'main', name: '主分支', is_default: true }], environments: [
      { id: 'dev', name: '开发', services: [], variables: [] },
      { id: 'prod', name: '生产', services: [], variables: [] },
    ], cli_version: '' }
    vi.spyOn(setup, 'discoverApifoxContext').mockImplementation(async () => { setup.apifoxContext = result; return result })
    await buttonByText(wrapper, '读取环境').trigger('click')
    await flushPromises()
    expect((wrapper.get('[data-testid="apifox-environment"]').element as HTMLSelectElement).value).toBe('prod')
    result.environments = [{ id: 'dev', name: '开发', services: [], variables: [] }, { id: 'staging', name: '预发', services: [], variables: [] }]
    await buttonByText(wrapper, '读取环境').trigger('click')
    await flushPromises()
    expect((wrapper.get('[data-testid="apifox-environment"]').element as HTMLSelectElement).value).toBe('')
  })

  it('shows searchable change details instead of only counts before activation', async () => {
    const wrapper = mount(AssetsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    useSetupStore().preview = {
      id: 'preview-a', project_id: 'project-1', source_id: 'source-1', previous_revision_id: null,
      candidate_revision_id: 'candidate-a', added_count: 1, changed_count: 1, removed_count: 0,
      changes: [
        { change_type: 'added', method: 'GET', path: '/new-interface', changed_fields: [] },
        { change_type: 'changed', method: 'POST', path: '/old-interface', changed_fields: ['responses'] },
      ],
    }
    await nextTick()
    expect(wrapper.get('[data-testid="source-changes"]').text()).toContain('/new-interface')
    expect(wrapper.get('[data-testid="source-changes"]').text()).toContain('响应定义')
    await wrapper.get('[data-testid="source-change-search"]').setValue('new-interface')
    expect(wrapper.get('[data-testid="source-changes"]').text()).not.toContain('/old-interface')
  })

  it('keeps saved changes with direct links to the new endpoint after activation', async () => {
    const wrapper = mount(AssetsView, { global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="JSON.stringify(to)"><slot /></a>' } } } })
    await flushPromises()
    const setup = useSetupStore()
    const context = useContextStore()
    const preview = { id: 'preview-a', project_id: 'project-1', source_id: 'source-a', previous_revision_id: null,
      candidate_revision_id: 'source-v2', added_count: 1, changed_count: 0, removed_count: 0,
      changes: [{ change_type: 'added', method: 'GET', path: '/new-endpoint', changed_fields: [] }] }
    setup.preview = preview
    setup.apifoxPreview = { source_preview: preview, environment_candidate: { name: '测试环境', secret_placeholders: [] } }
    vi.spyOn(setup, 'activateApifoxPreview').mockImplementation(async () => {
      setup.activeRevision = { id: 'source-v2', endpoints: [{ id: 'new-endpoint-id', method: 'GET', path: '/new-endpoint' }] } as never
      setup.preview = null
      setup.apifoxPreview = null
      return { workspace: { project_id: 'project-1', source_revision_id: 'source-v2', environment_revision_id: 'env-v2' },
        source_revision: setup.activeRevision, environment: { revision_id: 'env-v2' } } as never
    })
    vi.spyOn(context, 'loadOptions').mockImplementation(async () => {
      context.sourceRevisions = [{ id: 'source-v2', source_id: 'source-a', project_id: 'project-1', name: '默认模块', revision_number: 2, endpoint_count: 2 }]
      context.environmentRevisions = [{ id: 'env-v2', environment_id: 'env-a', project_id: 'project-1', name: '测试环境', revision: 2 }]
    })
    await nextTick()
    await buttonByText(wrapper, '保存并切换到新版本').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="source-preview"]').text()).toContain('本次更新已保存')
    const link = wrapper.get('.source-change-link')
    expect(JSON.parse(link.attributes('href')!)).toEqual({ path: '/', query: {
      projectId: 'project-1', sourceRevisionId: 'source-v2', environmentRevisionId: 'env-v2', endpointId: 'new-endpoint-id',
    } })
    expect(buttonText(wrapper)).not.toContain('保存并切换到新版本')
  })
})

function buttonText(wrapper: ReturnType<typeof mount>): string {
  return wrapper.findAll('button').map(item => item.text()).join('|')
}

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find(item => item.text().includes(text))
  expect(button, `button ${text}`).toBeTruthy()
  return button!
}
