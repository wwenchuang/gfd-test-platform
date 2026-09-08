// @vitest-environment jsdom
import {mount,flushPromises} from '@vue/test-utils'
import {describe,it,expect,vi,beforeEach} from 'vitest'
import type {LoadRun} from '../api/contracts'
import LoadPerformanceBaselines from './LoadPerformanceBaselines.vue'
import {performanceBaselinesApi,type PerformanceBaseline,type BaselineRegression} from '../api/loadPerformanceBaselines'
import {setApiTestingAccessProfile} from '../utils/authRedirect'
vi.mock('../api/loadPerformanceBaselines',()=>({performanceBaselinesApi:{list:vi.fn(),adopt:vi.fn(),retire:vi.fn(),compare:vi.fn()}}))
const run=(id:string):LoadRun=>({id,project_id:'p',scenario_version_id:'sv',environment_revision_id:'e1',load_model:'constant-arrival-rate',queue_priority:'normal',state:'finished',verdict:'passed',stop_reason:'',ai_analysis_state:'idle',summary:{},created_at:'2026-09-08T00:00:00Z',updated_at:'2026-09-08T00:01:00Z',started_at:'2026-09-08T00:00:00Z',finished_at:'2026-09-08T00:01:00Z',configuration:{scenario:{name:'查询'}}})
const baseline:PerformanceBaseline={id:'base',project_id:'p',scenario_id:'s',environment_id:'env',environment_revision_id:'e1',source_run_id:'a',name:'人工参考',adoption_reason:'核对后采纳',status:'active',created_at:'2026-09-08T00:00:00Z',created_by:'owner',evidence_hash:'hash',regression_policy:{p95_increase_percent:10},scenario_name:'查询',environment_name:'测试',release:'v1',metrics:{p95_ms:50,requests_per_second:10}}
const regression:BaselineRegression={baseline,current_run_id:'b',state:'inconclusive',message:'条件不完整，不能判断',notice:'不会自动重跑',checks:[{key:'p95_increase_percent',label:'P95 增幅',unit:'%',limit:10,actual:null,triggered:null}],comparison:{reference_run_id:'a',run_id:'b',eligible:false,reasons:['缓存状态不同'],scope:'同条件核对',differences:[],metrics:[]}}
beforeEach(()=>{vi.clearAllMocks();setApiTestingAccessProfile(null);vi.mocked(performanceBaselinesApi.list).mockResolvedValue({baselines:[baseline],truncated:false});vi.mocked(performanceBaselinesApi.adopt).mockResolvedValue(baseline);vi.mocked(performanceBaselinesApi.compare).mockResolvedValue(regression)})
describe('manual performance references',()=>{
 it('requires a reason and sends only the selected run and explicit warning policy',async()=>{
  const w=mount(LoadPerformanceBaselines,{props:{runs:[run('a'),run('b')],projectId:'p'}});await flushPromises()
  await w.get('[data-testid="baseline-open-adopt"]').trigger('click')
  await w.get('[data-testid="baseline-adopt-run"]').setValue('a')
  expect((w.get('[data-testid="baseline-adopt"]').element as HTMLButtonElement).disabled).toBe(true)
  await w.get('[data-testid="baseline-reason"]').setValue('已核对证据')
  await w.get('form').trigger('submit');await flushPromises()
  expect(performanceBaselinesApi.adopt).toHaveBeenCalledWith({run_id:'a',name:'性能参考基线',adoption_reason:'已核对证据',regression_policy:{p95_increase_percent:10,rps_decrease_percent:10,http_error_increase_points:1}})
  expect(w.text()).toContain('条件与预警阈值已冻结')
 })
 it('keeps adoption and retirement unavailable to view-only actors',async()=>{
  setApiTestingAccessProfile({status:'active',permissions:['api.view','api.loadtest.view']})
  const w=mount(LoadPerformanceBaselines,{props:{runs:[run('a'),run('b')],projectId:'p'}});await flushPromises()
  expect(w.find('[data-testid="baseline-open-adopt"]').exists()).toBe(false)
  expect(w.find('[data-testid="baseline-retire"]').exists()).toBe(false)
  await w.get('[data-testid="baseline-candidate"]').setValue('b');await w.get('[data-testid="baseline-compare"]').trigger('click');await flushPromises()
  expect(w.text()).toContain('条件或证据不足');expect(w.text()).toContain('缓存状态不同')
 })
 it('ignores late comparisons after project changes',async()=>{
  let resolve!:(value:BaselineRegression)=>void
  vi.mocked(performanceBaselinesApi.compare).mockImplementationOnce(()=>new Promise(r=>{resolve=r}))
  const w=mount(LoadPerformanceBaselines,{props:{runs:[run('a'),run('b')],projectId:'p'}});await flushPromises()
  await w.get('[data-testid="baseline-candidate"]').setValue('b');await w.get('[data-testid="baseline-compare"]').trigger('click')
  vi.mocked(performanceBaselinesApi.list).mockResolvedValue({baselines:[],truncated:false})
  await w.setProps({projectId:'other',runs:[]});resolve(regression);await flushPromises()
  expect(w.find('[aria-label="基线退化核对结果"]').exists()).toBe(false)
 })
 it('retirement preserves a visible historical record and does not trigger pressure',async()=>{
  vi.mocked(performanceBaselinesApi.retire).mockResolvedValue({...baseline,status:'retired'})
  const w=mount(LoadPerformanceBaselines,{props:{runs:[run('a'),run('b')],projectId:'p'}});await flushPromises()
  vi.mocked(performanceBaselinesApi.list).mockResolvedValue({baselines:[{...baseline,status:'retired'}],truncated:false})
  await w.get('[data-testid="baseline-retire"]').trigger('click');await flushPromises()
  expect(w.text()).toContain('历史证据仍保留');expect(w.find('[data-testid="baseline-retire"]').exists()).toBe(false)
  expect(performanceBaselinesApi.compare).not.toHaveBeenCalled()
 })
})
