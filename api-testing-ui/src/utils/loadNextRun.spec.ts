import { describe, it, expect } from 'vitest'
import { nextRunPreset } from './loadNextRun'
import type { LoadRun, LoadAiAnalysis } from '../api/contracts'
const run = {id:'r',project_id:'p',state:'finished',scenario_version_id:'old-v',environment_revision_id:'e',configuration:{scenario:{id:'s'},workload:{executor:'constant-arrival-rate',rate:1,time_unit:'1m',max_vus:3},thresholds:{p99_ms:{value:777,operator:'less_than',required:false}},monitoring:{services:[{revision_id:'m',required:true}],before_seconds:30,after_seconds:90}}} as unknown as LoadRun
const ai = {id:'a',run_id:'r',state:'completed',result:{next_run_strategy:{can_prefill:true,next_run:{load_model:'constant-arrival-rate',target:2,duration_seconds:60}}}} as unknown as LoadAiAnalysis
describe('next run configuration',()=>{
 it('keeps frozen IDs and criteria, makes next rate units explicit without mutating source',()=>{
  const result=nextRunPreset(run,ai)
  expect(result.scenarioVersionId).toBe('old-v'); expect(result.environmentId).toBe('e')
  expect(result.thresholds).toEqual(run.configuration.thresholds)
  expect(result.monitoring.services).toEqual([{revision_id:'m',required:true}])
  expect(result.target).toBe(2); expect(result.maxVus).toBe(3)
  expect((run.configuration.workload as Record<string,unknown>).time_unit).toBe('1m')
 })
 it('rejects wrong source, nonfinite targets and unspecified ramp stages',()=>{
  expect(()=>nextRunPreset(run,{...ai,run_id:'other'})).toThrow()
  for(const target of [0,1.5,Infinity,NaN]) expect(()=>nextRunPreset(run,{...ai,result:{next_run_strategy:{can_prefill:true,next_run:{load_model:'constant-vus',target,duration_seconds:60}}}})).toThrow()
  expect(nextRunPreset(run,{...ai,result:{next_run_strategy:{can_prefill:true,next_run:{load_model:'ramping-vus',target:2,duration_seconds:60}}}}).executor).toBe('ramping-vus')
 })
})

it('preserves one-per-minute fallback advice without amplifying it sixtyfold',()=>{
 const preset=nextRunPreset(run,{...ai,result:{next_run_strategy:{can_prefill:true,next_run:{load_model:'constant-arrival-rate',target:1/60,duration_seconds:60}}}})
 expect(preset.timeUnit).toBe('1m');expect(preset.target).toBe(1)
})

it('rejects historical and blocked advice instead of trusting AI numbers',()=>{
 expect(()=>nextRunPreset(run,{...ai,result:{next_run:{target:999}}})).toThrow('历史建议')
 expect(()=>nextRunPreset(run,{...ai,result:{next_run_strategy:{can_prefill:false,objective:'先补监控'}}})).toThrow('先补监控')
})

it('carries exact ramp start, minute units, all stages and VU budget to the wizard',()=>{
 const workload={executor:'ramping-arrival-rate',start_rate:30,time_unit:'1m',pre_allocated_vus:2,max_vus:4,stages:[{duration_seconds:30,target:60},{duration_seconds:60,target:60},{duration_seconds:30,target:0}]}
 const preset=nextRunPreset(run,{...ai,result:{next_run_strategy:{can_prefill:true,next_run:{load_model:'ramping-arrival-rate',target:1,duration_seconds:120,workload}}}})
 expect(preset.stages).toEqual(workload.stages)
 expect(preset.startTarget).toBe(30)
 expect(preset.timeUnit).toBe('1m')
 expect(preset.preAllocatedVus).toBe(2)
 expect(preset.maxVus).toBe(4)
 expect(preset.sourceAnalysisId).toBe('a')
})

it('rejects malformed numeric workload fields and workloads beyond the frozen VU budget',()=>{
 const base={executor:'ramping-arrival-rate',start_rate:0,time_unit:'1s',pre_allocated_vus:2,max_vus:4,stages:[{duration_seconds:60,target:2},{duration_seconds:60,target:0}]}
 for(const workload of [
  {...base,start_rate:'0'}, {...base,start_rate:-1}, {...base,pre_allocated_vus:5,max_vus:4},
  {...base,stages:[{duration_seconds:60,target:NaN},{duration_seconds:60,target:0}]},
 ]) expect(()=>nextRunPreset(run,{...ai,result:{next_run_strategy:{can_prefill:true,next_run:{load_model:'ramping-arrival-rate',target:2,duration_seconds:120,workload}}}})).toThrow()
})
