// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadAiAnalysis from './LoadAiAnalysis.vue'

describe('LoadAiAnalysis', () => {
  it('shows platform recommendation facts and evidence per action while distinguishing historical results', async () => {
    const analysis = { id:'a',run_id:'r',model:'m',prompt_version:'v9',evidence_hash:'h',state:'completed',error:'',created_at:'',result:{recommendation_contract_version:1,service_facts:[{evidence_id:'service.fact.demo.work_slots',component:'bounded_work_slots',state:'present',source:{kind:'source_review',reference:'worker.py @ abc123',recorded_by_operator:true}}],recommendations:[{priority:'high',action:'核对工作槽位',verification:'保持其他条件复验',domain:'cpu',service_key:'demo',intent:'verify',action_code:'verify_work_slots',fact_ids:['service.fact.demo.work_slots'],evidence_ids:['latency.summary']}]}}
    const wrapper=mount(LoadAiAnalysis,{props:{analysis}})
    const row=wrapper.get('.load-recommendations li')
    expect(row.text()).toContain('服务：demo')
    expect(row.text()).toContain('service.fact.demo.work_slots')
    expect(row.text()).toContain('工作槽位上限 · 存在 · 人工记录来源：代码核查记录 · worker.py @ abc123')
    expect(row.text()).toContain('响应时间统计')
    expect(row.text()).toContain('latency.summary')
    expect(row.text()).not.toContain('未逐条验证')
    await wrapper.setProps({analysis:{...analysis,result:{recommendations:analysis.result.recommendations}}})
    expect(wrapper.get('.load-recommendations li').text()).toContain('历史建议：未逐条验证服务事实与证据')
    expect(wrapper.text()).toContain('核对工作槽位')
  })
  it('shows evidence citations, low confidence and explains reanalysis does not rerun load', async () => {
    const analysis = { id: 'a1', run_id: 'r1', model: 'qwen-plus', prompt_version: 'v1', evidence_hash: 'abcdef1234567890', state: 'completed', error: '', created_at: '', result: { next_run_strategy: {can_prefill:true,reason:'本轮监控证据完整',objective:'验证增长后的延迟'}, conclusion: '瓶颈在目标服务', bottleneck_category: 'target_service', evidence: ['latency.summary', 'agent.a1'], confidence: { level: 'low', reason: '窗口较少' }, recommendations: [{ priority: 'high', action: '检查慢查询', verification: '复跑相同负载' }], next_run: { load_model: 'constant-arrival-rate', target: 20, duration_seconds: 120, agent_suggestion: '继续使用专用节点' } } }
    const wrapper = mount(LoadAiAnalysis, { props: { analysis } })
    expect(wrapper.text()).toContain('不会重新执行压测')
    expect(wrapper.text()).toContain('低置信度')
    expect(wrapper.text()).toContain('latency.summary')
    expect(wrapper.text()).toContain('疑似目标服务瓶颈')
    expect(wrapper.text()).toContain('高优先级')
    expect(wrapper.text()).toContain('检查慢查询')
    expect(wrapper.text()).toContain('固定吞吐')
    expect(wrapper.text()).toContain('目标 20')
    expect(wrapper.text()).toContain('继续使用专用节点')
    await wrapper.get('[data-testid="load-reanalyze"]').trigger('click')
    expect(wrapper.emitted('reanalyze')).toHaveLength(1)
  })

  it('keeps AI failure separate from the deterministic report', () => {
    const analysis = { id: 'a1', run_id: 'r1', model: 'qwen-plus', prompt_version: 'v1', evidence_hash: 'e', state: 'failed', error: 'AI诊断超时，请稍后重试', created_at: '', result: {} }
    const wrapper = mount(LoadAiAnalysis, { props: { analysis } })
    expect(wrapper.text()).toContain('AI诊断超时')
  })
})

it('explains fallback advice and translates evidence without losing technical traceability', () => {
  const wrapper = mount(LoadAiAnalysis, { props: { analysis: { id:'a',run_id:'r',model:'平台自动路由',prompt_version:'v4',evidence_hash:'hash',state:'completed',error:'',created_at:'',result:{confidence:{level:'low',reason:'模型引用无效，已回退为平台安全建议：AI诊断结论不能复述数值'},evidence:['load.goal']} } } })
  expect(wrapper.text()).toContain('当前展示平台规则建议，不是 AI 诊断结论')
  expect(wrapper.text()).toContain('目标压力达成情况')
  expect(wrapper.get('details').text()).toContain('模型引用无效')
})

it('offers a separate configuration action only for authorized viewers', async () => {
  const analysis = {id:'a',run_id:'r',model:'m',prompt_version:'v5',evidence_hash:'h',state:'completed',result:{next_run_strategy:{can_prefill:true},next_run:{load_model:'constant-arrival-rate',target:2,duration_seconds:60}},error:'',created_at:''}
  const wrapper=mount(LoadAiAnalysis,{props:{analysis}})
  expect(wrapper.find('[data-testid="load-next-run"]').exists()).toBe(false)
  await wrapper.setProps({canCreateNext:true})
  await wrapper.get('[data-testid="load-next-run"]').trigger('click')
  expect(wrapper.emitted('createNext')).toHaveLength(1)
  expect(wrapper.emitted('reanalyze')).toBeUndefined()
  expect(wrapper.text()).toContain('不会立即发压')
})

it('shows policy blockers and hides the configuration action',()=>{
 const wrapper=mount(LoadAiAnalysis,{props:{canCreateNext:true,analysis:{id:'a',run_id:'r',state:'completed',model:'m',prompt_version:'v6',evidence_hash:'h',created_at:'',error:'',result:{next_run_strategy:{can_prefill:false,reason:'缺少服务监控',objective:'先补监控同压力复验',limitations:['整机不等于服务']},next_run:{target:999}}}}})
 expect(wrapper.text()).toContain('先补监控同压力复验');expect(wrapper.text()).toContain('整机不等于服务');expect(wrapper.find('[data-testid=load-next-run]').exists()).toBe(false);expect(wrapper.text()).not.toContain('目标 999')
})

it.each([
 ['completed','ai_validated','AI 提出的下一轮参数已通过校验'],
 ['completed','ai_adjusted','AI 建议已由平台调整'],
 ['rule_fallback','rule_fallback','平台规则备用建议'],
])('shows recommendation source independently from action state for %s/%s',(analysisStatus,validationStatus,label)=>{
 const wrapper=mount(LoadAiAnalysis,{props:{canCreateNext:true,analysis:{id:'a',run_id:'r',state:'completed',model:'m',prompt_version:'v7',evidence_hash:'h',created_at:'',error:'',result:{analysis_status:analysisStatus,next_run_strategy:{source:validationStatus,validation_status:validationStatus,can_prefill:false,continuation_status:'需补证据',reason:'缺少服务监控',objective:'补齐监控后按原曲线复验',adjustment_reasons:['最大 VU 收敛到原预算'],original_workload:{executor:'constant-vus',vus:2,duration_seconds:60}},next_run:{load_model:'constant-vus',target:2,duration_seconds:60,workload:{executor:'constant-vus',vus:2,duration_seconds:60}}}}}})
 expect(wrapper.text()).toContain(label)
 expect(wrapper.text()).toContain('行动状态：需补证据')
 expect(wrapper.text()).toContain('最大 VU 收敛到原预算')
 expect(wrapper.text()).toContain('前往“监控配置”接入目标服务')
 expect(wrapper.find('[data-testid=load-next-run]').exists()).toBe(false)
})

it('uses explicit rule fallback status even when confidence has no legacy fallback wording',()=>{
 const wrapper=mount(LoadAiAnalysis,{props:{analysis:{id:'a',run_id:'r',state:'completed',model:'m',prompt_version:'v7',evidence_hash:'h',created_at:'',error:'',result:{analysis_status:'rule_fallback',confidence:{level:'low',reason:'AI 服务超时'},conclusion:'按原曲线复验'}}}})
 expect(wrapper.text()).toContain('平台规则建议')
 expect(wrapper.text()).toContain('当前展示平台规则建议，不是 AI 诊断结论')
 expect(wrapper.text()).toContain('平台建议结论')
})

it('does not mislabel explicit completed analysis from legacy words in confidence reason',()=>{
 const wrapper=mount(LoadAiAnalysis,{props:{analysis:{id:'a',run_id:'r',state:'completed',model:'m',prompt_version:'v7',evidence_hash:'h',created_at:'',error:'',result:{analysis_status:'completed',confidence:{level:'low',reason:'回退条件已排除'},conclusion:'证据仍需复验'}}}})
 expect(wrapper.text()).toContain('诊断完成')
 expect(wrapper.text()).toContain('低置信度：回退条件已排除')
 expect(wrapper.text()).not.toContain('当前展示平台规则建议')
 expect(wrapper.text()).not.toContain('平台建议结论')
})

it('separates validated actions from unverified AI root-cause prose',()=>{
 const wrapper=mount(LoadAiAnalysis,{props:{analysis:{id:'a',run_id:'r',state:'completed',model:'m',prompt_version:'v9',evidence_hash:'h',created_at:'',error:'',result:{analysis_status:'completed',recommendation_contract_version:1,confidence:{level:'high',reason:'模型判断'},conclusion:'数据库锁竞争已确认',recommendations:[]}}}})
 expect(wrapper.findAll('h3').map(x=>x.text())).toContain('AI 解释（根因待验证）')
 expect(wrapper.text()).toContain('已校验处理动作和引用范围；AI 解释仍需结合原始证据与对照实验复核。')
 expect(wrapper.findAll('h3').map(x=>x.text())).not.toContain('诊断结论')
})
