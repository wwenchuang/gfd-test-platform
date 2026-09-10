export function loadWorkloadSummary(workload: Record<string, unknown>): string {
  const executor = String(workload.executor || '')
  const arrival = executor.includes('arrival-rate')
  const unit = arrival ? `次/${workload.time_unit === '1m' ? '分钟' : '秒'}` : 'VU'
  if (executor.startsWith('ramping-')) {
    const stages = Array.isArray(workload.stages) ? workload.stages as Array<Record<string, unknown>> : []
    if (!stages.length) return '阶段配置缺失'
    const curve = [arrival ? workload.start_rate : workload.start_vus, ...stages.map(stage => stage.target)]
    const seconds = stages.map(stage => Number(stage.duration_seconds))
    const duration = seconds.every(value => Number.isFinite(value) && value > 0)
      ? seconds.reduce((total, value) => total + value, 0) : '未知'
    return `${curve.map(value => value ?? '未知').join(' → ')} ${unit} · ${duration} 秒`
  }
  return `${(arrival ? workload.rate : workload.vus) ?? '未知'} ${unit} · ${workload.duration_seconds ?? '未知'} 秒`
}
