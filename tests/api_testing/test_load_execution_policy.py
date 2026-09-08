import pytest
from task_server.api_testing.services.load_execution_policy import parse_test_context, parse_stop_policy, safety_thresholds
from task_server.api_testing.services.load_scenario_compiler import compile_scenario
from tests.api_testing.test_load_scenario_compiler import SEARCH_CHAIN, FIXED_RATE

@pytest.mark.parametrize('value', [{}, {'http_error_rate': 0, 'grace_seconds': 30}, {'http_error_rate': True, 'grace_seconds': 30}, {'http_error_rate': .1, 'grace_seconds': 1}, {'http_error_rate': float('nan'), 'grace_seconds': 30}])
def test_reject_invalid_stop_policy(value):
    with pytest.raises(ValueError): parse_stop_policy(value)

def test_safety_policy_is_in_immutable_script_hash_and_has_delayed_abort():
    policy = {'http_error_rate': .2, 'grace_seconds': 30}
    original = compile_scenario(SEARCH_CHAIN, FIXED_RATE)
    guarded = compile_scenario(SEARCH_CHAIN, FIXED_RATE, stop_policy=policy)
    assert original.content_hash != guarded.content_hash
    assert guarded.options['thresholds'] == safety_thresholds(policy)
    assert guarded.options['thresholds']['http_req_failed'][0]['delayAbortEval'] == '30s'
    assert 'abortOnFail' in guarded.script

def test_context_no_unknowns_no_mutable_reference():
    data = {'purpose': 'stress', 'release': ' V2 '}
    parsed = parse_test_context(data)
    data['release'] = 'later'
    assert parsed['release'] == 'V2'
    with pytest.raises(ValueError): parse_test_context({'purpose': 'stress', 'script': 'raw js'})

def test_guard_cancels_unclaimed_and_signals_running_peers():
    from types import SimpleNamespace
    from task_server.api_testing.services.load_execution_policy import stop_peers_after_failure
    run=SimpleNamespace(configuration={'stop_policy':{'http_error_rate':.1,'grace_seconds':30}},state='running',summary={},stop_reason=None)
    peers=[SimpleNamespace(state=s) for s in ['failed','assigned','running','finished']]
    stop_peers_after_failure(run,peers,'bad-node')
    assert [p.state for p in peers]==['failed','cancelled','stopping','finished']
    assert run.summary['automatic_stop']['trigger_shard_id']=='bad-node'
    assert run.state=='stopping'
