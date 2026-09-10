"""Each accepted action has an explicit scope and relevant run evidence."""
from copy import deepcopy
import pytest
from task_server.api_testing.services.load_ai_analysis_service import build_evidence_package, _validate_result, LoadAiAnalysisError
from tests.api_testing.test_load_ai_analysis_service import _report, _analysis
from tests.api_testing.test_load_service_facts import SOURCE, facts


def demo_report(db='absent'):
    report = _report()
    report['evidence']['environment_snapshot']['service_facts'] = {'version': 1, 'services': {'default': {
        'step_ids': ['search'], 'components': facts(db)['components'], 'behaviors': [
            {'kind': 'bounded_work_slots', 'state': 'present', 'limit': 4, 'source': SOURCE},
            {'kind': 'shared_ttl_allocation', 'state': 'present', 'bytes': 67108864, 'ttl_seconds': 30,
             'refresh_on_request': False, 'endpoints': ['/memory'], 'source': SOURCE}]}}}
    return report


def recommendation(option):
    return {'priority': 'high', **{key: deepcopy(option[key]) for key in
            ('domain', 'service_key', 'intent', 'action_code', 'fact_ids', 'evidence_ids')}}


def test_absent_database_has_no_sql_actions_but_known_work_slots_have_cited_collection():
    evidence = build_evidence_package(demo_report())
    options = evidence['recommendation_options']
    assert not any(x['domain'] == 'database' for x in options)
    option = next(x for x in options if x['action_code'] == 'observe_work_slot_rejections')
    assert option['fact_ids'] and option['evidence_ids'] == ['step.search']
    result = _validate_result({**_analysis(), 'recommendations': [recommendation(option)]}, evidence)
    assert result['recommendation_contract_version'] == 1
    assert '工作槽' in result['recommendations'][0]['action']
    assert '4' in result['recommendations'][0]['verification']


def test_unknown_database_allows_only_confirmation_and_old_reports_stay_unknown():
    for report in (_report(), demo_report('unknown')):
        evidence = build_evidence_package(report)
        options = [x for x in evidence['recommendation_options'] if x['domain'] == 'database']
        assert options and all(x['intent'] == 'confirm_component' for x in options)


@pytest.mark.parametrize('change', [
    {'evidence_ids': ['latency.summary']},
    {'service_key': 'other-service'},
    {'fact_ids': []},
    {'action': '检查 SQL 与 OOM'},
    {'intent': 'confirmed_root_cause'},
    {'action_code': 'change_pool_limit'},
])
def test_cross_domain_missing_fact_or_free_text_actions_are_rejected(change):
    evidence = build_evidence_package(demo_report('present'))
    option = next(x for x in evidence['recommendation_options'] if x['action_code'] == 'collect_database_observations')
    row = {**recommendation(option), **change}
    with pytest.raises(LoadAiAnalysisError, match='建议'):
        _validate_result({**_analysis(), 'recommendations': [row]}, evidence)


def test_unbound_metrics_remain_environment_scoped_and_have_metric_ids():
    report = demo_report()
    report['monitoring'] = {'services': [{'revision_id': 'm1', 'scope': 'container', 'state': 'completed', 'metrics': [
        {'key': 'cpu_cores', 'peak': .2, 'coverage': {'valid_ratio': 1}}]}]}
    evidence = build_evidence_package(report)
    option = next(x for x in evidence['recommendation_options'] if x['action_code'] == 'inspect_cpu_observation')
    assert option['service_key'] is None
    assert option['evidence_ids'] == ['monitoring.m1.cpu_cores']
    row = {**recommendation(option), 'service_key': 'default'}
    with pytest.raises(LoadAiAnalysisError):
        _validate_result({**_analysis(), 'recommendations': [row]}, evidence)


def test_absent_component_coverage_is_not_applicable_only_when_every_service_agrees():
    from task_server.api_testing.services.load_bottleneck_evidence import build_bottleneck_evidence
    report = demo_report()
    row = next(x for x in build_bottleneck_evidence(report) if x['domain'] == 'database')
    assert row['status'] == 'not_applicable'
    assert row['fact_ids']
    assert 'SQL' not in row['next_verification']
    report['evidence']['environment_snapshot']['service_facts']['services']['other'] = {'step_ids': [], 'components': [], 'behaviors': []}
    row = next(x for x in build_bottleneck_evidence(report) if x['domain'] == 'database')
    assert row['status'] != 'not_applicable'


def test_fallback_obeys_the_same_action_contract_without_changing_original_curve():
    from task_server.api_testing.services.load_ai_analysis_service import _citation_safe_fallback
    from task_server.api_testing.services.load_recommendation_contract import validate_recommendations
    evidence = build_evidence_package(demo_report())
    result = _citation_safe_fallback(evidence, TimeoutError())
    rows = [{k: v for k, v in row.items() if k not in {'action', 'verification'}} for row in result['recommendations']]
    assert validate_recommendations(rows, evidence) == result['recommendations']
    assert result['recommendation_contract_version'] == 1
    assert result['service_facts'] == evidence['service_facts']


def test_cpu_facts_do_not_supply_db_or_other_service_evidence():
    evidence = build_evidence_package(demo_report('present'))
    db = next(x for x in evidence['recommendation_options'] if x['action_code'] == 'collect_database_observations')
    work = next(x for x in evidence['recommendation_options'] if x['action_code'] == 'observe_work_slot_rejections')
    row = {**recommendation(db), 'fact_ids': work['fact_ids']}
    with pytest.raises(LoadAiAnalysisError):
        _validate_result({**_analysis(), 'recommendations': [row]}, evidence)


def test_coverage_unknowns_do_not_propose_component_parameter_changes():
    from task_server.api_testing.services.load_bottleneck_evidence import build_bottleneck_evidence
    report = demo_report()
    service = report['evidence']['environment_snapshot']['service_facts']['services']['default']
    service['behaviors'] = [{'kind': 'cpu_wall_time_work', 'state': 'absent', 'source': SOURCE}]
    rows = build_bottleneck_evidence(report)
    assert all('调整' not in row['next_verification'] and '改变' not in row['next_verification'] for row in rows)


def test_endpoint_behavior_cannot_cite_a_different_endpoint_on_the_same_service():
    from task_server.api_testing.services.load_service_facts import snapshot_service_facts
    metadata = {'default': {'load_service_facts': {**facts(), 'behaviors': [
        {'kind': 'cpu_wall_time_work', 'state': 'present', 'duration_ms': 150, 'endpoints': ['/cpu'], 'source': SOURCE}]}}}
    snapshot = snapshot_service_facts(metadata, {'steps': [
        {'id': 'cpu', 'request': {'service': 'default', 'path': '/cpu'}},
        {'id': 'health', 'request': {'service': 'default', 'path': '/health'}}]})
    report = _report()
    report['evidence']['environment_snapshot']['service_facts'] = snapshot
    report['steps'] = [{'id': 'cpu'}, {'id': 'health'}]
    evidence = build_evidence_package(report)
    option = next(x for x in evidence['recommendation_options'] if x['action_code'] == 'inspect_cpu_work')
    assert option['evidence_ids'] == ['step.cpu']
    row = {**recommendation(option), 'evidence_ids': ['step.health']}
    with pytest.raises(LoadAiAnalysisError, match='建议'):
        _validate_result({**_analysis(), 'evidence': ['load.goal'], 'recommendations': [row]}, evidence)
    assert next(f for f in evidence['service_facts'] if f['component'] == 'database')['step_ids'] == ['cpu', 'health']
