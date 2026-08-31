// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import { useContextStore } from '../stores/context'
import { replaceTestApplications } from '../utils/testApplications'
import ScheduledJobsView from './ScheduledJobsView.vue'

describe('ScheduledJobsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    replaceTestApplications([{
      package: 'com.example.school', name: '校园应用', enabled: true,
      business_lines: [{ id: 'shared', name: '校园共享', enabled: true }],
    }])
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
    expect(wrapper.text()).toContain('POST /login')
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
      cron_expression: '0 2 * * *',
      environment_strategy: 'fixed_revision',
      enabled: true,
      notify_feishu: true,
      retry_count: 0,
      timeout_seconds: 1800,
    })
    expect(wrapper.text()).toContain('每日发版回归')

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await wrapper.get('[data-testid="scheduled-run-job-1"]').trigger('click')
    await flushPromises()
    expect(post).toHaveBeenCalledTimes(1)
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/生产环境.*每日发版回归.*真实发送/))

    confirm.mockReturnValue(true)
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
    const target = wrapper.findAll('[data-testid="scheduled-target-option"]').find(item => item.text().includes('登录成功用例'))!
    expect(target.text()).toContain('校园应用 · 校园共享')
    expect(target.text()).not.toContain('com.example.school')
    expect(target.text()).not.toContain('shared')
    await target.trigger('click')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="scheduled-targets"]').exists()).toBe(false)
    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/scheduled-jobs', expect.objectContaining({
      target_type: 'cases',
      target_ids: ['case-version-1'],
    }))
  })

  it('refreshes target assets and shows baseline choices grouped by baseline group', async () => {
    let baselineRefreshes = 0
    vi.spyOn(apiClient, 'get').mockImplementation(async url => {
      const path = String(url)
      if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [] } }
      if (path.startsWith('/api/api-testing/v1/baselines')) {
        baselineRefreshes += 1
        return {
          data: {
            baselines: baselineRefreshes === 1
              ? [baselineFixture({ id: 'baseline-1', group_name: '基线', case_name: '登录成功用例' })]
              : [
                  baselineFixture({ id: 'baseline-1', group_name: '基线', case_name: '登录成功用例' }),
                  baselineFixture({ id: 'baseline-2', group_name: '测试', case_name: '支付成功用例', path: '/pay' }),
                  baselineFixture({ id: 'baseline-3', group_name: '', case_name: '未分组用例', path: '/profile', tags: [] }),
                  baselineFixture({ id: 'baseline-4', group_name: '已删除', case_name: '归档用例', path: '/archived', status: 'archived' }),
                  baselineFixture({ id: 'baseline-5', group_name: '历史', case_name: '历史版本用例', path: '/history', status: 'superseded' }),
                ],
          },
        }
      }
      if (path.startsWith('/api/api-testing/v1/cases')) return { data: { case_versions: [] } }
      if (path.startsWith('/api/api-testing/v1/tasks')) return { data: { tasks: [] } }
      return { data: {} }
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }],
    })

    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).not.toContain('新增分组')
    await wrapper.get('[data-testid="scheduled-refresh"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('测试')
    expect(wrapper.text()).toContain('未分组')
    expect(wrapper.text()).not.toContain('已删除')
    expect(wrapper.text()).not.toContain('历史')
    await wrapper.get('[data-testid="scheduled-target-type"]').setValue('baselines')
    expect(wrapper.text()).toContain('基线')
    expect(wrapper.text()).toContain('测试')
    expect(wrapper.text()).toContain('未分组')
    expect(wrapper.text()).not.toContain('登录成功用例')
    for (const toggle of wrapper.findAll('[data-testid="scheduled-target-group-toggle"]')) await toggle.trigger('click')
    expect(wrapper.text()).toContain('登录成功用例')
    expect(wrapper.text()).toContain('支付成功用例')
    expect(wrapper.text()).toContain('未分组用例')
    expect(wrapper.text()).not.toContain('归档用例')
    expect(wrapper.text()).not.toContain('历史版本用例')
  })

  it('validates cron expressions and explains the edited schedule', async () => {
    mockScheduledJobAssets()
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { scheduled_job: scheduledJobFixture({ id: 'job-4', schedule_type: 'cron', cron_expression: '0 3 * * *' }) },
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }],
    })

    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('[data-testid="scheduled-name"]').setValue('凌晨巡检')
    await wrapper.findAll('[data-testid="scheduled-target-option"]').find(item => item.text().includes('发版冒烟'))!.trigger('click')
    await wrapper.get('[data-testid="scheduled-schedule-cron"]').trigger('click')
    await wrapper.get('[data-testid="scheduled-cron"]').setValue('0 3 * * *')

    expect(wrapper.text()).toContain('每天 03:00 执行')

    await wrapper.get('[data-testid="scheduled-cron"]').setValue('99 3 * * *')
    expect(wrapper.text()).toContain('分钟字段超出范围')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()

    expect(post).not.toHaveBeenCalled()

    await wrapper.get('[data-testid="scheduled-cron"]').setValue('0 3 * * *')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/scheduled-jobs', expect.objectContaining({
      schedule_type: 'cron',
      cron_expression: '0 3 * * *',
    }))
  })

  it('edits list rows, toggles switches, and deletes after confirmation', async () => {
    const get = vi.spyOn(apiClient, 'get').mockImplementation(async url => {
      const path = String(url)
      if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) {
        return {
          data: {
            scheduled_jobs: [
              scheduledJobFixture({
                id: 'job-9',
                name: '基线回归测试',
                target_ids: ['基线'],
                enabled: true,
                notify_feishu: false,
                cron_expression: '0 2 * * *',
              }),
            ],
          },
        }
      }
      if (path.startsWith('/api/api-testing/v1/baselines')) {
        return { data: { baselines: [baselineFixture({ id: 'baseline-1', group_name: '基线', case_name: '登录成功用例' })] } }
      }
      if (path.startsWith('/api/api-testing/v1/cases')) return { data: { case_versions: [] } }
      if (path.startsWith('/api/api-testing/v1/tasks')) return { data: { tasks: [] } }
      return { data: {} }
    })
    const put = vi.spyOn(apiClient, 'put')
      .mockResolvedValueOnce({ data: { scheduled_job: scheduledJobFixture({ id: 'job-9', name: '基线回归测试', target_ids: ['基线'], enabled: false, notify_feishu: false }) } })
      .mockResolvedValueOnce({ data: { scheduled_job: scheduledJobFixture({ id: 'job-9', name: '基线回归测试', target_ids: ['基线'], enabled: true, notify_feishu: true }) } })
      .mockResolvedValueOnce({ data: { scheduled_job: scheduledJobFixture({ id: 'job-9', name: '编辑后的回归', enabled: true, notify_feishu: true }) } })
    const remove = vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { scheduled_job: scheduledJobFixture({ id: 'job-9' }) } })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }],
    })

    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.get('[data-testid="scheduled-list-enabled-job-9"]').text()).toContain('启用')
    expect(wrapper.get('[data-testid="scheduled-list-notify-job-9"]').text()).toContain('飞书')
    expect(wrapper.get('[data-testid="scheduled-list-enabled-job-9"]').attributes('role')).toBe('switch')
    expect(wrapper.get('[data-testid="scheduled-list-enabled-job-9"]').attributes('aria-checked')).toBe('true')
    await wrapper.get('[data-testid="scheduled-list-enabled-job-9"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="scheduled-list-notify-job-9"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="scheduled-edit-job-9"]').trigger('click')
    await flushPromises()

    expect((wrapper.get('[data-testid="scheduled-name"]').element as HTMLInputElement).value).toBe('基线回归测试')
    await wrapper.get('[data-testid="scheduled-name"]').setValue('编辑后的回归')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()

    expect(put).toHaveBeenCalledWith('/api/api-testing/v1/scheduled-jobs/job-9', expect.objectContaining({
      name: '编辑后的回归',
      target_ids: ['基线'],
    }))

    await wrapper.get('[data-testid="scheduled-delete-job-9"]').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledWith('删除定时任务“编辑后的回归”？该操作不可恢复。')
    expect(remove).toHaveBeenCalledWith('/api/api-testing/v1/scheduled-jobs/job-9')
    expect(get).toHaveBeenCalled()
  })

  it('shows the effective schedule and latest execution evidence with a direct link', async () => {
    vi.spyOn(apiClient, 'get').mockImplementation(async url => {
      const path = String(url)
      if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [scheduledJobFixture({
        id: 'job-observable',
        schedule_type: 'cron',
        cron_expression: '0 10 * * *',
        effective_cron_expression: '0 10 * * *',
        next_run_at: '2026-08-27T10:00:00Z',
        latest_run_at: '2026-08-26T10:00:00Z',
        latest_run_trigger: 'scheduler',
        latest_execution_id: 'execution-latest',
        latest_execution_state: 'DONE',
        latest_execution_summary: { total: 2, passed: 1, failed: 1, broken: 0, skipped: 0, cancelled: 0 },
      })] } }
      if (path.startsWith('/api/api-testing/v1/baselines')) return { data: { baselines: [baselineFixture({})] } }
      if (path.startsWith('/api/api-testing/v1/cases')) return { data: { case_versions: [] } }
      if (path.startsWith('/api/api-testing/v1/tasks')) return { data: { tasks: [] } }
      return { data: {} }
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

    const row = wrapper.get('[data-testid="scheduled-row-job-observable"]')
    expect(row.text()).toContain('每天 10:00 执行')
    expect(row.text()).toContain('调度时区 Asia/Shanghai（UTC+08:00）')
    expect(row.text()).toContain('下次执行 2026/08/27 18:00')
    expect(row.text()).toContain('最近调度 2026/08/26 18:00')
    expect(row.text()).toContain('通过 1/2 · 50%')

    await wrapper.get('[data-testid="scheduled-latest-execution-job-observable"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.executionId).toBe('execution-latest')
  })

  it('shows human-readable baseline versions on scheduled job cards instead of raw IDs', async () => {
    vi.spyOn(apiClient, 'get').mockImplementation(async url => {
      const path = String(url)
      if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [scheduledJobFixture({
        id: 'job-readable', target_type: 'baselines', target_ids: ['baseline-uuid-1'],
      })] } }
      if (path.startsWith('/api/api-testing/v1/baselines')) return { data: { baselines: [baselineFixture({
        id: 'baseline-uuid-1', case_name: '收藏列表正向用例', case_version: 3, origin: 'ai', group_name: '收藏回归',
      })] } }
      if (path.startsWith('/api/api-testing/v1/cases')) return { data: { case_versions: [] } }
      if (path.startsWith('/api/api-testing/v1/tasks')) return { data: { tasks: [] } }
      return { data: {} }
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }] })

    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    const row = wrapper.get('[data-testid="scheduled-row-job-readable"]')
    expect(row.text()).toContain('收藏列表正向用例 · v3')
    expect(row.text()).toContain('收藏回归')
    expect(row.text()).not.toContain('baseline-uuid-1')
  })

  it('shows disabled historical targets but prevents adding them to a new scheduled job', async () => {
    replaceTestApplications([{
      package: 'com.example.school', name: '校园应用', enabled: true,
      business_lines: [{ id: 'shared', name: '校园共享', enabled: false }],
    }])
    mockScheduledJobAssets()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }] })

    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()
    const option = wrapper.get('[data-testid="scheduled-target-option"]')
    expect(option.text()).toContain('业务“校园共享”已停用')
    expect(option.attributes('disabled')).toBeDefined()
    await option.trigger('click')
    expect(wrapper.text()).toContain('可多选')
  })

  it('marks an existing job as blocked when its saved target is no longer executable', async () => {
    replaceTestApplications([])
    vi.spyOn(apiClient, 'get').mockImplementation(async url => {
      const path = String(url)
      if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [scheduledJobFixture({ id: 'job-blocked' })] } }
      if (path.startsWith('/api/api-testing/v1/baselines')) return { data: { baselines: [baselineFixture({})] } }
      if (path.startsWith('/api/api-testing/v1/cases')) return { data: { case_versions: [] } }
      if (path.startsWith('/api/api-testing/v1/tasks')) return { data: { tasks: [] } }
      return { data: {} }
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }] })

    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    const row = wrapper.get('[data-testid="scheduled-row-job-blocked"]')
    expect(row.text()).toContain('执行已阻断')
    expect(row.text()).toContain('应用未配置或已移除')
    expect(row.text()).not.toContain('下次执行')
    expect(wrapper.get('[data-testid="scheduled-run-job-blocked"]').attributes('disabled')).toBeDefined()
  })

  it.each([
    ['blocked: permission or scope revoked', '保存任务配置的成员的执行权限或数据范围已撤销', '请联系管理员恢复保存任务配置的成员对项目、环境及执行操作的授权'],
    ['blocked: scheduled target unavailable or outside current scope', '定时任务目标不可用，或已超出当前数据范围', '请检查目标及环境是否有效，并编辑任务重新选择；若授权已撤销，请联系管理员恢复'],
  ])('shows server block %s in the row and editor without treating enabled as execution success', async (reason, message, remedy) => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({ id: 'job-server-blocked', blocked_reason: reason }))
    const row = wrapper.get('[data-testid="scheduled-row-job-server-blocked"]')

    expect(row.text()).toContain(`执行已阻断 · ${message}`)
    expect(row.text()).toContain(remedy)
    expect(row.text()).toContain('配置：已启用')
    expect(row.text()).not.toContain('下次执行')
    expect(row.text()).not.toContain(reason)
    expect(wrapper.get('[data-testid="scheduled-list-enabled-job-server-blocked"]').attributes('aria-checked')).toBe('true')
    // A previous dispatch denial must not prevent a retry after authorization is restored.
    expect(wrapper.get('[data-testid="scheduled-run-job-server-blocked"]').attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-testid="scheduled-edit-job-server-blocked"]').trigger('click')
    expect(wrapper.get('[data-testid="scheduled-editor-blocked"]').text()).toContain(message)
    expect(wrapper.get('[data-testid="scheduled-editor-blocked"]').text()).toContain(remedy)
    expect(wrapper.get('[data-testid="scheduled-enabled-toggle"]').attributes('aria-checked')).toBe('true')
  })

  it('removes both block notices only when the refreshed server state clears the reason', async () => {
    let job = scheduledJobFixture({ id: 'job-recovered', blocked_reason: 'blocked: permission or scope revoked' })
    const wrapper = await mountScheduledState(() => job)
    await wrapper.get('[data-testid="scheduled-edit-job-recovered"]').trigger('click')
    expect(wrapper.get('[data-testid="scheduled-editor-blocked"]').text()).toContain('执行已阻断')

    job = scheduledJobFixture({ id: 'job-recovered', blocked_reason: '', latest_execution_id: 'execution-recovered', latest_execution_state: 'DONE', latest_run_at: '2026-08-31T08:00:00Z', latest_execution_summary: { total: 1, passed: 1 } })
    await wrapper.get('[data-testid="scheduled-refresh"]').trigger('click')
    await flushPromises()

    const row = wrapper.get('[data-testid="scheduled-row-job-recovered"]')
    expect(row.text()).not.toContain('执行已阻断')
    expect(row.text()).toContain('下次执行')
    expect(row.text()).toContain('通过 1/1')
    expect(wrapper.find('[data-testid="scheduled-editor-blocked"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="scheduled-enabled-toggle"]').attributes('aria-checked')).toBe('true')
  })

  it('preserves disabled configuration while showing the last dispatch block', async () => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({ id: 'job-disabled-blocked', enabled: false, blocked_reason: 'blocked: permission or scope revoked' }))
    const row = wrapper.get('[data-testid="scheduled-row-job-disabled-blocked"]')
    expect(row.text()).toContain('配置：已停用')
    expect(row.text()).toContain('执行已阻断')
    expect(row.text()).not.toContain('下次执行')
    expect(wrapper.get('[data-testid="scheduled-list-enabled-job-disabled-blocked"]').attributes('aria-checked')).toBe('false')
  })

  it('keeps unknown server blocks visible with a safe Chinese fallback', async () => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({ id: 'job-unknown-blocked', blocked_reason: 'blocked: <img src=x onerror=alert(1)>' }))
    const row = wrapper.get('[data-testid="scheduled-row-job-unknown-blocked"]')
    expect(row.text()).toContain('服务端阻断原因尚未识别')
    expect(row.text()).toContain('请联系管理员检查任务权限、目标和环境后重试')
    expect(row.find('img').exists()).toBe(false)
    expect(row.text()).not.toContain('下次执行')
  })
})

async function mountScheduledState(job: () => ReturnType<typeof scheduledJobFixture>) {
  vi.spyOn(apiClient, 'get').mockImplementation(async url => {
    const path = String(url)
    if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [job()] } }
    if (path.startsWith('/api/api-testing/v1/baselines')) return { data: { baselines: [baselineFixture({})] } }
    if (path.startsWith('/api/api-testing/v1/cases')) return { data: { case_versions: [] } }
    if (path.startsWith('/api/api-testing/v1/tasks')) return { data: { tasks: [] } }
    return { data: {} }
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }] })
  const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

function mockScheduledJobAssets(): void {
  vi.spyOn(apiClient, 'get').mockImplementation(async url => {
    const path = String(url)
    if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [] } }
    if (path.startsWith('/api/api-testing/v1/baselines')) {
      return {
        data: {
          baselines: [
            baselineFixture({ id: 'baseline-1', group_name: '发版冒烟', case_name: '登录成功用例' }),
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
              group_name: '',
              name: '登录成功用例',
              purpose: '验证登录主链路',
              app_package: 'com.example.school',
              app_name: '校园应用旧名称',
              business: 'shared',
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

function baselineFixture(overrides: Record<string, unknown>) {
  return {
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
    app_package: 'com.example.school',
    app_name: '校园应用旧名称',
    business: 'shared',
    origin: 'manual',
    method: 'POST',
    path: '/login',
    endpoint_summary: '登录',
    tags: ['发版冒烟'],
    group_name: '发版冒烟',
    adoption_reason: '',
    adopted_at: '2026-08-14T10:00:00Z',
    ...overrides,
  }
}

function scheduledJobFixture(overrides: Record<string, unknown>) {
  return {
    id: 'job-1',
    project_id: 'project-1',
    source_revision_id: 'source-1',
    environment_revision_id: 'env-revision-1',
    environment_id: 'env-1',
    name: '每日发版回归',
    target_type: 'baseline_group',
    target_ids: ['发版冒烟'],
    schedule_type: 'daily',
    cron_expression: '0 2 * * *',
    environment_strategy: 'fixed_revision',
    enabled: true,
    notify_feishu: true,
    retry_count: 0,
    timeout_seconds: 1800,
    latest_execution_id: null,
    effective_cron_expression: '0 2 * * *',
    next_run_at: '2026-08-27T02:00:00+08:00',
    latest_run_at: null,
    latest_run_trigger: null,
    latest_execution_state: null,
    latest_execution_summary: {},
    scheduler_timezone: 'Asia/Shanghai',
    scheduler_utc_offset: '+08:00',
    created_at: '2026-08-14T10:00:00Z',
    updated_at: '2026-08-14T10:00:00Z',
    ...overrides,
  }
}
