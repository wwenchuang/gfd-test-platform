export type FactState = 'present' | 'absent' | 'unknown'
export type FactSource = {kind: string; reference: string; recorded_by_operator?: boolean}
export type ServiceFact = {key?: string; kind?: string; state: FactState; source?: FactSource; [key: string]: unknown}
export type ServiceFacts = {version: number; components: ServiceFact[]; behaviors: ServiceFact[]}
export const factDefinitions = [
  {key:'database', label:'数据库', group:'components'},
  {key:'downstream', label:'下游依赖', group:'components'},
  {key:'gc_runtime', label:'垃圾回收运行时', group:'components'},
  {key:'bounded_work_slots', label:'工作槽位上限', group:'behaviors'},
  {key:'bounded_connection_slots', label:'连接槽位上限', group:'behaviors'},
  {key:'cpu_wall_time_work', label:'固定墙钟时间 CPU 工作', group:'behaviors'},
  {key:'shared_ttl_allocation', label:'共享 TTL 内存分配', group:'behaviors'},
] as const
export const sourceLabels: Record<string,string> = {operator_declaration:'操作人声明',source_review:'代码核查记录',runtime_observation:'运行观察记录'}
export const stateLabels: Record<string,string> = {present:'存在',absent:'不存在',unknown:'未知'}
export function serviceFacts(metadata: Record<string,unknown>): ServiceFacts | undefined {
  return metadata.load_service_facts as ServiceFacts | undefined
}
export function serviceFactsIssue(metadata: Record<string,unknown>): string {
  const facts = serviceFacts(metadata)
  if (!facts) return ''
  if (facts.version !== 1 || !Array.isArray(facts.components) || !Array.isArray(facts.behaviors)) return '服务事实格式无法编辑，请核对记录版本'
  for (const row of [...facts.components,...facts.behaviors]) {
    const label = factDefinitions.find(item=>item.key===(row.key || row.kind))?.label || '服务事实'
    if ((row.state !== 'unknown' || row.source) && (!sourceLabels[row.source?.kind || ''] || !row.source?.reference.trim() || row.source.reference.length > 500)) return `${label}：请填写来源与引用（最多 500 字）`
    if (row.state !== 'present' || !row.kind) continue
    const ranges: Record<string,number> = row.kind.includes('slots') ? {limit:1000000} : row.kind === 'cpu_wall_time_work' ? {duration_ms:3600000} : {bytes:1099511627776,ttl_seconds:86400}
    if (Object.entries(ranges).some(([key,max])=>!Number.isSafeInteger(row[key]) || Number(row[key]) < 1 || Number(row[key]) > max)) return `${label}：请填写有效的正整数参数`
    if (row.kind === 'shared_ttl_allocation' && typeof row.refresh_on_request !== 'boolean') return `${label}：请选择请求时是否刷新 TTL`
    if (!row.kind.includes('slots') && (!Array.isArray(row.endpoints) || !row.endpoints.length || row.endpoints.length > 20 || row.endpoints.some(path=>typeof path !== 'string' || path.length > 500 || !path.startsWith('/') || path.startsWith('//') || /[?#\r\n]/.test(path)))) return `${label}：请填写 1–20 条接口路径，每行一条，以 / 开头`
  }
  return ''
}
