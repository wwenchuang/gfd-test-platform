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
})
