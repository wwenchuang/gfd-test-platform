// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { useRawEditorGuard } from './rawEditorGuard'

describe('raw editor navigation guard', () => {
  it('retains raw content when navigation is cancelled and permits an explicit discard', async () => {
    let pending = true
    const discard = vi.fn(() => { pending = false })
    const page = defineComponent({
      setup() {
        useRawEditorGuard(ref({ hasUnappliedRaw: () => pending, discardUnappliedRaw: discard }))
        return () => 'editor'
      },
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: page }, { path: '/other', component: { template: '<div>other</div>' } },
    ] })
    await router.push('/'); await router.isReady()
    const wrapper = mount({ template: '<router-view />' }, { global: { plugins: [router] } })
    await flushPromises()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    await router.push('/other'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(discard).not.toHaveBeenCalled()
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    await router.push('/other'); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/other')
    expect(discard).not.toHaveBeenCalled()
    expect(confirm).toHaveBeenCalledTimes(2)
    wrapper.unmount(); confirm.mockRestore()
  })
})
