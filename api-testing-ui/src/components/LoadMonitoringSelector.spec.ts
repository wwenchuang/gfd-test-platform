// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { defineComponent, ref } from 'vue'
import LoadMonitoringSelector from './LoadMonitoringSelector.vue'
import EnvironmentMonitoringPanel from './EnvironmentMonitoringPanel.vue'
import { monitoringApi, type MonitoringSelection } from '../api/monitoring'
vi.mock('../api/monitoring', () => ({ monitoringApi: { list: vi.fn(), save: vi.fn(), check: vi.fn(), disable: vi.fn() } }))
const service = { id: 's1', revision_id: 'r1', environment_id: 'e1', name: '业务主机', description: '', status: 'active', source_url: 'https://metrics.example.com', labels: { instance: 'host:9100' }, metrics: ['cpu_percent', 'memory_percent'], step_seconds: 15, has_token: true }
beforeEach(() => { vi.clearAllMocks(); vi.mocked(monitoringApi.list).mockResolvedValue([service]) })
describe('environment monitoring', () => {
  it('clears selected revisions on environment change and omits disabled services', async () => {
    vi.mocked(monitoringApi.list).mockResolvedValue([service, { ...service, id: 'disabled', revision_id: 'old', status: 'disabled' }])
    const Host = defineComponent({ components: { LoadMonitoringSelector }, setup: () => ({ env: ref('e1'), selection: ref<MonitoringSelection>({ services: [], before_seconds: 60, after_seconds: 60 }) }), template: '<LoadMonitoringSelector v-model="selection" :environment-revision-id="env" />' })
    const wrapper = mount(Host)
    await flushPromises()
    expect(wrapper.find('[data-testid="monitor-select-disabled"]').exists()).toBe(false)
    await wrapper.get('[data-testid="monitor-select-s1"]').setValue(true)
    await wrapper.get('[data-testid="monitor-required-s1"]').setValue(true)
    expect(wrapper.vm.selection.services).toEqual([{ revision_id: 'r1', required: true }])
    wrapper.vm.env = 'e2'
    await flushPromises()
    expect(wrapper.vm.selection.services).toEqual([])
    expect(wrapper.text()).toContain('已清除上个环境')
  })
  it('creates monitoring inline and refreshes selectable revisions', async () => {
    vi.mocked(monitoringApi.list).mockResolvedValue([])
    vi.mocked(monitoringApi.save).mockResolvedValue(service)
    const wrapper = mount(LoadMonitoringSelector, { props: { environmentRevisionId: 'e1', modelValue: { services: [], before_seconds: 60, after_seconds: 60 } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '新增 / 配置监控服务')!.trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="monitoring-create"]').trigger('click')
    await wrapper.get('[data-testid="monitoring-name"]').setValue('业务主机')
    await wrapper.get('[data-testid="monitoring-url"]').setValue('https://metrics.example.com')
    await wrapper.get('[data-testid="monitoring-instance"]').setValue('host:9100')
    vi.mocked(monitoringApi.list).mockResolvedValue([service])
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.find('[data-testid="monitor-select-s1"]').exists()).toBe(true)
    expect(monitoringApi.save).toHaveBeenCalledWith(undefined, expect.objectContaining({ environment_revision_id: 'e1' }))
  })
  it('ignores stale responses from a previous environment', async () => {
    let resolveOld!: (value: typeof service[]) => void
    vi.mocked(monitoringApi.list).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve })).mockResolvedValueOnce([])
    const wrapper = mount(LoadMonitoringSelector, { props: { environmentRevisionId: 'e1', modelValue: { services: [], before_seconds: 60, after_seconds: 60 } } })
    await wrapper.setProps({ environmentRevisionId: 'e2' }); await flushPromises()
    resolveOld([service]); await flushPromises()
    expect(wrapper.text()).not.toContain('业务主机')
  })
  it('keeps saved tokens blank and omits unchanged credentials from update payload', async () => {
    vi.mocked(monitoringApi.save).mockResolvedValue({ ...service, revision_id: 'r2' })
    const wrapper = mount(EnvironmentMonitoringPanel, { props: { environmentRevisionId: 'e1' } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '编辑')!.trigger('click')
    expect((wrapper.get('[data-testid="monitoring-token"]').element as HTMLInputElement).value).toBe('')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(monitoringApi.save).toHaveBeenCalledWith('s1', expect.objectContaining({ environment_revision_id: 'e1', deployment: 'host', labels: { instance: 'host:9100' } }))
    expect(vi.mocked(monitoringApi.save).mock.calls[0]?.[1]).not.toHaveProperty('token')
  })
  it('presents connection failure without claiming available monitoring', async () => {
    vi.mocked(monitoringApi.check).mockResolvedValue({ state: 'missing', message: '未匹配到新鲜样本' })
    const wrapper = mount(EnvironmentMonitoringPanel, { props: { environmentRevisionId: 'e1' } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '测试连接')!.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('未匹配到新鲜样本')
    expect(wrapper.text()).toContain('当前不采集单个服务')
  })
})
