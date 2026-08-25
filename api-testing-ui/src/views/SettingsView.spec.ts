// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { EnvironmentAsset, EnvironmentView } from '../api/contracts'
import { apiClient } from '../api/client'
import { useContextStore } from '../stores/context'
import { useNotificationsStore } from '../stores/notifications'
import { useSetupStore } from '../stores/setup'
import SettingsView from './SettingsView.vue'

const environment: EnvironmentAsset = {
  id: 'environment-1', project_id: 'project-1', source_id: 'source-1',
  active_revision_id: 'environment-revision-2', source_revision_id: 'source-revision-2',
  revision: 2, name: '生产环境（新）-腾讯云', description: '发布回归环境', status: 'active',
  service_count: 2, public_variable_count: 3, secret_count: 1,
  created_at: '2026-08-12T10:00:00Z', updated_at: '2026-08-13T10:00:00Z',
}

const environmentView: EnvironmentView = {
  id: environment.id, project_id: environment.project_id, source_id: environment.source_id,
  revision_id: environment.active_revision_id, source_revision_id: environment.source_revision_id,
  revision: environment.revision, name: environment.name, description: environment.description,
  status: environment.status,
  services: {
    default: { name: 'default', module_name: '默认服务', base_url: 'https://api.example.com', unresolved: false },
    '097168f8-348d-4138-b876-123456789abc': {
      name: '097168f8-348d-4138-b876-123456789abc',
      module_name: '图片建模',
      base_url: 'https://image.example.com',
      unresolved: false,
    },
  },
  variables: { Biz: 'ZXB', ZXBToken: { configured: true } },
  default_headers: {
    Biz: 'ZXB',
    ZXBToken: '{{ZXBToken}}',
    Authorization: 'Bearer {{ZXBToken}}',
  },
}

describe('SettingsView environment asset center', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()

    const context = useContextStore()
    context.projects = [
      { id: 'project-1', name: '3D 家用' },
      { id: 'project-2', name: '打印后台' },
    ]
    context.projectId = 'project-1'
    context.sourceRevisions = [
      { id: 'source-revision-2', source_id: 'source-1', project_id: 'project-1', name: '默认模块', revision_number: 2, endpoint_count: 999 },
    ]
    context.environmentRevisions = [
      { id: 'environment-revision-2', environment_id: 'environment-1', project_id: 'project-1', name: environment.name, revision: 2 },
      { id: 'environment-revision-1', environment_id: 'environment-1', project_id: 'project-1', name: '生产环境', revision: 1 },
    ]
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()

    const setup = useSetupStore()
    vi.spyOn(setup, 'loadEnvironmentAssets').mockImplementation(async () => {
      setup.environmentAssets = [environment]
      return setup.environmentAssets
    })
    vi.spyOn(setup, 'loadEnvironmentProjectStats').mockImplementation(async (projectIds: string[]) => {
      setup.environmentProjectStats = {
        ...setup.environmentProjectStats,
        ...Object.fromEntries(projectIds.map(id => [
          id,
          id === 'project-1'
            ? { environmentCount: 1, activeCount: 1, archivedCount: 0, updatedAt: environment.updated_at }
            : { environmentCount: 0, activeCount: 0, archivedCount: 0, updatedAt: null },
        ])),
      }
      return setup.environmentProjectStats
    })
    vi.spyOn(setup, 'loadEnvironmentRevision').mockImplementation(async () => {
      setup.environment = environmentView
      return environmentView
    })
    vi.spyOn(setup, 'loadEnvironmentHistory').mockImplementation(async () => {
      setup.environmentHistory = [
        { id: 'environment-revision-2', environment_id: environment.id, source_revision_id: 'source-revision-2', revision: 2, name: environment.name, description: environment.description, status: 'active', created_at: '2026-08-13T10:00:00Z', updated_at: '2026-08-13T10:00:00Z' },
        { id: 'environment-revision-1', environment_id: environment.id, source_revision_id: 'source-revision-1', revision: 1, name: '生产环境', description: '', status: 'active', created_at: '2026-08-12T10:00:00Z', updated_at: '2026-08-12T10:00:00Z' },
      ]
      return setup.environmentHistory
    })

    const notifications = useNotificationsStore()
    vi.spyOn(notifications, 'loadFeishu').mockResolvedValue()
  })

  it('lists project environments and opens the selected environment in the workbench', async () => {
    const { wrapper, router } = await mountView()

    expect(wrapper.text()).toContain('项目环境')
    expect(wrapper.text()).toContain('3D 家用')
    expect(wrapper.text()).toContain('生产环境（新）-腾讯云')
    expect(wrapper.text()).toContain('2 个服务')
    expect(wrapper.text()).toContain('版本历史')
    expect(wrapper.text()).toContain('v2')
    expect(wrapper.text()).toContain('图片建模')

    await wrapper.get('[data-action="workbench"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('workbench')
    expect(router.currentRoute.value.query).toEqual({
      projectId: 'project-1',
      sourceRevisionId: 'source-revision-2',
      environmentRevisionId: 'environment-revision-2',
    })
  })

  it('switches project scope and supports archiving and restoring environment assets', async () => {
    const { wrapper } = await mountView()
    const setup = useSetupStore()
    const loadAssets = vi.mocked(setup.loadEnvironmentAssets)
    vi.spyOn(setup, 'archiveEnvironment').mockResolvedValue({ ...environment, status: 'archived' })
    vi.spyOn(setup, 'restoreEnvironment').mockResolvedValue({ ...environment, status: 'active' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await wrapper.get('[data-project-id="project-2"]').trigger('click')
    await flushPromises()
    expect(loadAssets).toHaveBeenLastCalledWith('project-2', 'active')

    await wrapper.get('[data-project-id="project-1"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-action="archive"]').trigger('click')
    await flushPromises()
    expect(setup.archiveEnvironment).toHaveBeenCalledWith('environment-1')

    await wrapper.get('[data-status="archived"]').trigger('click')
    await flushPromises()
    expect(loadAssets).toHaveBeenLastCalledWith('project-1', 'archived')
  })

  it('uses readable detail tabs and restores a historical environment revision', async () => {
    const { wrapper } = await mountView()
    const setup = useSetupStore()
    vi.spyOn(setup, 'restoreEnvironmentRevision').mockResolvedValue({
      ...environmentView,
      revision_id: 'environment-revision-3',
      revision: 3,
      name: '生产环境',
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    expect(wrapper.text()).toContain('概览')
    expect(wrapper.text()).toContain('服务地址')
    expect(wrapper.text()).toContain('变量与凭证')
    expect(wrapper.text()).toContain('版本历史')
    expect(wrapper.text()).not.toContain('097168f8-348d-4138-b876-123456789abc')

    await wrapper.get('[data-tab="variables"]').trigger('click')
    expect(wrapper.text()).toContain('ZXBToken')
    expect(wrapper.text()).toContain('已配置')
    expect(wrapper.text()).not.toContain('secret-token')

    await wrapper.get('[data-tab="history"]').trigger('click')
    await wrapper.get('[data-revision-id="environment-revision-1"][data-action="restore-revision"]').trigger('click')
    await flushPromises()

    expect(setup.restoreEnvironmentRevision).toHaveBeenCalledWith('environment-revision-1')
    expect(wrapper.text()).toContain('v3')
  })

  it('groups duplicate and empty service addresses in read-only views without changing editor rows', async () => {
    const setup = useSetupStore()
    const groupedEnvironment = {
      ...environmentView,
      services: {
        default: { name: 'default', module_name: '默认服务', base_url: 'https://api.example.com/', unresolved: false },
        image: { name: 'image', module_name: '图片建模', base_url: 'https://api.example.com', unresolved: false },
        file: { name: 'file', module_name: '文件服务', base_url: '', unresolved: true },
      },
    } satisfies EnvironmentView
    vi.mocked(setup.loadEnvironmentRevision).mockImplementation(async () => {
      setup.environment = groupedEnvironment
      return setup.environment
    })
    const saveEnvironment = vi.spyOn(setup, 'saveEnvironment').mockResolvedValue(groupedEnvironment)

    const { wrapper } = await mountView()

    expect(wrapper.get('[data-testid="environment-effective-address-count"]').text()).toBe('1')
    expect(wrapper.text()).toContain('3 个服务键映射到 1 个有效地址')
    expect(wrapper.findAll('[data-testid="environment-service-group"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('默认服务、图片建模')
    expect(wrapper.text()).toContain('1 个服务键未配置地址')

    await wrapper.get('[data-action="edit"]').trigger('click')
    expect(wrapper.findAll('input[aria-label="服务地址"]')).toHaveLength(3)

    await wrapper.get('[data-action="save"]').trigger('click')
    await flushPromises()
    expect(saveEnvironment).toHaveBeenCalledWith('environment-1', expect.objectContaining({
      services: {
        default: { name: 'default', module_name: '默认服务', base_url: 'https://api.example.com/' },
        image: { name: 'image', module_name: '图片建模', base_url: 'https://api.example.com' },
        file: { name: 'file', module_name: '文件服务', base_url: null },
      },
    }))
  })

  it('uses readable service names in the edit form instead of internal service ids', async () => {
    const { wrapper } = await mountView()

    await wrapper.get('[data-action="edit"]').trigger('click')

    expect(wrapper.text()).toContain('服务名称')
    expect(wrapper.text()).toContain('内部服务键只用于执行匹配')
    expect(wrapper.text()).not.toContain('服务键已保留')

    const serviceNames = wrapper.findAll('input[aria-label="服务名"]').map(input => (input.element as HTMLInputElement).value)
    expect(serviceNames).toContain('默认服务')
    expect(serviceNames).toContain('图片建模')
    expect(serviceNames).not.toContain('097168f8-348d-4138-b876-123456789abc')
  })

  it('persists deleted default request headers when saving a new environment revision', async () => {
    const { wrapper } = await mountView()
    const setup = useSetupStore()
    const saveEnvironment = vi.spyOn(setup, 'saveEnvironment').mockResolvedValue({
      ...environmentView,
      revision_id: 'environment-revision-3',
      revision: 3,
      default_headers: {
        Biz: 'ZXB',
        ZXBToken: '{{ZXBToken}}',
      },
    })

    await wrapper.get('[data-action="edit"]').trigger('click')
    await flushPromises()

    const headerNameInputs = wrapper.findAll('input[aria-label="请求头名称"]')
    const deleteHeaderButtons = wrapper.findAll('button[title="删除请求头"]')
    const authorizationIndex = headerNameInputs.findIndex(
      input => (input.element as HTMLInputElement).value === 'Authorization',
    )
    expect(authorizationIndex).toBeGreaterThanOrEqual(0)

    await deleteHeaderButtons[authorizationIndex].trigger('click')
    await wrapper.get('[data-action="save"]').trigger('click')
    await flushPromises()

    const payload = saveEnvironment.mock.calls[0]?.[1]
    expect(payload?.default_headers).toEqual({
      Biz: 'ZXB',
      ZXBToken: '{{ZXBToken}}',
    })
  })

  it('preserves the internal default service key when creating an environment', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValue(new Error('source has no servers'))
    const { wrapper } = await mountView()
    const setup = useSetupStore()
    const saveEnvironment = vi.spyOn(setup, 'saveEnvironment').mockResolvedValue(environmentView)

    await wrapper.get('[data-action="create"]').trigger('click')
    await flushPromises()
    await wrapper.get('input[placeholder="例如：生产环境（新）- 腾讯云"]').setValue('本地验收环境')
    await wrapper.get('input[aria-label="服务地址"]').setValue('https://api.example.test')
    await wrapper.get('[data-action="save"]').trigger('click')
    await flushPromises()

    expect(saveEnvironment).toHaveBeenCalledWith(null, expect.objectContaining({
      services: {
        default: {
          name: 'default',
          module_name: '默认服务',
          base_url: 'https://api.example.test',
        },
      },
    }))
  })
})

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'workbench', component: { template: '<div />' } },
      { path: '/assets', name: 'assets', component: { template: '<div />' } },
      { path: '/settings', name: 'settings', component: SettingsView },
    ],
  })
  await router.push('/settings')
  await router.isReady()
  const wrapper = mount(SettingsView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}
