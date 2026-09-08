// @vitest-environment jsdom
import {mount,flushPromises} from '@vue/test-utils'
import {describe,it,expect,vi,beforeEach} from 'vitest'
import LoadRunComparison from './LoadRunComparison.vue'
import type {LoadRun} from '../api/contracts'
import {loadRunComparisonApi,type RunComparison} from '../api/loadRunComparison'
vi.mock('../api/loadRunComparison',()=>({loadRunComparisonApi:{compare:vi.fn()}}))
function run(id:string):LoadRun {return {id,project_id:'p1',scenario_version_id:'s1',environment_revision_id:'e1',load_model:'constant-arrival-rate',queue_priority:'normal',configuration:{scenario:{name:'搜索接口'},test_context:{release:'v1'}},state:'finished',verdict:'passed',stop_reason:'',ai_analysis_state:'pending',summary:{},created_at:'2026-09-08T00:00:00Z',started_at:'2026-09-08T00:00:00Z',finished_at:'2026-09-08T00:01:00Z',updated_at:'2026-09-08T00:01:00Z'}}
const response:RunComparison={schema_version:1,reference_run_id:'a',runs:['a','b'].map(id=>({run_id:id,scenario_name:'搜索',environment_name:'测试',started_at:null,finished_at:'2026-09-08T00:01:00Z',conditions:{release:'v1'},evidence:{complete:true,reasons:[]},metrics:{p95_ms:10}})),comparisons:[{reference_run_id:'a',run_id:'b',eligible:false,reasons:['缓存状态未完整记录或核验'],scope:'仅同条件展示差值',differences:[{key:'cache_state',label:'缓存状态',reference:'warm',current:null,kind:'unknown'}],metrics:[{key:'p95_ms',label:'P95',unit:'ms',reference:10,current:1,delta:null,change_percent:null,direction:'unknown'}]}],coverage:{scope:'当前激活接口资产',selected_run_count:2,observed_step_run_count:2,asset_endpoint_count:10,observed_endpoint_count:1,coverage_ratio:null,mapping_complete:false,unmapped_step_run_count:1,historical_endpoint_step_run_count:0,notice:'不按路径猜测归属',endpoints:[{endpoint_id:'endpoint1',method:'GET',path:'/search',run_ids:['a','b'],requests:20}]},generated_at:'2026-09-08T00:01:00Z',notice:'只读比较，不自动重跑'}
beforeEach(()=>{vi.clearAllMocks();vi.mocked(loadRunComparisonApi.compare).mockResolvedValue(response)})
describe('explicit performance comparisons',()=>{
  it('requires 2 runs, caps selection at 5 and lets the user choose the reference',async()=>{
    const w=mount(LoadRunComparison,{props:{runs:['a','b','c','d','e','f'].map(run),projectId:'p1'}})
    expect((w.get('[data-testid="compare-runs"]').element as HTMLButtonElement).disabled).toBe(true)
    for(const id of ['a','b','c','d','e'])await w.get(`[data-testid="comparison-select-${id}"]`).setValue(true)
    expect((w.get('[data-testid="comparison-select-f"]').element as HTMLInputElement).disabled).toBe(true)
    await w.get('select').setValue('b')
    await w.get('[data-testid="compare-runs"]').trigger('click');await flushPromises()
    expect(loadRunComparisonApi.compare).toHaveBeenCalledWith(['b','a','c','d','e'])
  })
  it('shows insufficient conditions and keeps endpoint and round counts separate',async()=>{
    const w=mount(LoadRunComparison,{props:{runs:[run('a'),run('b')]}})
    for(const id of ['a','b'])await w.get(`[data-testid="comparison-select-${id}"]`).setValue(true)
    await w.get('[data-testid="compare-runs"]').trigger('click');await flushPromises()
    expect(w.text()).toContain('缓存状态未完整记录或核验')
    expect(w.text()).toContain('条件或证据不足')
    const coverage=w.get('[aria-label="实际接口覆盖"]')
    expect(coverage.text()).toContain('执行轮数 2')
    expect(coverage.text()).toContain('已归属接口 1')
    expect(coverage.text()).toContain('覆盖率 未知')
    expect(w.text()).not.toContain('改善')
  })
  it('ignores a late comparison response after the project changes',async()=>{
    let resolve!:(value:RunComparison)=>void
    vi.mocked(loadRunComparisonApi.compare).mockImplementationOnce(()=>new Promise(r=>{resolve=r}))
    const w=mount(LoadRunComparison,{props:{runs:[run('a'),run('b')],projectId:'p1'}})
    for(const id of ['a','b'])await w.get(`[data-testid="comparison-select-${id}"]`).setValue(true)
    await w.get('[data-testid="compare-runs"]').trigger('click')
    await w.setProps({projectId:'p2',runs:[]});resolve(response);await flushPromises()
    expect(w.find('[aria-label="实际接口覆盖"]').exists()).toBe(false)
    expect(w.text()).toContain('已选 0 / 5')
  })
  it('omits failed or unfinished runs and retains selections on an API failure',async()=>{
    vi.mocked(loadRunComparisonApi.compare).mockRejectedValue(new Error('执行无权限'))
    const w=mount(LoadRunComparison,{props:{runs:[run('a'),run('b'),{...run('failed'),state:'failed'},{...run('pending'),finished_at:null}]}})
    expect(w.find('[data-testid="comparison-select-failed"]').exists()).toBe(false)
    expect(w.find('[data-testid="comparison-select-pending"]').exists()).toBe(false)
    for(const id of ['a','b'])await w.get(`[data-testid="comparison-select-${id}"]`).setValue(true)
    await w.get('[data-testid="compare-runs"]').trigger('click');await flushPromises()
    expect(w.get('[role="alert"]').text()).toBe('执行无权限')
    expect(w.text()).toContain('已选 2 / 5')
  })
})
