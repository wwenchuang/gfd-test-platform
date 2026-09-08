// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { LoadScenarioDefinition } from '../api/contracts'
import LoadScenarioAdvancedEditor from './LoadScenarioAdvancedEditor.vue'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../api/client', () => ({ apiClient: mocks }))
const definition = {
  name: '数据场景', description: '', mode: 'single_interface',
  steps: [{ id: 'query', name: '查询', scope: 'iteration', action: 'http_request', assertions: [], extractions: [], sleep_ms: 0, side_effect: 'readonly' }],
  dataset_contract: { dataset_id: null, usage_mode: 'cycle', variables: ['keyword'] },
  risk: { level: 'low', ownership_variable: null, notes: '' }, source_snapshot: { type: 'manual' },
} as LoadScenarioDefinition

describe('LoadScenarioAdvancedEditor datasets', () => {
  beforeEach(() => {
    mocks.get.mockResolvedValue({ data: { datasets: [{ id: 'd1', name: '关键词', row_count: 2, field_schema: { fields: [{ name: 'keyword' }] } }] } })
    mocks.post.mockReset()
  })

  it('loads project dataset metadata and binds selected id while preserving variables', async () => {
    const wrapper = mount(LoadScenarioAdvancedEditor, { props: { modelValue: definition, projectId: 'project-1' } })
    await flushPromises()
    expect(wrapper.find('[data-testid="scenario-dataset-select"]').exists()).toBe(true)
    await wrapper.get('[data-testid="scenario-dataset-select"]').setValue('d1')
    expect(mocks.get).toHaveBeenCalledWith('/api/api-testing/v1/load-datasets?project_id=project-1')
    expect(wrapper.emitted('update:modelValue')?.[0]?.[0]).toMatchObject({ dataset_contract: { dataset_id: 'd1', variables: ['keyword'] } })
    expect(wrapper.text()).toContain('keyword')
  })

  it('imports UTF-8 JSON using the existing base64 API and never displays returned preview values', async () => {
    mocks.post.mockResolvedValue({ data: { dataset: { id: 'new-data', name: '搜索数据', row_count: 1, fields: ['keyword'], preview_rows: [{ keyword: 'private-returned-preview' }] } } })
    const wrapper = mount(LoadScenarioAdvancedEditor, { props: { modelValue: definition, projectId: 'project-1' } })
    await flushPromises()
    expect(wrapper.find('[data-testid="scenario-dataset-file"]').exists()).toBe(true)
    const file = new File(['[{"keyword":"模型"}]'], '搜索数据.json', { type: 'application/json' })
    Object.defineProperty(wrapper.get('[data-testid="scenario-dataset-file"]').element, 'files', { value: [file] })
    await wrapper.get('[data-testid="scenario-dataset-file"]').trigger('change')
    await wrapper.get('[data-testid="scenario-dataset-import"]').trigger('click')
    await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledOnce())
    await flushPromises()
    const payload = mocks.post.mock.calls[0][1]
    expect(payload).toMatchObject({ project_id: 'project-1', filename: '搜索数据.json', usage_mode: 'cycle' })
    expect(new TextDecoder().decode(Uint8Array.from(atob(payload.content_base64), c => c.charCodeAt(0)))).toBe('[{"keyword":"模型"}]')
    expect(wrapper.text()).not.toContain('private-returned-preview')
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toMatchObject({ dataset_contract: { dataset_id: 'new-data', variables: ['keyword'] } })
  })
})
