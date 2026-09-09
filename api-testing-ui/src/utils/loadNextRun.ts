import type { LoadRun, LoadAiAnalysis } from '../api/contracts'
import type { TestContext, StopPolicy } from '../components/LoadTestIntent.vue'
import type { MonitoringSelection } from '../api/monitoring'
export interface NextRunPreset {
  sourceId: string; sourceAnalysisId: string; scenarioId: string; scenarioVersionId: string; environmentId: string
  executor: LoadRun['load_model']; target: number; timeUnit: '1s' | '1m'; duration: number; maxVus: number
  stages?: Array<{duration_seconds:number;target:number}>; startTarget?: number; preAllocatedVus?: number
  originalWorkload?: Record<string, unknown>
  reason?: string; objective?: string; testContext?: TestContext; stopPolicy?: StopPolicy
  thresholds: Record<string, unknown>; monitoring: MonitoringSelection; previous: string
}
export function nextRunPreset(run: LoadRun, analysis: LoadAiAnalysis): NextRunPreset {
  if (analysis.run_id !== run.id || analysis.state !== 'completed' || !['finished','failed','cancelled'].includes(run.state)) throw new Error('诊断与当前已结束执行不匹配，请刷新报告。')
  const policy = analysis.result.next_run_strategy as Record<string, unknown> | undefined
  if (!policy) throw new Error('此历史建议尚未按场景与服务监控策略校验，请先重新诊断。')
  if (policy.can_prefill !== true) throw new Error(String(policy.objective || '请先补齐建议所需条件。'))
  const next = policy.next_run as Record<string, unknown> | undefined
  if (!next) throw new Error('没有可用的下一轮建议。')
  if (!['constant-vus','constant-arrival-rate','ramping-vus','ramping-arrival-rate'].includes(String(next.load_model))) throw new Error('建议负载模型不支持。')
  let target = Number(next.target)
  let timeUnit: '1s' | '1m' = '1s'
  const full = next.workload as Record<string, unknown> | undefined
  const duration = Number(next.duration_seconds)
  if (String(next.load_model).includes('arrival-rate') && Number.isFinite(target) && target > 0 && !Number.isInteger(target)) {
    const perMinute = target * 60
    if (Math.abs(perMinute - Math.round(perMinute)) < 1e-8) { target = Math.round(perMinute); timeUnit = '1m' }
  }
  if (!Number.isSafeInteger(target) || target <= 0 || !Number.isSafeInteger(duration) || duration < 10 || duration > 86400) throw new Error('建议需要正整数 VU，或可表示为整次每秒/每分钟的吞吐；请手动配置有效压力与时长。')
  let stages: NextRunPreset['stages']
  if (full) {
    if (full.executor !== next.load_model) throw new Error('建议曲线与负载模型不一致。')
    if (String(full.executor).startsWith('ramping-')) {
      if (!Array.isArray(full.stages) || !full.stages.length || full.stages.length > 20) throw new Error('建议缺少完整阶段。')
      stages = full.stages.map((s: Record<string, unknown>) => ({duration_seconds:s.duration_seconds as number, target:s.target as number}))
      if (stages.some(s => !Number.isSafeInteger(s.target) || s.target < 0 || !Number.isSafeInteger(s.duration_seconds) || s.duration_seconds < 1) || stages.reduce((n,s)=>n+s.duration_seconds,0) !== duration) throw new Error('建议阶段参数或总时长无效。')
    }
    timeUnit = full.time_unit === '1m' ? '1m' : '1s'
    const startValue = full.start_rate ?? full.start_vus
    if (stages && (typeof startValue !== 'number' || !Number.isSafeInteger(startValue) || startValue < 0)) throw new Error('建议起始压力无效。')
    const preAllocated = full.pre_allocated_vus
    const maximumVus = full.max_vus
    if (String(full.executor).includes('arrival-rate') && (typeof preAllocated !== 'number' || !Number.isSafeInteger(preAllocated) || preAllocated <= 0 || typeof maximumVus !== 'number' || !Number.isSafeInteger(maximumVus) || maximumVus <= 0 || preAllocated > maximumVus)) throw new Error('建议 VU 预算无效。')
    target = stages ? Math.max(...stages.map(s=>s.target), startValue as number) : (full.rate ?? full.vus) as number
    if (!Number.isSafeInteger(target) || target <= 0) throw new Error('建议目标无效。')
  }
  const config = run.configuration
  const previous = config.workload as Record<string, unknown> || {}
  const monitoring = config.monitoring as MonitoringSelection | undefined
  return {
    reason:String(policy.reason || ''), objective:String(policy.objective || ''),
    sourceId:run.id, sourceAnalysisId:analysis.id, scenarioId:String((config.scenario as Record<string,unknown>)?.id || ''), scenarioVersionId:run.scenario_version_id, environmentId:run.environment_revision_id,
    stages, startTarget:full ? Number(full.start_rate ?? full.start_vus ?? 1) : undefined, preAllocatedVus:full ? Number(full.pre_allocated_vus ?? 1) : undefined,
    originalWorkload:JSON.parse(JSON.stringify(previous)),
    executor:next.load_model as NextRunPreset['executor'],target,timeUnit,duration,
    maxVus:Math.max(1, Number(full?.max_vus || previous.max_vus || previous.vus || 1)),
    testContext:config.test_context ? JSON.parse(JSON.stringify(config.test_context)) : undefined,
    stopPolicy:config.stop_policy ? JSON.parse(JSON.stringify(config.stop_policy)) : undefined,
    thresholds:JSON.parse(JSON.stringify(config.thresholds || {})),
    monitoring:{services:(monitoring?.services || []).map(item=>({revision_id:item.revision_id,required:item.required})),before_seconds:monitoring?.before_seconds ?? 60,after_seconds:monitoring?.after_seconds ?? 60},
    previous:previous.executor?.toString().includes('arrival-rate') ? `${previous.rate ?? previous.start_rate} 次/${previous.time_unit === '1m' ? '分钟' : '秒'} · ${previous.duration_seconds ?? '阶梯'} 秒` : `${previous.vus ?? previous.start_vus} VU · ${previous.duration_seconds ?? '阶梯'} 秒`,
  }
}
