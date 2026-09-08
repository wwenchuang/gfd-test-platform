// @vitest-environment jsdom
import {mount} from '@vue/test-utils'
import {it,expect} from 'vitest'
import LoadGeneratorResources from './LoadGeneratorResources.vue'
it('does not turn missing runtime into healthy zero or hide generator scope',()=>{
 const missing=mount(LoadGeneratorResources,{props:{agents:[{name:'节点',shard_id:'s'}]}})
 expect(missing.text()).toContain('未采集');expect(missing.text()).not.toContain('0 核')
 const rendered=mount(LoadGeneratorResources,{props:{agents:[{name:'节点',shard_id:'s',load_generator_resources:{interval_seconds:5,samples:[{sampled_at:'2026-09-08T00:00:00Z',cpu_scope:'k6_process',cpu_used_cores:.5,cpu_percent:5,cpu_limit_source:'visible_cpus',memory_scope:'k6_process',memory_used_bytes:1048576,memory_limit_bytes:null}]}}]}})
 expect(rendered.text()).toContain('仅 k6 进程');expect(rendered.text()).toContain('不是独享配额');expect(rendered.text()).toContain('0.5 核')
 expect(rendered.findAll('svg')).toHaveLength(2)
})
