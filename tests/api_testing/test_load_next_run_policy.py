from copy import deepcopy
import pytest
from task_server.api_testing.services.load_next_run_policy import build_next_run_policy, resource_observations, number, validate_next_run_advice
from task_server.api_testing.services.load_execution_policy import parse_test_context


def report():
    metrics = [{'key': key, 'unit':'%', 'coverage':{'valid_ratio':1}, 'series':[{'labels':{'instance':'a'},'points':[{'value':v} for v in [20,30,25]]}]} for key in ['cpu_percent','memory_percent']]
    return {'scenario_safety':{'readonly':True},'verdict':'passed','load_goal':{'reached':True},'evidence':{'complete':True,'workload_snapshot':{'executor':'constant-arrival-rate','rate':10,'time_unit':'1s','duration_seconds':120,'pre_allocated_vus':2,'max_vus':4}},'transport':{'http_error_rate':0,'requests':1200},'business':{'failure_rate':0,'assertions':1200},'workflow':{'failure_rate':0,'iterations':1200},'test_context':{'purpose':'stress','recommendation_goal':20,'max_step_percent':20,'observation_seconds':120},'monitoring':{'services':[{'revision_id':'m','name':'业务主机','scope':'host','state':'completed','metrics':metrics}]}}


def test_bounded_growth_requires_goal_and_real_resources():
    r=report(); p=build_next_run_policy(r)
    assert p['action']=='increase_bounded' and p['next_run']['target']==12
    r['test_context']['recommendation_goal']=11
    assert build_next_run_policy(r)['next_run']['target']==11
    r['monitoring']['services']=[]
    assert build_next_run_policy(r)['action']=='connect_monitoring'
    assert not build_next_run_policy(r)['can_prefill']

@pytest.mark.parametrize('change,action',[
    ({'scenario_safety':{}},'review_side_effects'),
    ({'verdict':'failed'},'repair_failures'),
    ({'load_goal':{'reached':False}},'repair_evidence'),
    ({'test_context':{'purpose':'smoke'}},'smoke_complete'),
    ({'test_context':{'purpose':'stress'}},'set_goal'),
])
def test_non_growth_branches(change,action):
    r=report();r.update(change);assert build_next_run_policy(r)['action']==action

def test_no_false_rejection_within_user_sample_tolerance():
    r=report();r['evidence']['sample_integrity']={'consistent':False,'acceptable':True}
    assert build_next_run_policy(r)['action']=='increase_bounded'
    r['evidence']['sample_integrity']['acceptable']=False
    assert build_next_run_policy(r)['action']=='repair_evidence'

def test_each_instance_and_missing_quota_are_checked():
    r=report();metric=r['monitoring']['services'][0]['metrics'][0]
    metric['series'].append({'labels':{'instance':'hot'},'points':[{'value':90}]})
    assert build_next_run_policy(r)['action']=='inspect_resources'
    r=report();r['monitoring']['services'][0]['metrics'][0]['key']='cpu_cores'
    assert build_next_run_policy(r)['action']=='connect_monitoring'

def test_separate_pod_state_does_not_need_its_own_cpu_but_restart_blocks():
    r=report();r['monitoring']['services'].append({'revision_id':'p','scope':'pod_state','state':'completed','metrics':[{'key':'pod_restarts_increase_2m','series':[{'points':[{'value':0}]}]}]})
    assert build_next_run_policy(r)['action']=='increase_bounded'
    r['monitoring']['services'][1]['metrics'][0]['series'][0]['points'][0]['value']=1
    assert build_next_run_policy(r)['action']=='inspect_resources'

def test_observe_first_and_preserve_rate_units_and_no_mutation():
    r=report();r['evidence']['workload_snapshot'].update(rate=30,time_unit='1m',duration_seconds=20)
    before=deepcopy(r);p=build_next_run_policy(r)
    assert p['action']=='observe_same_load' and p['next_run']['target']==.5 and p['next_run']['duration_seconds']==120
    assert r==before

def test_missing_last_point_not_fabricated_and_numbers_finite():
    r=report();r['monitoring']['services'][0]['metrics'][0]['series'][0]['points'].append({'value':None})
    assert resource_observations(r)[0]['last'] is None
    assert number(10**1000) is None

@pytest.mark.parametrize('key,value',[('max_step_percent',51),('recommendation_goal',float('inf')),('observation_seconds',True),('observation_seconds',59)])
def test_invalid_user_policy_rejected(key,value):
    with pytest.raises(ValueError):parse_test_context({'purpose':'load',key:value})

def test_pressure_node_saturation_is_not_business_bottleneck():
    r=report();r['agents']=[{'load_generator_resources':{'samples':[{'cpu_percent':90}]}}]
    assert build_next_run_policy(r)['action']=='inspect_generator'

def test_completed_soak_does_not_repeat_forever():
    r=report();r['test_context']['purpose']='soak'
    assert build_next_run_policy(r)['action']=='observation_complete'

def test_one_missing_instance_cannot_be_hidden_by_healthy_instance():
    r=report();r['monitoring']['services'][0]['metrics'][0]['series'].append({'points':[{'value':None}]})
    assert build_next_run_policy(r)['action']=='connect_monitoring'

def ramp_report():
    r = report()
    r['evidence']['workload_snapshot'] = {'executor':'ramping-arrival-rate', 'start_rate':30, 'time_unit':'1m', 'pre_allocated_vus':2, 'max_vus':4, 'stages':[{'duration_seconds':30,'target':60},{'duration_seconds':60,'target':60},{'duration_seconds':30,'target':0}]}
    return r

@pytest.mark.parametrize('change,action', [({'load_goal':{'reached':False}},'repair_evidence'), ({'verdict':'failed','business':{'failure_rate':.1}},'repair_failures')])
def test_ramp_failure_and_attainment_keep_exact_curve(change, action):
    r = ramp_report(); r.update(change); before = deepcopy(r)
    p = build_next_run_policy(r)
    assert p['action'] == action and p['can_prefill']
    assert p['next_run']['workload'] == r['evidence']['workload_snapshot']
    assert p['observations'] and r == before

def test_ramp_resources_are_inspected_before_recommendation():
    r = ramp_report(); r['monitoring']['services'][0]['metrics'][0]['series'][0]['points'][0]['value'] = 95
    assert build_next_run_policy(r)['action'] == 'inspect_resources'

def test_ai_curve_is_preserved_when_valid_and_adjusted_when_unsafe():
    from task_server.api_testing.services.load_next_run_policy import validate_next_run_advice
    r = ramp_report(); policy = build_next_run_policy(r)
    advice = deepcopy(policy['next_run'])
    advice['workload']['stages'][0]['target'] = 66
    advice['workload']['stages'][1]['target'] = 66
    p = validate_next_run_advice(policy, advice)
    assert p['validation_status'] == 'ai_validated'
    assert p['next_run']['workload']['stages'][1]['target'] == 66
    assert p['original_workload'] == r['evidence']['workload_snapshot']
    r['verdict'] = 'failed'
    p = validate_next_run_advice(build_next_run_policy(r), advice)
    assert p['validation_status'] == 'ai_adjusted'
    assert p['next_run']['workload'] == r['evidence']['workload_snapshot']
    assert p['adjustment_reasons']

def test_ai_cannot_hide_early_spike_or_increase_vu_budget():
    p = build_next_run_policy(ramp_report())
    for field in ['start_rate', 'max_vus']:
        advice = deepcopy(p['next_run']); advice['workload'][field] = 99999
        assert validate_next_run_advice(p, advice)['validation_status'] == 'ai_adjusted'

@pytest.mark.parametrize('stages', [None, [{'duration_seconds':'bad','target':60}], [{'duration_seconds':30,'target':None}]])
def test_malformed_original_ramp_is_reported_instead_of_crashing(stages):
    r = ramp_report(); r['evidence']['workload_snapshot']['stages'] = stages
    p = build_next_run_policy(r)
    assert p['action'] == 'review_profile'
    assert not p['can_prefill']

@pytest.mark.parametrize('section,field', [('transport','http_error_rate'), ('business','failure_rate'), ('workflow','failure_rate')])
def test_unknown_error_rate_is_missing_evidence_not_zero(section, field):
    r = report(); r[section] = {field: None}
    p = build_next_run_policy(r)
    assert p['action'] == 'repair_evidence'
    assert '未知' in p['reason'] or '缺' in p['reason']

def test_cpu_and_memory_must_belong_to_same_instance_labels():
    r = report(); metrics = r['monitoring']['services'][0]['metrics']
    metrics[0]['series'][0]['labels'] = {'instance':'a'}
    metrics[1]['series'][0]['labels'] = {'instance':'b'}
    p = build_next_run_policy(r)
    assert p['action'] == 'connect_monitoring'

@pytest.mark.parametrize('mutate', [
    lambda w: w['stages'][0].update(target=20),  # original rise became a fall
    lambda w: w['stages'][1].update(target=50),  # original steady became a fall
    lambda w: w['stages'][2].update(target=60),  # recovery fall became steady
    lambda w: w.update(time_unit='1s'),
    lambda w: w.update(pre_allocated_vus=1),
    lambda w: w['stages'][2].update(duration_seconds=20),
])
def test_ai_ramp_preserves_direction_units_vu_budget_and_recovery(mutate):
    p = build_next_run_policy(ramp_report()); advice = deepcopy(p['next_run'])
    mutate(advice['workload'])
    result = validate_next_run_advice(p, advice)
    assert result['validation_status'] == 'ai_adjusted'
    assert result['next_run']['workload'] == p['original_workload']

def test_fixed_fractional_rate_must_be_exactly_representable():
    p = build_next_run_policy(report()); advice = {'load_model':'constant-arrival-rate','target':1/7,'duration_seconds':120}
    result = validate_next_run_advice(p, advice)
    assert result['validation_status'] == 'ai_adjusted'
    assert result['next_run']['workload'] == p['original_workload']

@pytest.mark.parametrize('purpose', ['soak', 'stress'])
def test_completed_ramp_goal_does_not_keep_suggesting_growth(purpose):
    r = ramp_report(); r['test_context']['purpose'] = purpose
    if purpose == 'stress': r['test_context']['recommendation_goal'] = 1
    p = build_next_run_policy(r)
    assert p['action'] in {'observation_complete', 'goal_reached'}
    assert not p['can_prefill']

@pytest.mark.parametrize('change,action', [
    ({'load_goal':{'reached':False}}, 'repair_evidence'),
    ({'verdict':'failed','business':{'failure_rate':.1}}, 'repair_failures'),
])
def test_failure_plans_keep_monitoring_gaps_and_resource_observations(change, action):
    r = report(); r.update(change)
    r['monitoring']['services'][0]['metrics'].pop()
    p = build_next_run_policy(r)
    assert p['action'] == action
    assert p['observations']
    assert any('CPU/内存' in item for item in p['limitations'])
    assert '监控' in p['objective']

def test_failure_plan_does_not_hide_concurrent_resource_signal():
    r = report(); r.update(verdict='failed', business={'failure_rate':.1})
    r['monitoring']['services'][0]['metrics'][0]['series'][0]['points'][0]['value'] = 95
    p = build_next_run_policy(r)
    assert p['action'] == 'repair_failures'
    assert any('资源异常' in item for item in p['limitations'])

@pytest.mark.parametrize('missing', ['transport','business','workflow'])
def test_missing_error_section_blocks_growth(missing):
    r = report(); r.pop(missing)
    assert build_next_run_policy(r)['action'] == 'repair_evidence'

def test_incomplete_fixed_workload_is_not_executable():
    r = report(); r['evidence']['workload_snapshot'].pop('max_vus')
    assert build_next_run_policy(r)['action'] == 'review_profile'

@pytest.mark.parametrize('action', ['observe_same_load','increase_bounded'])
def test_fixed_advice_cannot_reduce_pressure_or_shorten_observation(action):
    p = build_next_run_policy(report()); p['action'] = action
    advice = deepcopy(p['next_run']); advice.update(load_model='constant-arrival-rate')
    advice['workload']['rate'] = 9; advice['workload']['duration_seconds'] = 10
    assert validate_next_run_advice(p, advice)['validation_status'] == 'ai_adjusted'

def test_ramp_recovery_target_cannot_be_raised_even_if_direction_stays_down():
    p = build_next_run_policy(ramp_report()); advice = deepcopy(p['next_run'])
    advice['workload']['stages'][-1]['target'] = 3
    assert validate_next_run_advice(p, advice)['validation_status'] == 'ai_adjusted'

def test_ai_fallback_and_adjustment_return_coherent_original_curve_plan():
    p = build_next_run_policy(report())
    fallback = validate_next_run_advice(p, {}, fallback=True)
    adjusted = validate_next_run_advice(p, {'load_model':'constant-arrival-rate','target':999,'duration_seconds':10})
    for result in (fallback, adjusted):
        assert result['next_run']['workload'] == p['original_workload']
        assert result['action'] == 'repeat_original'
        assert result['can_prefill']
        assert '原配置' in result['objective']

@pytest.mark.parametrize('purpose,action', [('smoke','smoke_complete'),('soak','observation_complete')])
def test_completed_action_has_completed_status(purpose, action):
    r = report(); r['test_context']['purpose'] = purpose
    p = build_next_run_policy(r)
    assert p['action'] == action
    assert p['continuation_status'] == '已完成本轮目标'

def test_known_failure_takes_priority_over_not_reached_but_inconclusive_alone_does_not():
    r = report(); r['load_goal']['reached'] = False; r['verdict'] = 'inconclusive'
    assert build_next_run_policy(r)['action'] == 'repair_evidence'
    r['business']['failure_rate'] = .1
    assert build_next_run_policy(r)['action'] == 'repair_failures'
    r['business']['failure_rate'] = 0
    r['thresholds'] = [{'required':True, 'passed':False, 'actual':900}]
    assert build_next_run_policy(r)['action'] == 'repair_failures'

@pytest.mark.parametrize('mutate,expected', [
    (lambda r: r.update(scenario_safety={}), 'review_side_effects'),
    (lambda r: r['evidence'].update(workload_snapshot={}), 'review_profile'),
    (lambda r: r['monitoring'].update(services=[]), 'connect_monitoring'),
    (lambda r: r.update(verdict='failed'), 'repair_failures'),
    (lambda r: r['load_goal'].update(reached=False), 'repair_evidence'),
])
def test_ai_failure_cannot_bypass_existing_policy_gate(mutate, expected):
    r = report(); mutate(r); p = build_next_run_policy(r)
    before = (p['action'], p['reason'], p['objective'], p['continuation_status'], p['can_prefill'])
    for result in (validate_next_run_advice(p, {}, fallback=True), validate_next_run_advice(p, {'workload':{'bad':1}})):
        assert (result['action'], result['reason'], result['objective'], result['continuation_status'], result['can_prefill']) == before

@pytest.mark.parametrize('purpose,expected', [('stress','observe_same_load'),('soak','observe_same_load')])
def test_fixed_goal_or_soak_must_finish_requested_observation_first(purpose, expected):
    r = report(); r['test_context'].update(purpose=purpose, recommendation_goal=10, observation_seconds=300)
    r['evidence']['workload_snapshot']['duration_seconds'] = 60
    assert build_next_run_policy(r)['action'] == expected

def test_ramp_incomplete_observation_offers_full_curve_observation():
    r = ramp_report(); r['test_context']['observation_seconds'] = 300
    p = build_next_run_policy(r)
    assert p['action'] == 'observe_full_curve' and p['can_prefill']
    assert p['next_run']['workload'] == r['evidence']['workload_snapshot']

def test_ramp_ai_cannot_change_pressure_and_duration_together():
    r = ramp_report(); r['test_context']['observation_seconds'] = 300
    p = build_next_run_policy(r); advice = deepcopy(p['next_run'])
    advice['workload']['stages'][0]['target'] = 66
    advice['workload']['stages'][1]['target'] = 66
    advice['workload']['stages'][0]['duration_seconds'] = 60
    assert validate_next_run_advice(p, advice)['validation_status'] == 'ai_adjusted'

def test_full_curve_observation_cannot_raise_pressure_even_without_duration_change():
    r = ramp_report(); r['test_context']['observation_seconds'] = 300
    p = build_next_run_policy(r); advice = deepcopy(p['next_run'])
    advice['workload']['stages'][0]['target'] = 66
    advice['workload']['stages'][1]['target'] = 66
    result = validate_next_run_advice(p, advice)
    assert result['validation_status'] == 'ai_adjusted'
    assert result['next_run']['workload'] == p['original_workload']

def test_resource_pairing_ignores_prometheus_metric_name_label():
    r = report(); metrics = r['monitoring']['services'][0]['metrics']
    metrics[0]['series'][0]['labels'] = {'instance':'a','id':'/demo'}
    metrics[1]['series'][0]['labels'] = {'__name__':'container_memory_working_set_bytes','instance':'a','id':'/demo'}
    assert build_next_run_policy(r)['action'] == 'increase_bounded'

def test_host_network_device_series_does_not_require_cpu_and_memory_pair():
    r = report(); r['monitoring']['services'][0]['metrics'].append({'key':'network_receive_bytes_per_second','coverage':{'valid_ratio':1},'series':[{'labels':{'instance':'a','device':'eth0'},'points':[{'value':10}]}]})
    assert build_next_run_policy(r)['action'] == 'increase_bounded'
