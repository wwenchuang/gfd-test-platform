import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type { ExecutionView, ScheduledJob } from '../api/contracts'
import { createIdempotencyKey } from '../utils/idempotency'

export interface ScheduledJobInput {
  project_id: string
  source_revision_id?: string | null
  environment_revision_id?: string | null
  environment_id?: string | null
  name: string
  target_type: ScheduledJob['target_type']
  target_ids: string[]
  schedule_type: ScheduledJob['schedule_type']
  cron_expression: string
  environment_strategy: ScheduledJob['environment_strategy']
  enabled: boolean
  notify_feishu: boolean
  retry_count: number
  timeout_seconds: number
}

export const useScheduledJobsStore = defineStore('api-scheduled-jobs', {
  state: () => ({
    items: [] as ScheduledJob[],
    loading: false,
    saving: false,
    runningId: '',
    removingId: '',
    error: '',
  }),
  actions: {
    async load(projectId: string): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const response = await apiClient.get<{ scheduled_jobs: ScheduledJob[] }>(
          `/api/api-testing/v1/scheduled-jobs?project_id=${encodeURIComponent(projectId)}`,
        )
        this.items = response.data.scheduled_jobs
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法读取定时任务'
      } finally {
        this.loading = false
      }
    },
    async create(input: ScheduledJobInput): Promise<ScheduledJob> {
      this.saving = true
      this.error = ''
      try {
        const response = await apiClient.post<{ scheduled_job: ScheduledJob }>(
          '/api/api-testing/v1/scheduled-jobs',
          input,
        )
        this.items = [response.data.scheduled_job, ...this.items.filter(item => item.id !== response.data.scheduled_job.id)]
        return response.data.scheduled_job
      } catch (error) {
        this.error = error instanceof Error ? error.message : '定时任务保存失败'
        throw error
      } finally {
        this.saving = false
      }
    },
    async update(jobId: string, input: ScheduledJobInput): Promise<ScheduledJob> {
      this.saving = true
      this.error = ''
      try {
        const response = await apiClient.put<{ scheduled_job: ScheduledJob }>(
          `/api/api-testing/v1/scheduled-jobs/${encodeURIComponent(jobId)}`,
          input,
        )
        this.items = this.items.map(item => item.id === jobId ? response.data.scheduled_job : item)
        return response.data.scheduled_job
      } catch (error) {
        this.error = error instanceof Error ? error.message : '定时任务保存失败'
        throw error
      } finally {
        this.saving = false
      }
    },
    async remove(jobId: string): Promise<void> {
      this.error = ''
      this.removingId = jobId
      try {
        await apiClient.delete(`/api/api-testing/v1/scheduled-jobs/${encodeURIComponent(jobId)}`)
        this.items = this.items.filter(item => item.id !== jobId)
      } catch (error) {
        this.error = error instanceof Error ? error.message : '定时任务删除失败，请刷新列表确认后重试'
        throw error
      } finally { this.removingId = '' }
    },
    async runOnce(jobId: string): Promise<ExecutionView> {
      this.runningId = jobId
      this.error = ''
      try {
        const response = await apiClient.post<{ execution: ExecutionView }>(
          `/api/api-testing/v1/scheduled-jobs/${encodeURIComponent(jobId)}/run`,
          { idempotency_key: createIdempotencyKey() },
        )
        this.items = this.items.map(item => item.id === jobId ? { ...item, latest_execution_id: response.data.execution.id } : item)
        return response.data.execution
      } catch (error) {
        this.error = error instanceof Error ? error.message : '定时任务执行失败'
        throw error
      } finally {
        this.runningId = ''
      }
    },
  },
})
