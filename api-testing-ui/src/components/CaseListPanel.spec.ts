// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ApiEndpoint, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import { replaceTestApplications } from '../utils/testApplications'
import CaseListPanel from './CaseListPanel.vue'

const ENDPOINTS = [
  {
    id: 'endpoint-add',
    method: 'POST',
    path: '/print3d/api/v1/collection/add',
    summary: '添加收藏',
    tags: [],
    operation: {
      'x-apifox-folder': { path: ['家用业务', 'app接口', '我的收藏'], name: '我的收藏' },
    },
  },
  {
    id: 'endpoint-list',
    method: 'GET',
    path: '/print3d/api/v1/collection/page',
    summary: '我的收藏列表',
    tags: ['家用业务', 'app接口', '我的收藏'],
  },
] as ApiEndpoint[]

const SAVED = {
  id: 'version-add',
  case_id: 'case-add',
  endpoint_id: 'endpoint-add',
  status: 'draft',
  origin: 'imported',
  version: 1,
  group_name: '',
  validation_summary: {},
  name: '添加收藏 - 基础正向流程',
  purpose: '验证添加收藏',
  business: 'home',
  priority: 'P1',
  request: { method: 'POST', path: '/print3d/api/v1/collection/add', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
  data_rows: [],
  assertions: [],
  extractions: [],
  dependencies: [],
  processing: { pre: [], post: [] },
} as CaseVersion

const PREVIEW = {
  id: 'basic-positive-endpoint-list',
  endpoint_id: 'endpoint-list',
  origin: 'imported',
  workflow: {
    kind: 'read_only',
    label: '只读查询',
    risk: 'low',
    requires_setup: false,
    requires_cleanup: false,
    baseline_policy: 'direct',
    reason: '无业务状态变更，可直接校验业务码、结构和关键数据字段',
  },
  case: {
    name: '我的收藏列表 - 基础正向流程',
    purpose: '验证我的收藏列表',
    priority: 'P1',
    request: { method: 'GET', path: '/print3d/api/v1/collection/page', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
    data_rows: [],
    assertions: [],
    extractions: [],
    dependencies: [],
    processing: { pre: [], post: [] },
  },
} as GeneratedCasePreview

const DEVICE_ENDPOINT = {
  id: 'endpoint-device',
  method: 'GET',
  path: '/pmc/api/v1/iot/device/info',
  summary: '查询设备详情',
  tags: ['本地测试', '启迪设备', '设备详情'],
} as ApiEndpoint

const DEVICE_CASE = {
  ...SAVED,
  id: 'version-device',
  case_id: 'case-device',
  endpoint_id: 'endpoint-device',
  name: '查询设备详情 - 基础正向流程',
  purpose: '验证设备详情查询',
  request: { ...SAVED.request, method: 'GET', path: '/pmc/api/v1/iot/device/info' },
} as CaseVersion

const EXTRA_ENDPOINTS = [
  { id: 'endpoint-order', method: 'POST', path: '/order/create', summary: '创建订单', tags: ['共享业务', '订单接口'] },
  { id: 'endpoint-user', method: 'GET', path: '/users/info', summary: '用户详情', tags: ['家用业务', '登录注册'] },
] as ApiEndpoint[]

const EXTRA_CASES = EXTRA_ENDPOINTS.map((endpoint, index) => ({
  ...SAVED,
  id: `version-extra-${index}`,
  case_id: `case-extra-${index}`,
  endpoint_id: endpoint.id,
  name: `${endpoint.summary} - 基础正向流程`,
  purpose: `验证${endpoint.summary}`,
  request: { ...SAVED.request, method: endpoint.method, path: endpoint.path },
})) as CaseVersion[]

describe('CaseListPanel', () => {
  it('explains overlapping progress counts and separates candidates from saved cases', async () => {
    const wrapper = mount(CaseListPanel, { props: { endpoints: ENDPOINTS, versions: [SAVED], generatedPreviews: [PREVIEW] } })
    expect(wrapper.text()).toContain('已保存 1 条 · 未保存候选 1 条')
    expect(wrapper.get('[data-testid="case-view-guidance"]').text()).toContain('数量不能相加')
    await wrapper.get('[data-testid="case-work-view-debugged"]').trigger('click')
    expect(wrapper.get('[data-testid="case-view-guidance"]').text()).toContain('包括通过、失败和异常')
    await wrapper.get('[data-testid="case-work-view-candidate"]').trigger('click')
    expect(wrapper.get('[data-testid="case-view-guidance"]').text()).toContain('先检查内容并保存')
    expect(wrapper.text()).toContain(PREVIEW.case.name)
  })
  it('opens the interface picker from the empty case list', async () => {
    const wrapper = mount(CaseListPanel, { props: { endpoints: ENDPOINTS, versions: [] } })

    await wrapper.get('[data-testid="case-list-open-endpoints"]').trigger('click')

    expect(wrapper.emitted('open-endpoints')).toHaveLength(1)
  })

  it('shows application plus its package-scoped business without package or business IDs', () => {
    replaceTestApplications([{
      package: 'com.example.school', name: '校园应用', enabled: true,
      business_lines: [{ id: 'shared', name: '校园共享', enabled: true }],
    }])
    const schoolCase = {
      ...SAVED,
      app_package: 'com.example.school',
      app_name: '校园应用旧名称',
      business: 'shared',
    } as CaseVersion
    const wrapper = mount(CaseListPanel, {
      props: { endpoints: ENDPOINTS, versions: [schoolCase] },
    })

    const text = wrapper.get('[data-testid="case-version-version-add"]').text()
    expect(text).toContain('校园应用 · 校园共享')
    expect(text).not.toContain('com.example.school')
    expect(text).not.toContain('shared')
  })

  it('groups generated previews and saved cases by Apifox folder', () => {
    const wrapper = mount(CaseListPanel, {
      props: {
        endpoints: ENDPOINTS,
        versions: [SAVED],
        generatedPreviews: [PREVIEW],
        selectedEndpointIds: ['endpoint-add'],
      },
    })

    expect(wrapper.get('[data-testid="case-list-group-家用业务 / app接口 / 我的收藏"]').text()).toContain('2')
    expect(wrapper.get('[data-testid="case-version-version-add"]').text()).toContain('添加收藏 - 基础正向流程')
    expect(wrapper.get('[data-testid="case-version-version-add"]').text()).toContain('v1 · 平台')
    expect(wrapper.get('[data-testid="case-version-version-add"]').text()).toContain('家用')
    expect(wrapper.get('[data-testid="case-preview-basic-positive-endpoint-list"]').text()).toContain('我的收藏列表 - 基础正向流程')
    expect(wrapper.get('[data-testid="case-preview-basic-positive-endpoint-list"]').text()).toContain('候选 · 平台')
    expect(wrapper.get('[data-testid="case-preview-basic-positive-endpoint-list"]').text()).toContain('只读查询')
    expect(wrapper.get('[data-testid="case-preview-basic-positive-endpoint-list"]').text()).toContain('可直接进入基线校验')
  })

  it('emits edit, save, discard, run, delete, and task-scope operations', async () => {
    const wrapper = mount(CaseListPanel, {
      props: {
        endpoints: ENDPOINTS,
        versions: [SAVED],
        generatedPreviews: [PREVIEW],
        selectedEndpointIds: ['endpoint-add'],
        activeVersionId: 'version-add',
        activePreviewId: 'basic-positive-endpoint-list',
      },
    })

    await wrapper.get('[data-testid="case-preview-edit-basic-positive-endpoint-list"]').trigger('click')
    await wrapper.get('[data-testid="case-preview-save-basic-positive-endpoint-list"]').trigger('click')
    await wrapper.get('[data-testid="case-preview-discard-basic-positive-endpoint-list"]').trigger('click')
    await wrapper.get('[data-testid="case-version-edit-version-add"]').trigger('click')
    await wrapper.get('[data-testid="case-version-run-version-add"]').trigger('click')
    await wrapper.get('[data-testid="case-version-delete-version-add"]').trigger('click')
    await wrapper.get('[data-testid="case-version-scope-version-add"]').trigger('click')

    expect(wrapper.emitted('edit-preview')?.[0]).toEqual([PREVIEW])
    expect(wrapper.emitted('save-preview')?.[0]).toEqual([PREVIEW])
    expect(wrapper.emitted('discard-preview')?.[0]).toEqual(['basic-positive-endpoint-list'])
    expect(wrapper.emitted('edit-version')?.[0]).toEqual([SAVED])
    expect(wrapper.emitted('run-version')?.[0]).toEqual([SAVED])
    expect(wrapper.emitted('delete-version')?.[0]).toEqual([SAVED])
    expect(wrapper.emitted('toggle-scope')?.[0]).toEqual(['endpoint-add'])
    expect(wrapper.get('[data-testid="case-version-scope-version-add"]').attributes('title')).toBe('从当前任务范围移除')
  })

  it('collapses groups independently and keeps search results visible', async () => {
    const wrapper = mount(CaseListPanel, {
      props: {
        endpoints: [...ENDPOINTS, DEVICE_ENDPOINT],
        versions: [SAVED, DEVICE_CASE],
        generatedPreviews: [PREVIEW],
      },
    })

    const collectionGroup = '家用业务 / app接口 / 我的收藏'
    const deviceGroup = '本地测试 / 启迪设备 / 设备详情'

    expect(wrapper.get('[data-testid="case-list-group-toggle-本地测试"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="case-version-version-device"]').exists()).toBe(true)

    await wrapper.get('[data-testid="case-list-group-toggle-家用业务"]').trigger('click')
    await wrapper.get('[data-testid="case-list-group-toggle-家用业务 / app接口"]').trigger('click')
    await wrapper.get(`[data-testid="case-list-group-toggle-${collectionGroup}"]`).trigger('click')

    expect(wrapper.get(`[data-testid="case-list-group-toggle-${collectionGroup}"]`).attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="case-version-version-device"]').exists()).toBe(true)

    await wrapper.get('[data-testid="case-list-collapse-all"]').trigger('click')

    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="case-version-version-device"]').exists()).toBe(false)

    await wrapper.get('[data-testid="case-list-search"]').setValue('设备')

    expect(wrapper.get(`[data-testid="case-list-group-toggle-${deviceGroup}"]`).attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-testid="case-version-version-device"]').text()).toContain('查询设备详情')
    expect(wrapper.get('[data-testid="case-version-version-device"] mark').text()).toBe('设备')
    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(false)

    await wrapper.get('[data-testid="case-list-search"]').setValue('')
    await wrapper.get('[data-testid="case-list-expand-all"]').trigger('click')

    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="case-version-version-device"]').exists()).toBe(true)
  })

  it('uses a compact recursive directory and expands only the first root path by default', async () => {
    const wrapper = mount(CaseListPanel, {
      props: {
        endpoints: [...ENDPOINTS, DEVICE_ENDPOINT, ...EXTRA_ENDPOINTS],
        versions: [SAVED, DEVICE_CASE, ...EXTRA_CASES],
        generatedPreviews: [PREVIEW],
      },
    })
    const firstGroup = '本地测试 / 启迪设备 / 设备详情'
    const laterGroup = '共享业务 / 订单接口'

    expect(wrapper.get('[data-testid="case-list-group-toolbar"]').text()).toContain('分组浏览')
    expect(wrapper.get('[data-testid="case-list-group-summary"]').text()).toContain('3 个根目录')
    expect(wrapper.find('.case-group-index').exists()).toBe(false)
    expect(wrapper.get(`[data-testid="case-list-group-toggle-${firstGroup}"]`).attributes('aria-expanded')).toBe('true')
    expect(wrapper.find(`[data-testid="case-list-group-toggle-${laterGroup}"]`).exists()).toBe(false)
    expect(wrapper.find('[data-testid="case-version-version-extra-0"]').exists()).toBe(false)

    await wrapper.get('[data-testid="case-list-group-toggle-共享业务"]').trigger('click')
    await wrapper.get(`[data-testid="case-list-group-toggle-${laterGroup}"]`).trigger('click')

    expect(wrapper.get(`[data-testid="case-list-group-toggle-${laterGroup}"]`).attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-testid="case-version-version-extra-0"]').text()).toContain('创建订单')
  })

  it('groups saved cases by user-managed group and uses a searchable movement picker', async () => {
    const groupedCase = {
      ...SAVED,
      id: 'version-release',
      case_id: 'case-release',
      name: '添加收藏 - 发版主链',
      group_name: '发版回归',
    } as CaseVersion
    const wrapper = mount(CaseListPanel, {
      props: {
        endpoints: [...ENDPOINTS, DEVICE_ENDPOINT],
        versions: [groupedCase, DEVICE_CASE],
        generatedPreviews: [],
      },
    })

    expect(wrapper.get('[data-testid="case-list-group-发版回归"]').text()).toContain('1')
    await wrapper.get('[data-testid="case-list-group-toggle-发版回归"]').trigger('click')
    expect(wrapper.get('[data-testid="case-version-version-release"]').text()).toContain('添加收藏 - 发版主链')
    expect(wrapper.find('[data-testid="case-version-group-version-release"] select').exists()).toBe(false)

    await wrapper.get('[data-testid="case-version-group-version-release"]').trigger('click')
    await wrapper.get('[data-testid="case-group-picker-search"]').setValue('收藏链路')
    await wrapper.get('[data-testid="case-group-picker-create"]').trigger('click')

    expect(wrapper.emitted('update-version-group')?.[0]).toEqual([groupedCase, '收藏链路'])
  })

  it('combines work views with search and explains filtered empty states', async () => {
    const workflowCase = {
      ...DEVICE_CASE,
      processing: {
        pre: [], post: [],
        setup_steps: [{
          name: '查询设备', enabled: true,
          request: DEVICE_CASE.request,
          assertions: [], extractions: [], required_variables: [],
        }],
      },
    } as CaseVersion
    const oneTimeCase = { ...EXTRA_CASES[0], group_name: 'API Test / 一次性' } as CaseVersion
    const wrapper = mount(CaseListPanel, {
      props: {
        endpoints: [...ENDPOINTS, DEVICE_ENDPOINT, ...EXTRA_ENDPOINTS],
        versions: [SAVED, workflowCase, oneTimeCase],
        generatedPreviews: [PREVIEW],
        selectedEndpointIds: ['endpoint-add'],
      },
    })

    expect(wrapper.get('[data-testid="case-work-view-all"]').text()).toContain('4')
    expect(wrapper.get('[data-testid="case-work-view-task"]').text()).toContain('1')
    expect(wrapper.get('[data-testid="case-work-view-orchestrated"]').text()).toContain('1')
    expect(wrapper.get('[data-testid="case-work-view-one-time"]').text()).toContain('1')
    expect(wrapper.get('[data-testid="case-work-view-candidate"]').text()).toContain('1')

    await wrapper.get('[data-testid="case-list-search"]').setValue('设备')
    expect(wrapper.get('[data-testid="case-work-view-all"]').text()).toContain('1')
    expect(wrapper.get('[data-testid="case-work-view-task"]').text()).toContain('0')
    expect(wrapper.get('[data-testid="case-work-view-orchestrated"]').text()).toContain('1')
    expect(wrapper.get('[data-testid="case-work-view-one-time"]').text()).toContain('0')
    expect(wrapper.get('[data-testid="case-work-view-candidate"]').text()).toContain('0')

    await wrapper.get('[data-testid="case-list-search"]').setValue('')

    await wrapper.get('[data-testid="case-work-view-orchestrated"]').trigger('click')
    expect(wrapper.find('[data-testid="case-version-version-device"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(false)

    await wrapper.get('[data-testid="case-list-search"]').setValue('不存在')
    expect(wrapper.get('[data-testid="case-list-empty"]').text()).toContain('当前筛选下没有匹配用例')
  })

  it('shows lifecycle badges and filters ordinary, debugged, and baseline cases', async () => {
    const ordinary = { ...SAVED, id: 'version-ordinary', case_id: 'case-ordinary', lifecycle: {} } as CaseVersion
    const debugged = {
      ...DEVICE_CASE,
      id: 'version-debugged', case_id: 'case-debugged',
      lifecycle: { debug_status: 'FAILED', debug_execution_id: 'execution-debug' },
    } as CaseVersion
    const baseline = {
      ...EXTRA_CASES[0],
      id: 'version-baseline', case_id: 'case-baseline',
      lifecycle: { debug_status: 'PASSED', debug_execution_id: 'execution-baseline', baseline_status: 'active', baseline_id: 'baseline-1', regression_status: 'PASSED' },
    } as CaseVersion
    const wrapper = mount(CaseListPanel, {
      props: {
        endpoints: [...ENDPOINTS, DEVICE_ENDPOINT, ...EXTRA_ENDPOINTS],
        versions: [ordinary, debugged, baseline],
      },
    })
    await wrapper.get('[data-testid="case-list-expand-all"]').trigger('click')

    expect(wrapper.get('[data-testid="case-version-version-debugged"]').text()).toContain('调试失败')
    expect(wrapper.get('[data-testid="case-version-version-baseline"]').text()).toContain('已基线')
    expect(wrapper.get('[data-testid="case-version-version-baseline"]').text()).toContain('回归通过')
    await wrapper.get('[data-testid="case-version-debug-history-version-debugged"]').trigger('click')
    await wrapper.get('[data-testid="case-version-baseline-version-baseline"]').trigger('click')
    expect(wrapper.emitted('open-debug-history')?.[0]).toEqual([debugged])
    expect(wrapper.emitted('open-baseline')?.[0]).toEqual([baseline])

    await wrapper.get('[data-testid="case-work-view-regular"]').trigger('click')
    expect(wrapper.find('[data-testid="case-version-version-ordinary"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="case-version-version-debugged"]').exists()).toBe(false)

    await wrapper.get('[data-testid="case-work-view-debugged"]').trigger('click')
    expect(wrapper.find('[data-testid="case-version-version-debugged"]').exists()).toBe(true)

    await wrapper.get('[data-testid="case-work-view-baseline"]').trigger('click')
    expect(wrapper.find('[data-testid="case-version-version-baseline"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="case-version-version-ordinary"]').exists()).toBe(false)
  })

  it('selects saved cases and emits a batch group movement', async () => {
    const wrapper = mount(CaseListPanel, {
      props: {
        endpoints: [...ENDPOINTS, DEVICE_ENDPOINT],
        versions: [SAVED, DEVICE_CASE],
        generatedPreviews: [],
      },
    })
    await wrapper.get('[data-testid="case-list-expand-all"]').trigger('click')
    await wrapper.get('[data-testid="case-version-select-version-add"]').setValue(true)
    await wrapper.get('[data-testid="case-version-select-version-device"]').setValue(true)

    expect(wrapper.get('[data-testid="case-batch-toolbar"]').text()).toContain('已选 2 条')
    await wrapper.get('[data-testid="case-batch-move"]').trigger('click')
    await wrapper.get('[data-testid="case-group-picker-search"]').setValue('冒烟回归')
    await wrapper.get('[data-testid="case-group-picker-create"]').trigger('click')

    expect(wrapper.emitted('update-version-groups')?.[0]).toEqual([
      ['version-add', 'version-device'],
      '冒烟回归',
    ])

    await wrapper.get('[data-testid="case-batch-clear"]').trigger('click')
    expect(wrapper.find('[data-testid="case-batch-toolbar"]').exists()).toBe(false)
  })
})
