// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import { useContextStore } from '../stores/context'
import { setApiTestingAccessProfile } from '../utils/authRedirect'
import { replaceTestApplications } from '../utils/testApplications'
import ScheduledJobsView from './ScheduledJobsView.vue'

describe('ScheduledJobsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    setApiTestingAccessProfile(null)
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
      allow_one_time_baselines: false,
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

  it('requires and persists an explicit opt-in before scheduling one-time baselines', async () => {
    mockScheduledJobAssets(true)
    const saved = scheduledJobFixture({
      name: '每日含一次性回归',
      target_ids: ['发版冒烟'],
      allow_one_time_baselines: true,
    })
    const post = vi.spyOn(apiClient, 'post').mockResolvedValueOnce({ data: { scheduled_job: saved } })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }],
    })
    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('[data-testid="scheduled-name"]').setValue('每日含一次性回归')
    await wrapper.findAll('[data-testid="scheduled-target-option"]')
      .find(item => item.text().includes('发版冒烟'))!
      .trigger('click')
    expect(wrapper.text()).toContain('包含 1 条一次性基线')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()
    expect(post).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="scheduled-editor-feedback"]').text()).toContain('请明确开启')

    await wrapper.get('[data-testid="scheduled-one-time-toggle"]').trigger('click')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/scheduled-jobs', expect.objectContaining({
      allow_one_time_baselines: true,
      target_ids: ['发版冒烟'],
    }))
    expect(wrapper.text()).toContain('一次性基线已允许')
  })

  it('clears a resolved validation message when the user changes target type', async () => {
    mockScheduledJobAssets(true)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'scheduled-jobs', component: ScheduledJobsView }],
    })
    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('[data-testid="scheduled-name"]').setValue('一次性门禁检查')
    await wrapper.findAll('[data-testid="scheduled-target-option"]')
      .find(item => item.text().includes('发版冒烟'))!
      .trigger('click')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    expect(wrapper.get('[data-testid="scheduled-editor-feedback"]').text()).toContain('请明确开启')

    await wrapper.get('[data-testid="scheduled-target-type"]').setValue('cases')

    expect(wrapper.find('[data-testid="scheduled-editor-feedback"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('请明确开启“一次性基线也执行”')
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
    expect(wrapper.text()).toContain('校园应用 · 校园共享')
    expect(wrapper.text()).not.toContain('登录成功用例')
    for (const toggle of wrapper.findAll('[data-testid="scheduled-target-group-toggle"]')) await toggle.trigger('click')
    expect(wrapper.text()).toContain('基线')
    expect(wrapper.text()).toContain('测试')
    expect(wrapper.text()).toContain('未分组')
    expect(wrapper.text()).toContain('登录成功用例')
    expect(wrapper.text()).toContain('支付成功用例')
    expect(wrapper.text()).toContain('未分组用例')
    expect(wrapper.text()).not.toContain('归档用例')
    expect(wrapper.text()).not.toContain('历史版本用例')
  })

  it('uses application and business as the primary schedule grouping', async () => {
    replaceTestApplications([{
      package: 'com.example.school', name: '智小白3D', enabled: true,
      business_lines: [
        { id: 'home', name: '家用', enabled: true },
        { id: 'shared', name: '共享', enabled: true },
      ],
    }])
    vi.spyOn(apiClient, 'get').mockImplementation(async url => {
      const path = String(url)
      if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [] } }
      if (path.startsWith('/api/api-testing/v1/baselines')) {
        return {
          data: {
            baselines: [
              baselineFixture({ id: 'home-local', business: 'home', group_name: '本地测试', case_name: '家用本地用例' }),
              baselineFixture({ id: 'home-verified', business: 'home', group_name: '家用业务 · 已复验', case_name: '家用已复验用例' }),
              baselineFixture({ id: 'home-one-time', business: 'home', group_name: 'API Test / 一次性', case_name: '家用一次性用例' }),
              baselineFixture({ id: 'shared', business: 'shared', group_name: '共享业务', case_name: '共享用例' }),
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

    const primaryGroups = wrapper.findAll('[data-testid="scheduled-target-group-toggle"]')
    expect(primaryGroups).toHaveLength(2)
    expect(primaryGroups.map(item => item.text())).toEqual(expect.arrayContaining([
      expect.stringContaining('智小白3D · 家用'),
      expect.stringContaining('智小白3D · 共享'),
    ]))
    expect(wrapper.text()).toContain('本地测试')
    expect(wrapper.text()).toContain('家用业务 · 已复验')
    expect(wrapper.text()).toContain('API Test / 一次性')
  })

  it('selects or clears a whole baseline group without losing selections from other groups', async () => {
    replaceTestApplications([{
      package: 'com.example.school', name: '智小白3D', enabled: true,
      business_lines: [
        { id: 'home', name: '家用', enabled: true },
        { id: 'shared', name: '共享', enabled: true },
      ],
    }])
    vi.spyOn(apiClient, 'get').mockImplementation(async url => {
      const path = String(url)
      if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [] } }
      if (path.startsWith('/api/api-testing/v1/baselines')) {
        return {
          data: {
            baselines: [
              baselineFixture({ id: 'home-1', business: 'home', group_name: '家用业务', case_name: '家用查询一' }),
              baselineFixture({ id: 'home-2', business: 'home', group_name: '家用业务 · 已复验', case_name: '家用查询二', path: '/home-two' }),
              baselineFixture({ id: 'shared-1', group_name: '共享业务', case_name: '共享查询', path: '/shared' }),
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

    await wrapper.get('[data-testid="scheduled-target-type"]').setValue('baselines')
    const groupActions = () => wrapper.findAll('[data-testid="scheduled-target-group-select"]')
    const homeAction = () => groupActions().find(item => item.text().includes('智小白3D · 家用'))!
    const sharedAction = () => groupActions().find(item => item.text().includes('智小白3D · 共享'))!

    expect(homeAction().text()).toContain('全选本业务')
    await homeAction().trigger('click')
    expect(wrapper.text()).toContain('已选 2 项')
    expect(homeAction().text()).toContain('清空本业务')

    await sharedAction().trigger('click')
    expect(wrapper.text()).toContain('已选 3 项')

    await homeAction().trigger('click')
    expect(wrapper.text()).toContain('已选 1 项')
    expect(sharedAction().text()).toContain('清空本业务')
    expect(homeAction().text()).toContain('全选本业务')
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
    expect(wrapper.get('[data-testid="scheduled-new"]').text()).toBe('取消编辑')
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
    expect(row.text()).toContain('未通过 · 1/2 通过 · 50%')
    expect(row.text()).toContain('上次结果（历史）')
    expect(wrapper.get('[data-testid="scheduled-current-target-job-observable"]').text()).toContain('1 条基线')

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

  it('blocks production schedule changes before a member edits the full form', async () => {
    setApiTestingAccessProfile({ status: 'active', permissions: ['api.view', 'api.edit', 'api.execute'] })
    const wrapper = await mountScheduledState(() => scheduledJobFixture({ latest_execution_id: 'execution-latest' }))
    const row = wrapper.get('[data-testid="scheduled-row-job-1"]')

    expect(row.text()).toContain('当前账号没有 api.production 权限')
    expect(wrapper.get('[data-testid="scheduled-edit-job-1"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="scheduled-list-enabled-job-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="scheduled-list-notify-job-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="scheduled-run-job-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="scheduled-latest-execution-job-1"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="scheduled-production-permission"]').text()).toContain('启用或执行前请联系管理员授权')
    expect(wrapper.get('[data-testid="scheduled-fieldset"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="scheduled-save"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-testid="scheduled-edit-job-1"]').trigger('click')
    await wrapper.get('[data-testid="scheduled-enabled-toggle"]').trigger('click')
    await wrapper.get('[data-testid="scheduled-notify-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="scheduled-production-permission"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="scheduled-save"]').attributes('disabled')).toBeUndefined()
  })

  it('keeps non-production schedules readable while disabling every unsupported readonly action', async () => {
    useContextStore().environmentRevisions[0].name = '测试环境'
    setApiTestingAccessProfile({ status: 'active', permissions: ['api.view'] })
    const wrapper = await mountScheduledState(() => scheduledJobFixture({ latest_execution_id: 'execution-latest', notify_feishu: false }))
    const row = wrapper.get('[data-testid="scheduled-row-job-1"]')

    expect(row.text()).toContain('没有接口执行权限')
    expect(wrapper.get('[data-testid="scheduled-edit-job-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="scheduled-list-enabled-job-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="scheduled-list-notify-job-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="scheduled-delete-job-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="scheduled-run-job-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="scheduled-latest-execution-job-1"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="scheduled-fieldset"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="scheduled-save"]').attributes('disabled')).toBeDefined()
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

  it('preserves the saved source and fixed environment when editing from another workbench scope', async () => {
    const original = scheduledJobFixture({ source_revision_id: 'old-source', environment_revision_id: 'old-env', environment_id: 'old-environment' })
    const wrapper = await mountScheduledState(() => original)
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { scheduled_job: original } })
    await wrapper.get('[data-testid="scheduled-edit-job-1"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="scheduled-name"]').setValue('只修改名称')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      name: '只修改名称', source_revision_id: 'old-source', environment_revision_id: 'old-env',
    }))
  })

  it('keeps saved target IDs after changing the editor target type during edit', async () => {
    const original = scheduledJobFixture({ target_type: 'baselines', target_ids: ['baseline-1'] })
    const wrapper = await mountScheduledState(() => original)
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { scheduled_job: original } })
    await wrapper.get('[data-testid="scheduled-edit-job-1"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已选 1 项')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ target_ids: ['baseline-1'] }))
  })

  it('lets an editor remove retired baseline IDs before selecting the replacement', async () => {
    const original = scheduledJobFixture({ target_type: 'baselines', target_ids: ['retired-baseline'] })
    const replacement = scheduledJobFixture({ target_type: 'baselines', target_ids: ['baseline-1'] })
    const wrapper = await mountScheduledState(() => original)
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { scheduled_job: replacement } })

    await wrapper.get('[data-testid="scheduled-edit-job-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="scheduled-missing-targets"]').text()).toContain('1 个已失效目标')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    expect(put).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="scheduled-editor-feedback"]').text()).toContain('先移除失效目标')

    await wrapper.get('[data-testid="scheduled-remove-missing-targets"]').trigger('click')
    expect(wrapper.text()).toContain('选择基线可多选')
    await wrapper.get('[data-testid="scheduled-target-group-select"]').trigger('click')
    expect(wrapper.text()).toContain('选择基线已选 1 项')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()

    expect(put).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ target_ids: ['baseline-1'] }))
  })

  it('replaces a retired scheduled baseline with the active version of the same case', async () => {
    const original = scheduledJobFixture({ target_type: 'baselines', target_ids: ['baseline-retired'] })
    const replacement = scheduledJobFixture({ target_type: 'baselines', target_ids: ['baseline-current'] })
    vi.spyOn(apiClient, 'get').mockImplementation(async url => {
      const path = String(url)
      if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [original] } }
      if (path.startsWith('/api/api-testing/v1/baselines')) return { data: { baselines: [
        baselineFixture({ id: 'baseline-retired', status: 'superseded', case_id: 'case-upgraded', case_version: 10 }),
        baselineFixture({ id: 'baseline-current', status: 'active', case_id: 'case-upgraded', case_version: 11 }),
      ] } }
      if (path.startsWith('/api/api-testing/v1/cases')) return { data: { case_versions: [] } }
      if (path.startsWith('/api/api-testing/v1/tasks')) return { data: { tasks: [] } }
      return { data: {} }
    })
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { scheduled_job: replacement } })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ScheduledJobsView }] })
    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('[data-testid="scheduled-edit-job-1"]').trigger('click')
    expect(wrapper.get('[data-testid="scheduled-missing-targets"]').text()).toContain('1 个已失效目标')
    await wrapper.get('[data-testid="scheduled-replace-missing-targets"]').trigger('click')
    expect(wrapper.text()).toContain('已将 1 个失效基线替换为当前有效版本')
    expect(wrapper.text()).toContain('已选 1 项')

    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ target_ids: ['baseline-current'] }))
  })

  it('shows target loading and read failures instead of claiming targets were deleted', async () => {
    mockScheduledJobAssets()
    const realGet = apiClient.get
    let fail!: (error: Error) => void
    vi.spyOn(apiClient, 'get').mockImplementation(url => String(url).startsWith('/api/api-testing/v1/baselines')
      ? new Promise((_resolve, reject) => { fail = reject })
      : String(url).startsWith('/api/api-testing/v1/scheduled-jobs')
        ? Promise.resolve({ data: { scheduled_jobs: [scheduledJobFixture({})] } })
        : realGet(url))
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ScheduledJobsView }] })
    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('正在读取目标')
    expect(wrapper.get('[data-testid="scheduled-row-job-1"]').text()).toContain('正在校验目标')
    expect(wrapper.get('[data-testid="scheduled-row-job-1"]').text()).not.toContain('执行已阻断')
    expect(wrapper.text()).not.toContain('目标已删除')
    expect(wrapper.get('[data-testid="scheduled-refresh"]').attributes('disabled')).toBeDefined()
    fail(new Error('基线读取超时，请重试'))
    await flushPromises()
    expect(wrapper.text()).toContain('基线读取超时，请重试')
    expect(wrapper.text()).not.toContain('目标已删除')
    expect(wrapper.get('[data-testid="scheduled-run-job-1"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('surfaces delete failure in the page and keeps the row for retry', async () => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({}))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(apiClient, 'delete').mockRejectedValue(new Error('没有删除权限，请联系管理员'))
    const unhandled = vi.fn()
    wrapper.vm.$.appContext.config.errorHandler = unhandled
    await wrapper.get('[data-testid="scheduled-delete-job-1"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[role="alert"]').some(item => item.text().includes('没有删除权限'))).toBe(true)
    expect(wrapper.find('[data-testid="scheduled-row-job-1"]').exists()).toBe(true)
    expect(unhandled).not.toHaveBeenCalled()
  })

  it('rejects a blank name locally and keeps submit failures next to the form without losing edits', async () => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({}))
    const post = vi.spyOn(apiClient, 'post').mockRejectedValue(new Error('保存失败，请刷新列表核对后重试'))
    const unhandled = vi.fn()
    wrapper.vm.$.appContext.config.errorHandler = unhandled
    await wrapper.get('[data-testid="scheduled-target-option"]').trigger('click')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()
    expect(post).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="scheduled-editor-feedback"]').text()).toContain('请输入任务名称')
    await wrapper.get('[data-testid="scheduled-name"]').setValue('保留未保存修改')
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="scheduled-editor-feedback"]').text()).toContain('保存失败')
    expect((wrapper.get('[data-testid="scheduled-name"]').element as HTMLInputElement).value).toBe('保留未保存修改')
    expect(unhandled).not.toHaveBeenCalled()
  })

  it('announces refresh completion and focuses the editor after edit', async () => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({}))
    const focus = vi.spyOn(wrapper.get('[data-testid="scheduled-name"]').element as HTMLInputElement, 'focus')
    await wrapper.get('[data-testid="scheduled-edit-job-1"]').trigger('click')
    await flushPromises()
    expect(focus).toHaveBeenCalled()
    await wrapper.get('[data-testid="scheduled-refresh"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[role="status"]').some(item => item.text().includes('已刷新'))).toBe(true)
  })


  it('does not classify an old-source case as deleted from the current-source cache', async () => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({ source_revision_id: 'old-source', target_type: 'cases', target_ids: ['old-case'] }))
    expect(wrapper.text()).not.toContain('目标已删除')
    expect(wrapper.get('[data-testid="scheduled-row-job-1"]').text()).toContain('原接口版本')
    expect(wrapper.get('[data-testid="scheduled-run-job-1"]').attributes('disabled')).toBeUndefined()
  })

  it('serializes row switches so a pending disable cannot be undone by a notification update', async () => {
    const original = scheduledJobFixture({ notify_feishu: false })
    const wrapper = await mountScheduledState(() => original)
    let complete!: (value: any) => void
    const put = vi.spyOn(apiClient, 'put').mockReturnValue(new Promise(resolve => { complete = resolve }))
    await wrapper.get('[data-testid="scheduled-list-enabled-job-1"]').trigger('click')
    await wrapper.get('[data-testid="scheduled-list-notify-job-1"]').trigger('click')
    expect(put).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[data-testid="scheduled-delete-job-1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('正在保存')
    complete({ data: { scheduled_job: { ...original, enabled: false } } })
    await flushPromises()
    expect(wrapper.get('[data-testid="scheduled-list-enabled-job-1"]').attributes('aria-checked')).toBe('false')
    expect(wrapper.text()).toContain('已停用')
  })

  it('preserves a non-default daily time when only the name is edited', async () => {
    const original = scheduledJobFixture({ schedule_type: 'daily', cron_expression: '0 8 * * *' })
    const wrapper = await mountScheduledState(() => original)
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { scheduled_job: original } })
    await wrapper.get('[data-testid="scheduled-edit-job-1"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ cron_expression: '0 8 * * *' }))
  })

  it('labels schedule shortcuts with their exact time before replacing a custom time', async () => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({ schedule_type: 'daily', cron_expression: '0 8 * * *' }))
    await wrapper.get('[data-testid="scheduled-edit-job-1"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="scheduled-schedule-daily"]').text()).toBe('每天 02:00')
    expect(wrapper.get('[data-testid="scheduled-schedule-weekly"]').text()).toBe('每周一 09:00')
    expect(wrapper.text()).toContain('会使用按钮标注的默认时间')
    expect(wrapper.text()).toContain('其他执行时间请保留或选择“自定义表达式”')
  })


  it('omits the absent fixed revision when toggling a latest-environment job', async () => {
    const original = scheduledJobFixture({ environment_strategy: 'latest_environment', environment_revision_id: null, enabled: false })
    const wrapper = await mountScheduledState(() => original)
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { scheduled_job: { ...original, enabled: true } } })
    await wrapper.get('[data-testid="scheduled-list-enabled-job-1"]').trigger('click')
    await flushPromises()
    const wirePayload = JSON.parse(JSON.stringify(put.mock.calls[0][1]))
    expect(wirePayload).not.toHaveProperty('environment_revision_id')
    expect(wirePayload).toMatchObject({ environment_strategy: 'latest_environment', environment_id: 'env-1', enabled: true })
  })


  it('keeps a pinned earlier case version executable even within the current source', async () => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({ target_type: 'cases', target_ids: ['earlier-case-version'] }))
    expect(wrapper.text()).not.toContain('目标已删除')
    expect(wrapper.get('[data-testid="scheduled-run-job-1"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="scheduled-row-job-1"]').text()).toContain('固定用例版本')
  })

  it('does not announce successful refresh when the context store swallowed a read error', async () => {
    const wrapper = await mountScheduledState(() => scheduledJobFixture({}))
    const context = useContextStore()
    vi.spyOn(context, 'loadOptions').mockImplementation(async () => { context.error = '项目与环境读取超时' })
    await wrapper.get('[data-testid="scheduled-name"]').setValue('保留正在编辑的名称')
    await wrapper.get('[data-testid="scheduled-refresh"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[role="alert"]').some(item => item.text().includes('项目与环境读取超时'))).toBe(true)
    expect(wrapper.text()).not.toContain('已刷新')
    expect((wrapper.get('[data-testid="scheduled-name"]').element as HTMLInputElement).value).toBe('保留正在编辑的名称')
  })

  it('preserves the saved project even if the workspace project changes during an edit', async () => {
    const original = scheduledJobFixture({})
    const wrapper = await mountScheduledState(() => original)
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { scheduled_job: original } })
    await wrapper.get('[data-testid="scheduled-edit-job-1"]').trigger('click')
    await flushPromises()
    useContextStore().projectId = 'other-project'
    await wrapper.get('[data-testid="scheduled-save"]').trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ project_id: 'project-1' }))
  })


  it('does not offer a ready task with zero executable baselines as a runnable target', async () => {
    mockScheduledJobAssets()
    const get = apiClient.get
    vi.spyOn(apiClient, 'get').mockImplementation(url => String(url).startsWith('/api/api-testing/v1/tasks')
      ? Promise.resolve({ data: { tasks: [{ id: 'task-empty', name: '无基线的任务', selected_endpoint_ids: [], state: 'ready', runnable_baseline_count: 0 }] } })
      : get(url))
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ScheduledJobsView }] })
    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()
    await wrapper.get('[data-testid="scheduled-target-type"]').setValue('task')
    const option = wrapper.get('[data-testid="scheduled-target-option"]')
    expect(option.attributes('disabled')).toBeDefined()
    expect(option.text()).toContain('待采纳基线')
    expect(option.text()).toContain('0 条可执行基线')
    expect(option.text()).not.toContain('ready')
  })

  it('uses runnable active baseline ownership when deciding whether a saved task is selectable', async () => {
    vi.spyOn(apiClient, 'get').mockImplementation(async url => {
      const path = String(url)
      if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [] } }
      if (path.startsWith('/api/api-testing/v1/baselines')) return { data: { baselines: [baselineFixture({})] } }
      if (path.startsWith('/api/api-testing/v1/cases')) return { data: { case_versions: [{
        id: 'legacy-unassigned', case_id: 'legacy-case', endpoint_id: 'endpoint-1', status: 'active', origin: 'manual', version: 1,
        group_name: '', name: '旧未归属用例', purpose: '', app_package: '', app_name: '', business: '', priority: 'P1',
        request: { method: 'GET', path: '/legacy', service: '', path_params: {}, query: {}, headers: {}, cookies: {}, body: {} },
        data_rows: [], assertions: [], extractions: [], dependencies: [], processing: { pre: [], post: [] }, validation_summary: {},
      }] } }
      if (path.startsWith('/api/api-testing/v1/tasks')) return { data: { tasks: [{
        id: 'task-runnable', project_id: 'project-1', source_revision_id: 'source-1', environment_revision_id: 'env-revision-1',
        name: '已保存可执行任务', state: 'ready', selected_endpoint_ids: ['endpoint-1'], runnable_baseline_count: 1,
      }] } }
      return { data: {} }
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ScheduledJobsView }] })
    const wrapper = mount(ScheduledJobsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('[data-testid="scheduled-target-type"]').setValue('task')
    const option = wrapper.get('[data-testid="scheduled-target-option"]')

    expect(option.attributes('disabled')).toBeUndefined()
    expect(option.text()).toContain('校园应用 · 校园共享')
    expect(option.text()).not.toContain('应用未配置或已移除')
    await option.trigger('click')
    expect(wrapper.text()).toContain('选择已保存任务已选 1 项')
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

function mockScheduledJobAssets(includeOneTime = false): void {
  vi.spyOn(apiClient, 'get').mockImplementation(async url => {
    const path = String(url)
    if (path.startsWith('/api/api-testing/v1/scheduled-jobs')) return { data: { scheduled_jobs: [] } }
    if (path.startsWith('/api/api-testing/v1/baselines')) {
      return {
        data: {
          baselines: [
            baselineFixture({ id: 'baseline-1', group_name: '发版冒烟', case_name: '登录成功用例' }),
            ...(includeOneTime ? [baselineFixture({
              id: 'baseline-one-time',
              case_id: 'case-one-time',
              case_version_id: 'case-version-one-time',
              case_name: '数据初始化 - 一次性人工验证',
              group_name: '发版冒烟',
              tags: ['一次性'],
            })] : []),
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
    allow_one_time_baselines: false,
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
