// @vitest-environment jsdom
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { expect, it, vi } from 'vitest'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'
import RunsView from './RunsView.vue'

it('selects a record through the actual route and retains unrelated query values', async () => {
  const pinia = createPinia(); setActivePinia(pinia)
  const context = useContextStore(), executions = useExecutionsStore()
  vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
  vi.spyOn(context, 'loadOptions').mockResolvedValue()
  vi.spyOn(executions, 'load').mockResolvedValue()
  const select = vi.spyOn(executions, 'select').mockResolvedValue()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/runs', component: RunsView }] })
  await router.push('/runs?endpointId=endpoint-1&other=preserved'); await router.isReady()
  const wrapper = mount(RunsView, { global: { plugins: [pinia, router], stubs: { ExecutionConsole: true, ExecutionDetailDrawer: true } } })
  await flushPromises()
  wrapper.findComponent({ name: 'ExecutionConsole' }).vm.$emit('select', 'execution-new')
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({ endpointId: 'endpoint-1', other: 'preserved', executionId: 'execution-new' })
  expect(select).toHaveBeenCalledWith('execution-new')
  wrapper.unmount()
})
