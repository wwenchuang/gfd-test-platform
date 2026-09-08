// @vitest-environment jsdom
import {mount} from '@vue/test-utils'
import {expect,it} from 'vitest'
import LoadConditionValue from './LoadConditionValue.vue'
it('summarizes load without confusing per minute and per second while retaining raw evidence',()=>{
 const value={executor:'constant-arrival-rate',rate:60,time_unit:'1m',duration_seconds:20,max_vus:2}
 const w=mount(LoadConditionValue,{props:{value,kind:'workload'}})
 expect(w.get('[data-testid="condition-summary"]').text()).toContain('60 次/分钟')
 expect(w.get('[data-testid="condition-summary"]').text()).toContain('20 秒')
 expect(w.get('details').attributes('open')).toBeUndefined()
 expect(w.get('pre').text()).toContain('"time_unit": "1m"')
})
it('shows node runtime versions and does not invent unreported limits',()=>{
 const w=mount(LoadConditionValue,{props:{kind:'agents',value:[{id:'a',agent_version:'0.1.2',allocation:{vus:1}}]}})
 expect(w.get('[data-testid="condition-summary"]').text()).toContain('Agent 0.1.2')
 expect(w.get('[data-testid="condition-summary"]').text()).toContain('1 VU')
 expect(w.text()).not.toContain('0 核')
})
