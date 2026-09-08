import { describe, it, expect } from 'vitest'
import { nextRunPreset } from './loadNextRun'
import type { LoadRun, LoadAiAnalysis } from '../api/contracts'
const run = {id:'r',project_id:'p',state:'finished',scenario_version_id:'old-v',environment_revision_id:'e',configuration:{scenario:{id:'s'},workload:{executor:'constant-arrival-rate',rate:1,time_unit:'1m',max_vus:3},thresholds:{p99_ms:{value:777,operator:'less_than',required:false}},monitoring:{services:[{revision_id:'m',required:true}],before_seconds:30,after_seconds:90}}} as unknown as LoadRun
const ai = {id:'a',run_id:'r',state:'completed',result:{next_run:{load_model:'constant-arrival-rate',target:2,duration_seconds:60}}} as unknown as LoadAiAnalysis
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
  for(const target of [0,1.5,Infinity,NaN]) expect(()=>nextRunPreset(run,{...ai,result:{next_run:{load_model:'constant-vus',target,duration_seconds:60}}})).toThrow()
  expect(()=>nextRunPreset(run,{...ai,result:{next_run:{load_model:'ramping-vus',target:2,duration_seconds:60}}})).toThrow('阶段')
 })
})

it('preserves one-per-minute fallback advice without amplifying it sixtyfold',()=>{
 const preset=nextRunPreset(run,{...ai,result:{next_run:{load_model:'constant-arrival-rate',target:1/60,duration_seconds:60}}})
 expect(preset.timeUnit).toBe('1m');expect(preset.target).toBe(1)
})
