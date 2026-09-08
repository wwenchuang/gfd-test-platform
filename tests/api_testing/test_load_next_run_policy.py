from copy import deepcopy
import pytest
from task_server.api_testing.services.load_next_run_policy import build_next_run_policy, resource_observations, number
from task_server.api_testing.services.load_execution_policy import parse_test_context


def report():
    metrics = [{'key': key, 'unit':'%', 'coverage':{'valid_ratio':1}, 'series':[{'labels':{'instance':'a'},'points':[{'value':v} for v in [20,30,25]]}]} for key in ['cpu_percent','memory_percent']]
    return {'scenario_safety':{'readonly':True},'verdict':'passed','load_goal':{'reached':True},'evidence':{'complete':True,'workload_snapshot':{'executor':'constant-arrival-rate','rate':10,'time_unit':'1s','duration_seconds':120}},'test_context':{'purpose':'stress','recommendation_goal':20,'max_step_percent':20,'observation_seconds':120},'monitoring':{'services':[{'revision_id':'m','name':'业务主机','scope':'host','state':'completed','metrics':metrics}]}}


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
