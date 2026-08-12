import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { apiClient } from '../api/client'
import { useNotificationsStore } from './notifications'

describe('notifications store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
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
      notification: { execution_id: 'execution-1', channel_type: 'feishu', sent: true, message: '飞书报告已发送' },
    } })
    const store = useNotificationsStore()

    await store.sendExecutionReport('execution-1')

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/executions/execution-1/notify', {
      channel_type: 'feishu',
    })
    expect(store.lastSendMessage).toBe('飞书报告已发送')
  })
})
