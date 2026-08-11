import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type { ApiTestTask, ExecutionView } from '../api/contracts'
import { createIdempotencyKey } from '../utils/idempotency'

interface TaskContext {
  projectId: string
  sourceRevisionId: string
  environmentRevisionId: string
}

export const useTasksStore = defineStore('api-test-tasks', {
  state: () => ({
    task: null as ApiTestTask | null,
    loading: false,
    saving: false,
    running: false,
    error: '',
  }),
  actions: {
    async restore(projectId: string): Promise<ApiTestTask | null> {
      this.loading = true
      this.error = ''
      try {
        const response = await apiClient.get<{ task: ApiTestTask | null }>(
          `/api/api-testing/v1/tasks/active?project_id=${encodeURIComponent(projectId)}`,
        )
        this.task = response.data.task
        return this.task
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法恢复当前测试任务'
        return null
      } finally {
        this.loading = false
      }
    },
    async saveSelection(context: TaskContext, endpointIds: string[], name: string): Promise<ApiTestTask> {
      if (!endpointIds.length) throw new Error('请至少选择一个接口')
      this.saving = true
      this.error = ''
      try {
        const response = await apiClient.post<{ task: ApiTestTask }>('/api/api-testing/v1/tasks', {
          project_id: context.projectId,
          source_revision_id: context.sourceRevisionId,
          environment_revision_id: context.environmentRevisionId,
          name,
          selected_endpoint_ids: endpointIds,
        })
        this.task = response.data.task
        return this.task
      } catch (error) {
        this.error = error instanceof Error ? error.message : '测试任务保存失败'
        throw error
      } finally {
        this.saving = false
      }
    },
    async runCurrent(): Promise<ExecutionView> {
      if (!this.task) throw new Error('请先保存本次测试任务')
      this.running = true
      this.error = ''
      try {
        const response = await apiClient.post<{ task: ApiTestTask; execution: ExecutionView }>(
          `/api/api-testing/v1/tasks/${encodeURIComponent(this.task.id)}/run`,
          { idempotency_key: createIdempotencyKey() },
        )
        this.task = response.data.task
        return response.data.execution
      } catch (error) {
        this.error = error instanceof Error ? error.message : '测试任务执行失败'
        throw error
      } finally {
        this.running = false
      }
    },
    clear(): void {
      this.task = null
      this.error = ''
    },
  },
})
