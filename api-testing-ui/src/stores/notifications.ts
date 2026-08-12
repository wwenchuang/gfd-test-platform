import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type { FeishuNotification, NotificationSendResult } from '../api/contracts'

interface FeishuInput {
  name: string
  enabled: boolean
  webhook: string
}

export const useNotificationsStore = defineStore('api-notifications', {
  state: () => ({
    feishu: null as FeishuNotification | null,
    loading: false,
    saving: false,
    sending: false,
    error: '',
    message: '',
    lastSendMessage: '',
  }),
  actions: {
    async loadFeishu(projectId: string): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const response = await apiClient.get<{ notification: FeishuNotification }>(
          `/api/api-testing/v1/notifications/feishu?project_id=${encodeURIComponent(projectId)}`,
        )
        this.feishu = response.data.notification
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法读取飞书通知配置'
      } finally {
        this.loading = false
      }
    },
    async saveFeishu(projectId: string, input: FeishuInput): Promise<void> {
      this.saving = true
      this.error = ''
      this.message = ''
      try {
        const response = await apiClient.put<{ notification: FeishuNotification }>(
          '/api/api-testing/v1/notifications/feishu',
          {
            project_id: projectId,
            name: input.name,
            enabled: input.enabled,
            webhook: input.webhook,
          },
        )
        this.feishu = response.data.notification
        this.message = '飞书通知配置已保存'
      } catch (error) {
        this.error = error instanceof Error ? error.message : '飞书通知配置保存失败'
        throw error
      } finally {
        this.saving = false
      }
    },
    async sendExecutionReport(executionId: string): Promise<NotificationSendResult> {
      this.sending = true
      this.error = ''
      this.lastSendMessage = ''
      try {
        const response = await apiClient.post<{ notification: NotificationSendResult }>(
          `/api/api-testing/v1/executions/${encodeURIComponent(executionId)}/notify`,
          { channel_type: 'feishu' },
        )
        this.lastSendMessage = response.data.notification.message
        return response.data.notification
      } catch (error) {
        this.error = error instanceof Error ? error.message : '飞书报告发送失败'
        throw error
      } finally {
        this.sending = false
      }
    },
  },
})
