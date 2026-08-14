// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import { useContextStore } from '../stores/context'
import ScheduledJobsView from './ScheduledJobsView.vue'

describe('ScheduledJobsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1',
      sourceRevisionId: 'source-1',
      environmentRevisionId: 'env-revision-1',
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [
        { id: 'source-1', project_id: 'project-1', name: '默认模块', revision_number: 1, endpoint_count: 999 },
      ],
      environmentRevisions: [
        { id: 'env-revision-1', environment_id: 'env-1', project_id: 'project-1', name: '生产环境', revision: 1 },
      ],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
  })

  it('creates a scheduled baseline group job and manually runs it', async () => {
    mockScheduledJobAssets()
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({
        data: {
          scheduled_job: {
            id: 'job-1',
            project_id: 'project-1',
            source_revision_id: 'source-1',
            environment_revision_id: 'env-revision-1',
            environment_id: 'env-1',
            name: '每日发版回归',
            target_type: 'baseline_group',
            target_ids: ['发版冒烟'],
            schedule_type: 'daily',
            cron_expression: '',
            environment_strategy: 'fixed_revision',
            enabled: true,
            notify_feishu: true,
            retry_count: 1,
            timeout_seconds: 900,
            latest_execution_id: null,
            created_at: '2026-08-14T10:00:00Z',
            updated_at: '2026-08-14T10:00:00Z',
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          execution: {
            id: 'execution-1',
            project_id: 'project-1',
            task_id: 'job-1',
            task_name: '每日发版回归',
            task_type: 'scheduled_job',
            execution_source: 'scheduled_job',
            state: 'QUEUED',
            execution_type: 'baseline_regression',
            source_revision_id: 'source-1',
            environment_revision_id: 'env-revision-1',
            environment_name: '生产环境',
            case_statuses: [],
            case_results: [],
            summary: { total: 0 },
            notifications: {},
            cancellation_requested: false,
            created_at: '2026-08-14T10:01:00Z',
            started_at: null,
            finished_at: null,
          },
        },
      })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'scheduled-jobs', component: ScheduledJobsView },
        { path: '/runs', name: 'runs', component: { template: '<div />' } },
      ],
    })

    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('[data-testid="scheduled-name"]').setValue('每日发版回归')
    expect(wrapper.find('[data-testid="scheduled-targets"]').exists()).toBe(false)
    await wrapper.findAll('[data-testid="scheduled-target-option"]').find(item => item.text().includes('发版冒烟'))!.trigger('click')
    await wrapper.get('[data-testid="scheduled-notify-toggle"]').trigger('click')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/scheduled-jobs', {
      project_id: 'project-1',
      source_revision_id: 'source-1',
      environment_revision_id: 'env-revision-1',
      name: '每日发版回归',
      target_type: 'baseline_group',
      target_ids: ['发版冒烟'],
      schedule_type: 'daily',
      cron_expression: '',
      environment_strategy: 'fixed_revision',
      enabled: true,
      notify_feishu: true,
      retry_count: 0,
      timeout_seconds: 1800,
    })
    expect(wrapper.text()).toContain('每日发版回归')

    await wrapper.get('[data-testid="scheduled-run-job-1"]').trigger('click')
    await flushPromises()

    expect(post.mock.calls[1][0]).toBe('/api/api-testing/v1/scheduled-jobs/job-1/run')
    expect(router.currentRoute.value.name).toBe('runs')
    expect(router.currentRoute.value.query.executionId).toBe('execution-1')
  })

  it('fills a cron expression from examples', async () => {
    mockScheduledJobAssets()
    const post = vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      data: {
        scheduled_job: {
          id: 'job-2',
          project_id: 'project-1',
          source_revision_id: 'source-1',
          environment_revision_id: 'env-revision-1',
          environment_id: null,
          name: '每周接口巡检',
          target_type: 'baseline_group',
          target_ids: ['发版冒烟'],
          schedule_type: 'cron',
          cron_expression: '0 9 * * 1',
          environment_strategy: 'fixed_revision',
          enabled: true,
          notify_feishu: false,
          retry_count: 0,
          timeout_seconds: 1800,
          latest_execution_id: null,
          created_at: '2026-08-14T10:00:00Z',
          updated_at: '2026-08-14T10:00:00Z',
        },
      },
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }],
    })

    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('[data-testid="scheduled-name"]').setValue('每周接口巡检')
    await wrapper.findAll('[data-testid="scheduled-target-option"]').find(item => item.text().includes('发版冒烟'))!.trigger('click')
    await wrapper.get('[data-testid="scheduled-schedule-cron"]').trigger('click')
    await wrapper.get('[data-testid="scheduled-cron-weekly"]').trigger('click')
    expect((wrapper.get('[data-testid="scheduled-cron"]').element as HTMLInputElement).value).toBe('0 9 * * 1')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/scheduled-jobs', expect.objectContaining({
      schedule_type: 'cron',
      cron_expression: '0 9 * * 1',
      target_ids: ['发版冒烟'],
    }))
  })

  it('selects saved case versions instead of requiring typed case IDs', async () => {
    mockScheduledJobAssets()
    const post = vi.spyOn(apiClient, 'post').mockResolvedValueOnce({
      data: {
        scheduled_job: {
          id: 'job-3',
          project_id: 'project-1',
          source_revision_id: 'source-1',
          environment_revision_id: 'env-revision-1',
          environment_id: null,
          name: '登录用例巡检',
          target_type: 'cases',
          target_ids: ['case-version-1'],
          schedule_type: 'daily',
          cron_expression: '',
          environment_strategy: 'fixed_revision',
          enabled: true,
          notify_feishu: false,
          retry_count: 0,
          timeout_seconds: 1800,
          latest_execution_id: null,
          created_at: '2026-08-14T10:00:00Z',
          updated_at: '2026-08-14T10:00:00Z',
        },
      },
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }],
    })

    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('[data-testid="scheduled-name"]').setValue('登录用例巡检')
    await wrapper.get('[data-testid="scheduled-target-type"]').setValue('cases')
    await wrapper.findAll('[data-testid="scheduled-target-option"]').find(item => item.text().includes('登录成功用例'))!.trigger('click')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="scheduled-targets"]').exists()).toBe(false)
    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/scheduled-jobs', expect.objectContaining({
      target_type: 'cases',
      target_ids: ['case-version-1'],
    }))
  })
})

function mockScheduledJobAssets(): void {
  vi.spyOn(apiClient, 'get').mockImplementation(async url => {
    const path = String(url)
    if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [] } }
    if (path.startsWith('/api/api-testing/v1/baselines')) {
      return {
        data: {
          baselines: [
            {
              id: 'baseline-1',
              project_id: 'project-1',
              case_id: 'case-1',
              case_version_id: 'case-version-1',
              environment_revision_id: 'env-revision-1',
              source_revision_id: 'source-1',
              endpoint_id: 'endpoint-1',
              status: 'active',
              case_name: '登录成功用例',
              case_version: 1,
              priority: 'P0',
              origin: 'manual',
              method: 'POST',
              path: '/login',
              endpoint_summary: '登录',
              tags: ['发版冒烟'],
              group_name: '发版冒烟',
              adoption_reason: '',
              adopted_at: '2026-08-14T10:00:00Z',
            },
          ],
        },
      }
    }
    if (path.startsWith('/api/api-testing/v1/cases')) {
      return {
        data: {
          case_versions: [
            {
              id: 'case-version-1',
              case_id: 'case-1',
              endpoint_id: 'endpoint-1',
              status: 'active',
              origin: 'manual',
              version: 1,
              name: '登录成功用例',
              purpose: '验证登录主链路',
              priority: 'P0',
              request: {
                method: 'POST',
                path: '/login',
                service: '',
                path_params: {},
                query: {},
                headers: {},
                cookies: {},
                body: {},
              },
              data_rows: [],
              assertions: [],
              extractions: [],
              dependencies: [],
              processing: { pre: [], post: [] },
              validation_summary: {},
            },
          ],
        },
      }
    }
    if (path.startsWith('/api/api-testing/v1/tasks')) return { data: { tasks: [] } }
    return { data: {} }
  })
}
