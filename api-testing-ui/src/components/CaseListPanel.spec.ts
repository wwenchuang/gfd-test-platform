// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ApiEndpoint, CaseVersion, GeneratedCasePreview } from '../api/contracts'
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
  validation_summary: {},
  name: '添加收藏 - 基础正向流程',
  purpose: '验证添加收藏',
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
    expect(wrapper.get('[data-testid="case-preview-basic-positive-endpoint-list"]').text()).toContain('我的收藏列表 - 基础正向流程')
    expect(wrapper.get('[data-testid="case-preview-basic-positive-endpoint-list"]').text()).toContain('候选 · 平台')
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

    expect(wrapper.get('[data-testid="case-list-group-toggle-家用业务 / app接口 / 我的收藏"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="case-version-version-device"]').exists()).toBe(true)

    await wrapper.get(`[data-testid="case-list-group-toggle-${collectionGroup}"]`).trigger('click')

    expect(wrapper.get(`[data-testid="case-list-group-toggle-${collectionGroup}"]`).attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="case-version-version-device"]').exists()).toBe(true)

    await wrapper.get('[data-testid="case-list-collapse-all"]').trigger('click')

    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="case-version-version-device"]').exists()).toBe(false)

    await wrapper.get('[data-testid="case-list-search"]').setValue('设备')

    expect(wrapper.get(`[data-testid="case-list-group-toggle-${deviceGroup}"]`).attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-testid="case-version-version-device"]').text()).toContain('查询设备详情')
    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(false)

    await wrapper.get('[data-testid="case-list-expand-all"]').trigger('click')
    await wrapper.get('[data-testid="case-list-search"]').setValue('')

    expect(wrapper.find('[data-testid="case-version-version-add"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="case-version-version-device"]').exists()).toBe(true)
  })

  it('uses a compact group directory and collapses extra groups by default when many groups exist', async () => {
    const wrapper = mount(CaseListPanel, {
      props: {
        endpoints: [...ENDPOINTS, DEVICE_ENDPOINT, ...EXTRA_ENDPOINTS],
        versions: [SAVED, DEVICE_CASE, ...EXTRA_CASES],
        generatedPreviews: [PREVIEW],
      },
    })
    const firstGroup = '本地测试 / 启迪设备 / 设备详情'
    const laterGroup = '共享业务 / 订单接口'

    expect(wrapper.get('[data-testid="case-list-group-jump-家用业务 / app接口 / 我的收藏"]').text()).toContain('2')
    expect(wrapper.get(`[data-testid="case-list-group-toggle-${firstGroup}"]`).attributes('aria-expanded')).toBe('true')
    expect(wrapper.get(`[data-testid="case-list-group-toggle-${laterGroup}"]`).attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('[data-testid="case-version-version-extra-0"]').exists()).toBe(false)

    await wrapper.get(`[data-testid="case-list-group-jump-${laterGroup}"]`).trigger('click')

    expect(wrapper.get(`[data-testid="case-list-group-toggle-${laterGroup}"]`).attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-testid="case-version-version-extra-0"]').text()).toContain('创建订单')
  })
})
