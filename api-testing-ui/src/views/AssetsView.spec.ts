// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import { useSetupStore } from '../stores/setup'
import AssetsView from './AssetsView.vue'

describe('AssetsView Apifox actions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    vi.spyOn(apiClient, 'get').mockImplementation(async (path) => {
      if (path.endsWith('/workspace')) return { data: { workspace: null } } as never
      if (path.endsWith('/context-options')) {
        return { data: {
          projects: [{ id: 'project-1', name: '3D 家用' }],
          source_revisions: [], environment_revisions: [],
        } } as never
      }
      return { data: { credential: {
        provider: 'apifox', configured: true, fingerprint: 'a1b2c3d4e5f6', updated_at: null,
      } } } as never
    })
  })

  it('shows operation-specific loading text and keeps saving as a separate confirmation', async () => {
    const wrapper = mount(AssetsView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()
    const setup = useSetupStore()

    setup.busy = true
    setup.apifoxOperation = 'loading_projects'
    await nextTick()
    expect(buttonText(wrapper)).toContain('正在读取项目…')

    setup.apifoxOperation = 'loading_context'
    await nextTick()
    expect(buttonText(wrapper)).toContain('正在读取环境…')

    setup.apifoxOperation = 'checking_update'
    await nextTick()
    expect(buttonText(wrapper)).toContain('正在检查更新…')

    setup.busy = false
    setup.apifoxOperation = null
    setup.preview = {
      id: 'preview-1', project_id: 'project-1', source_id: 'source-1', previous_revision_id: null,
      candidate_revision_id: 'candidate-1', added_count: 3, changed_count: 1, removed_count: 0, changes: [],
    }
    setup.apifoxPreview = {
      source_preview: setup.preview,
      environment_candidate: { name: '生产环境', secret_placeholders: [] },
    }
    await nextTick()

    expect(buttonText(wrapper)).toContain('保存为新版本')
    expect(wrapper.text()).toContain('检查更新只生成预览，不会覆盖当前版本')
  })
})

function buttonText(wrapper: ReturnType<typeof mount>): string {
  return wrapper.findAll('button').map(item => item.text()).join('|')
}
