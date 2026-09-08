import type { LoadRun, LoadAiAnalysis } from '../api/contracts'
import type { TestContext, StopPolicy } from '../components/LoadTestIntent.vue'
import type { MonitoringSelection } from '../api/monitoring'
export interface NextRunPreset {
  sourceId: string; scenarioId: string; scenarioVersionId: string; environmentId: string
  executor: LoadRun['load_model']; target: number; timeUnit: '1s' | '1m'; duration: number; maxVus: number
  testContext?: TestContext; stopPolicy?: StopPolicy
  thresholds: Record<string, unknown>; monitoring: MonitoringSelection; previous: string
}
export function nextRunPreset(run: LoadRun, analysis: LoadAiAnalysis): NextRunPreset {
  if (analysis.run_id !== run.id || analysis.state !== 'completed' || !['finished','failed','cancelled'].includes(run.state)) throw new Error('诊断与当前已结束执行不匹配，请刷新报告。')
  const next = analysis.result.next_run as Record<string, unknown> | undefined
  if (!next) throw new Error('没有可用的下一轮建议。')
  if (!['constant-vus','constant-arrival-rate','ramping-vus','ramping-arrival-rate'].includes(String(next.load_model))) throw new Error('建议负载模型不支持。')
  let target = Number(next.target)
  let timeUnit: '1s' | '1m' = '1s'
  const duration = Number(next.duration_seconds)
  if (String(next.load_model).includes('arrival-rate') && Number.isFinite(target) && target > 0 && !Number.isInteger(target)) {
    const perMinute = target * 60
    if (Math.abs(perMinute - Math.round(perMinute)) < 1e-8) { target = Math.round(perMinute); timeUnit = '1m' }
  }
  if (!Number.isSafeInteger(target) || target <= 0 || !Number.isSafeInteger(duration) || duration < 10 || duration > 86400) throw new Error('建议需要正整数 VU，或可表示为整次每秒/每分钟的吞吐；请手动配置有效压力与时长。')
  const config = run.configuration
  const previous = config.workload as Record<string, unknown> || {}
  const monitoring = config.monitoring as MonitoringSelection | undefined
  return {
    sourceId:run.id, scenarioId:String((config.scenario as Record<string,unknown>)?.id || ''), scenarioVersionId:run.scenario_version_id, environmentId:run.environment_revision_id,
    executor:next.load_model as NextRunPreset['executor'],target,timeUnit,duration,
    maxVus:Math.max(1, Number(previous.max_vus || previous.vus || 1)),
    testContext:config.test_context ? JSON.parse(JSON.stringify(config.test_context)) : undefined,
    stopPolicy:config.stop_policy ? JSON.parse(JSON.stringify(config.stop_policy)) : undefined,
    thresholds:JSON.parse(JSON.stringify(config.thresholds || {})),
    monitoring:{services:(monitoring?.services || []).map(item=>({revision_id:item.revision_id,required:item.required})),before_seconds:monitoring?.before_seconds ?? 60,after_seconds:monitoring?.after_seconds ?? 60},
    previous:previous.executor?.toString().includes('arrival-rate') ? `${previous.rate ?? previous.start_rate} 次/${previous.time_unit === '1m' ? '分钟' : '秒'} · ${previous.duration_seconds ?? '阶梯'} 秒` : `${previous.vus ?? previous.start_vus} VU · ${previous.duration_seconds ?? '阶梯'} 秒`,
  }
}
