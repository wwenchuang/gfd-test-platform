// @vitest-environment jsdom
import {mount,flushPromises} from '@vue/test-utils'
import {it,expect,vi} from 'vitest'
import LoadReportExports from './LoadReportExports.vue'
import {apiClient} from '../api/client'
vi.mock('../api/client',()=>({apiClient:{get:vi.fn()}}))
it('does not request a nonfinal report and exposes authenticated download errors',async()=>{
 const wrapper=mount(LoadReportExports,{props:{runId:'r1',ready:false}})
 expect(wrapper.findAll('button').every(b=>b.attributes('disabled')!==undefined)).toBe(true)
 await wrapper.setProps({ready:true});vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('尚未收齐监控'))
 await wrapper.findAll('button')[0]!.trigger('click');await flushPromises()
 expect(apiClient.get).toHaveBeenCalledWith('/api/api-testing/v1/load-runs/r1/report-export?format=docx')
 expect(wrapper.get('[role=alert]').text()).toContain('尚未收齐监控')
})
