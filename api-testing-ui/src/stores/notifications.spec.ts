import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { apiClient } from '../api/client'
import { useNotificationsStore } from './notifications'

describe('notifications store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('ignores a late Feishu configuration from the previous project', async () => {
    const pending = new Map<string, (value: unknown) => void>()
    vi.spyOn(apiClient, 'get').mockImplementation(path => new Promise(resolve => pending.set(path, resolve)) as never)
    const store = useNotificationsStore()
    const first = store.loadFeishu('project-1')
    const second = store.loadFeishu('project-2')
    pending.get('/api/api-testing/v1/notifications/feishu?project_id=project-2')?.({ data: { notification: { project_id: 'project-2', configured: false } } })
    await second
    pending.get('/api/api-testing/v1/notifications/feishu?project_id=project-1')?.({ data: { notification: { project_id: 'project-1', configured: true } } })
    await first
    expect(store.feishu?.project_id).toBe('project-2')

    const stale = store.loadFeishu('project-1')
    store.clearFeishu()
    pending.get('/api/api-testing/v1/notifications/feishu?project_id=project-1')?.({ data: { notification: { project_id: 'project-1', configured: true } } })
    await stale
    expect(store.feishu).toBeNull()
  })

  it('does not restore an old project notification after leaving during save', async () => {
    let finishSave: ((value: unknown) => void) | undefined
    vi.spyOn(apiClient, 'put').mockImplementation(() => new Promise(resolve => { finishSave = resolve }) as never)
    const store = useNotificationsStore()
    const saving = store.saveFeishu('project-1', { name: 'A 项目通知', enabled: true, webhook: '' })
    store.clearFeishu()
    finishSave?.({ data: { notification: { project_id: 'project-1', configured: true } } })
    await saving

    expect(store.feishu).toBeNull()
    expect(store.message).toBe('')
  })

  it('unlocks saving when a read starts while the save is pending', async () => {
    let finishSave: ((value: unknown) => void) | undefined
    let finishRead: ((value: unknown) => void) | undefined
    vi.spyOn(apiClient, 'put').mockImplementation(() => new Promise(resolve => { finishSave = resolve }) as never)
    vi.spyOn(apiClient, 'get').mockImplementation(() => new Promise(resolve => { finishRead = resolve }) as never)
    const store = useNotificationsStore()
    const saving = store.saveFeishu('project-1', { name: 'A 项目通知', enabled: true, webhook: '' })
    const reading = store.loadFeishu('project-1')
    finishSave?.({ data: { notification: { project_id: 'project-1', configured: true } } })
    await saving
    expect(store.saving).toBe(false)
    finishRead?.({ data: { notification: { project_id: 'project-1', configured: true } } })
    await reading
  })

  it('keeps saving busy until the newest overlapping save finishes', async () => {
    const finish: Array<(value: unknown) => void> = []
    vi.spyOn(apiClient, 'put').mockImplementation(() => new Promise(resolve => { finish.push(resolve) }) as never)
    const store = useNotificationsStore()
    const first = store.saveFeishu('project-1', { name: '第一次', enabled: true, webhook: '' })
    const second = store.saveFeishu('project-1', { name: '第二次', enabled: true, webhook: '' })
    finish[0]?.({ data: { notification: { project_id: 'project-1', name: '第一次' } } })
    await first
    expect(store.saving).toBe(true)
    finish[1]?.({ data: { notification: { project_id: 'project-1', name: '第二次' } } })
    await second
    expect(store.saving).toBe(false)
    expect(store.feishu?.name).toBe('第二次')
  })

  it('loads and saves the Feishu webhook without requiring the stored secret to be displayed', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { notification: {
      project_id: 'project-1', channel_type: 'feishu', name: '接口回归通知',
      enabled: true, configured: true, fingerprint: 'abc123def456', updated_at: '2026-08-12T10:00:00Z',
    } } })
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { notification: {
      project_id: 'project-1', channel_type: 'feishu', name: '接口回归通知',
      enabled: true, configured: true, fingerprint: 'fed654cba321', updated_at: '2026-08-12T10:02:00Z',
    } } })
    const store = useNotificationsStore()

    await store.loadFeishu('project-1')
    await store.saveFeishu('project-1', {
      name: '接口回归通知',
      enabled: true,
      webhook: 'https://open.feishu.cn/open-apis/bot/v2/hook/token',
    })

    expect(get).toHaveBeenCalledWith('/api/api-testing/v1/notifications/feishu?project_id=project-1')
    expect(put).toHaveBeenCalledWith('/api/api-testing/v1/notifications/feishu', {
      project_id: 'project-1',
      name: '接口回归通知',
      enabled: true,
      webhook: 'https://open.feishu.cn/open-apis/bot/v2/hook/token',
    })
    expect(store.feishu?.fingerprint).toBe('fed654cba321')
  })

  it('sends a completed execution report to Feishu', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {
      notification: { execution_id: 'execution-1', channel_type: 'feishu', sent: true, message: '飞书通知已发' },
    } })
    const store = useNotificationsStore()

    await store.sendExecutionReport('execution-1')

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/executions/execution-1/notify', {
      channel_type: 'feishu',
    })
    expect(store.lastSendMessage).toBe('飞书通知已发')
  })

  it('sends a project notification configuration test without an execution', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {
      notification: { project_id: 'project-1', channel_type: 'feishu', sent: true, message: '飞书测试通知已发' },
    } })
    const store = useNotificationsStore()

    await store.testFeishu('project-1')

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/notifications/feishu/test', {
      project_id: 'project-1',
    })
    expect(store.lastSendMessage).toBe('飞书测试通知已发')
  })
})
