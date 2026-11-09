import { defineStore } from 'pinia'

import { apiClient, type ApiClient } from '../api/client'
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
    listLoading: false,
    restoreLoading: false,
    saving: false,
    savingOperations: 0,
    running: false,
    error: '',
    errorSource: '' as '' | 'list' | 'restore' | 'operation',
    operationError: '',
    readErrors: { list: '', restore: '' },
    listGeneration: 0,
    restoreGeneration: 0,
    saveOperationGeneration: 0,
    runOperationGeneration: 0,
  }),
  actions: {
    beginSaving(): void {
      this.savingOperations += 1
      this.saving = true
    },
    endSaving(): void {
      this.savingOperations = Math.max(0, this.savingOperations - 1)
      this.saving = this.savingOperations > 0
    },
    async list(projectId: string, client: Pick<ApiClient, 'get'> = apiClient): Promise<ApiTestTask[]> {
      const generation = ++this.listGeneration
      this.listLoading = true
      this.loading = true
      this.error = ''
      this.errorSource = ''
      try {
        const response = await client.get<{ tasks: ApiTestTask[] }>(
          `/api/api-testing/v1/tasks?project_id=${encodeURIComponent(projectId)}`,
        )
        if (generation !== this.listGeneration) return []
        this.readErrors.list = ''
        this.tasks = response.data.tasks
        if (this.task) this.task = this.tasks.find(item => item.id === this.task?.id) || this.task
        return this.tasks
      } catch (error) {
        if (generation !== this.listGeneration) return []
        this.error = error instanceof Error ? error.message : '无法读取测试任务列表'
        this.errorSource = 'list'
        this.readErrors.list = this.error
        return []
      } finally {
        if (generation === this.listGeneration) {
          this.listLoading = false
          this.loading = this.restoreLoading
        }
      }
    },
    select(taskId: string): ApiTestTask | null {
      this.restoreGeneration += 1
      this.restoreLoading = false
      this.loading = this.listLoading
      const task = this.tasks.find(item => item.id === taskId) || null
      this.task = task
      this.error = ''
      this.errorSource = ''
      return task
    },
    async restore(projectId: string, client: Pick<ApiClient, 'get'> = apiClient): Promise<ApiTestTask | null> {
      const generation = ++this.restoreGeneration
      this.restoreLoading = true
      this.loading = true
      this.error = ''
      this.errorSource = ''
      try {
        const response = await client.get<{ task: ApiTestTask | null }>(
          `/api/api-testing/v1/tasks/active?project_id=${encodeURIComponent(projectId)}`,
        )
        if (generation !== this.restoreGeneration) return null
        this.readErrors.restore = ''
        this.task = response.data.task
        if (this.task && !this.tasks.some(item => item.id === this.task?.id)) {
          this.tasks = [this.task, ...this.tasks]
        }
        return this.task
      } catch (error) {
        if (generation !== this.restoreGeneration) return null
        this.error = error instanceof Error ? error.message : '无法恢复当前测试任务'
        this.errorSource = 'restore'
        this.readErrors.restore = this.error
        return null
      } finally {
        if (generation === this.restoreGeneration) {
          this.restoreLoading = false
          this.loading = this.listLoading
        }
      }
    },
    async saveSelection(context: TaskContext, endpointIds: string[], name: string, options: { isCurrent?: () => boolean } = {}): Promise<ApiTestTask> {
      if (!endpointIds.length) throw new Error('请至少选择一个接口')
      const generation = ++this.saveOperationGeneration
      const isCurrent = () => generation === this.saveOperationGeneration && options.isCurrent?.() !== false
      this.beginSaving()
      this.error = ''
      this.errorSource = ''
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
        if (!isCurrent()) throw new Error('任务已保存，但范围或当前任务已变化，请重新确认。')
        this.task = response.data.task
        this.upsertTask(this.task)
        return this.task
      } catch (error) {
        if (!isCurrent()) throw error
        this.error = error instanceof Error ? error.message : '测试任务保存失败'
        this.errorSource = 'operation'
        this.operationError = this.error
        throw error
      } finally {
        this.endSaving()
      }
    },
    async createSelection(context: TaskContext, endpointIds: string[], name: string): Promise<ApiTestTask> {
      const selectedEndpointIds = [...new Set(endpointIds)]
      if (!selectedEndpointIds.length) throw new Error('请至少选择一个接口')
      this.beginSaving()
      this.error = ''
      this.errorSource = ''
      try {
        const response = await apiClient.post<{ task: ApiTestTask }>('/api/api-testing/v1/tasks', {
          project_id: context.projectId,
          source_revision_id: context.sourceRevisionId,
          environment_revision_id: context.environmentRevisionId,
          name,
          selected_endpoint_ids: selectedEndpointIds,
        })
        this.task = response.data.task
        this.upsertTask(this.task)
        return this.task
      } catch (error) {
        this.error = error instanceof Error ? error.message : '测试任务创建失败'
        this.errorSource = 'operation'
        this.operationError = this.error
        throw error
      } finally {
        this.endSaving()
      }
    },
    async rename(taskId: string, name: string): Promise<ApiTestTask> {
      const nextName = name.trim()
      if (!nextName) throw new Error('任务名称不能为空')
      this.beginSaving()
      this.error = ''
      this.errorSource = ''
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
        this.errorSource = 'operation'
        this.operationError = this.error
        throw error
      } finally {
        this.endSaving()
      }
    },
    async runCurrent(environmentRevisionId?: string, options: { isCurrent?: () => boolean; expectedTaskId?: string } = {}): Promise<ExecutionView> {
      if (!this.task) throw new Error('请先保存本次测试任务')
      if (options.expectedTaskId && this.task.id !== options.expectedTaskId) throw new Error('当前任务已变化，未执行；请重新确认。')
      const generation = ++this.runOperationGeneration
      const isCurrent = () => generation === this.runOperationGeneration && options.isCurrent?.() !== false
      this.running = true
      this.error = ''
      this.errorSource = ''
      try {
        const payload: Record<string, string> = { idempotency_key: createIdempotencyKey() }
        if (environmentRevisionId) payload.environment_revision_id = environmentRevisionId
        const response = await apiClient.post<{ task: ApiTestTask; execution: ExecutionView }>(
          `/api/api-testing/v1/tasks/${encodeURIComponent(this.task.id)}/run`,
          payload,
        )
        if (!isCurrent()) throw new Error('任务执行请求已提交，请前往执行记录核对结果。')
        this.task = response.data.task
        this.upsertTask(this.task)
        return response.data.execution
      } catch (error) {
        if (!isCurrent()) throw error
        this.error = error instanceof Error ? error.message : '测试任务执行失败'
        this.errorSource = 'operation'
        this.operationError = this.error
        throw error
      } finally {
        if (generation === this.runOperationGeneration) this.running = false
      }
    },
    async remove(taskId: string): Promise<ApiTestTask> {
      this.beginSaving()
      this.error = ''
      this.errorSource = ''
      try {
        const response = await apiClient.delete<{ task: ApiTestTask }>(
          `/api/api-testing/v1/tasks/${encodeURIComponent(taskId)}`,
        )
        this.tasks = this.tasks.filter(item => item.id !== taskId)
        if (this.task?.id === taskId) this.task = null
        return response.data.task
      } catch (error) {
        this.error = error instanceof Error ? error.message : '测试任务删除失败'
        this.errorSource = 'operation'
        this.operationError = this.error
        throw error
      } finally {
        this.endSaving()
      }
    },
    clear(): void {
      this.listGeneration += 1
      this.restoreGeneration += 1
      this.task = null
      this.error = ''
      this.errorSource = ''
      this.readErrors = { list: '', restore: '' }
      this.operationError = ''
      this.listLoading = false
      this.restoreLoading = false
      this.loading = false
    },
    upsertTask(task: ApiTestTask): void {
      const index = this.tasks.findIndex(item => item.id === task.id)
      if (index >= 0) this.tasks.splice(index, 1, task)
      else this.tasks.unshift(task)
    },
  },
})
