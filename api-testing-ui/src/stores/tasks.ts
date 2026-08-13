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
    tasks: [] as ApiTestTask[],
    loading: false,
    saving: false,
    running: false,
    error: '',
  }),
  actions: {
    async list(projectId: string): Promise<ApiTestTask[]> {
      this.loading = true
      this.error = ''
      try {
        const response = await apiClient.get<{ tasks: ApiTestTask[] }>(
          `/api/api-testing/v1/tasks?project_id=${encodeURIComponent(projectId)}`,
        )
        this.tasks = response.data.tasks
        if (this.task) this.task = this.tasks.find(item => item.id === this.task?.id) || this.task
        return this.tasks
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法读取测试任务列表'
        return []
      } finally {
        this.loading = false
      }
    },
    select(taskId: string): ApiTestTask | null {
      const task = this.tasks.find(item => item.id === taskId) || null
      this.task = task
      this.error = ''
      return task
    },
    async restore(projectId: string): Promise<ApiTestTask | null> {
      this.loading = true
      this.error = ''
      try {
        const response = await apiClient.get<{ task: ApiTestTask | null }>(
          `/api/api-testing/v1/tasks/active?project_id=${encodeURIComponent(projectId)}`,
        )
        this.task = response.data.task
        if (this.task && !this.tasks.some(item => item.id === this.task?.id)) {
          this.tasks = [this.task, ...this.tasks]
        }
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
        const payload = {
          project_id: context.projectId,
          source_revision_id: context.sourceRevisionId,
          environment_revision_id: context.environmentRevisionId,
          name,
          selected_endpoint_ids: endpointIds,
        }
        const sameTaskContext = this.task
          && this.task.project_id === context.projectId
          && this.task.source_revision_id === context.sourceRevisionId
        const response = sameTaskContext
          ? await apiClient.put<{ task: ApiTestTask }>(
            `/api/api-testing/v1/tasks/${encodeURIComponent(this.task!.id)}`,
            payload,
          )
          : await apiClient.post<{ task: ApiTestTask }>('/api/api-testing/v1/tasks', payload)
        this.task = response.data.task
        this.upsertTask(this.task)
        return this.task
      } catch (error) {
        this.error = error instanceof Error ? error.message : '测试任务保存失败'
        throw error
      } finally {
        this.saving = false
      }
    },
    async rename(taskId: string, name: string): Promise<ApiTestTask> {
      const nextName = name.trim()
      if (!nextName) throw new Error('任务名称不能为空')
      this.saving = true
      this.error = ''
      try {
        const response = await apiClient.put<{ task: ApiTestTask }>(
          `/api/api-testing/v1/tasks/${encodeURIComponent(taskId)}/name`,
          { name: nextName },
        )
        this.upsertTask(response.data.task)
        if (this.task?.id === response.data.task.id) this.task = response.data.task
        return response.data.task
      } catch (error) {
        this.error = error instanceof Error ? error.message : '任务名称保存失败'
        throw error
      } finally {
        this.saving = false
      }
    },
    async runCurrent(environmentRevisionId?: string): Promise<ExecutionView> {
      if (!this.task) throw new Error('请先保存本次测试任务')
      this.running = true
      this.error = ''
      try {
        const payload: Record<string, string> = { idempotency_key: createIdempotencyKey() }
        if (environmentRevisionId) payload.environment_revision_id = environmentRevisionId
        const response = await apiClient.post<{ task: ApiTestTask; execution: ExecutionView }>(
          `/api/api-testing/v1/tasks/${encodeURIComponent(this.task.id)}/run`,
          payload,
        )
        this.task = response.data.task
        this.upsertTask(this.task)
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
    upsertTask(task: ApiTestTask): void {
      const index = this.tasks.findIndex(item => item.id === task.id)
      if (index >= 0) this.tasks.splice(index, 1, task)
      else this.tasks.unshift(task)
    },
  },
})
