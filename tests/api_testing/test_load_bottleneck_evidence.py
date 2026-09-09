from copy import deepcopy
import json
import pytest
from task_server.api_testing.services.load_bottleneck_evidence import build_bottleneck_evidence


def domains(report):
    return {row['domain']: row for row in build_bottleneck_evidence(report)}


def monitored(key, *, scope='container', points=None, coverage=1):
    return {'monitoring': {'services': [{'revision_id': 'm1', 'scope': scope, 'state': 'completed', 'metrics': [
        {'key': key, 'coverage': {'valid_ratio': coverage}, 'series': [{'points': points if points is not None else [{'value': 10}, {'value': 20}, {'value': 30}]}]}
    ]}]}}


def test_empty_report_has_nine_unknown_domains_not_invented_inapplicability():
    rows = build_bottleneck_evidence({'test_context': {'notes': 'demo没有数据库'}})
    assert len(rows) == 9
    assert all(r['status'] == 'missing' for r in rows)
    assert all(r['next_verification'] and r['limitations'] for r in rows)
    assert '未配置' in domains({})['database']['limitations'][0]


@pytest.mark.parametrize('key,domain,phrase', [
    ('cpu_cores', 'cpu', '节流'), ('memory_working_set_bytes', 'memory', 'GC'),
    ('postgres_connections', 'database', 'SQL'), ('network_receive_bytes_per_second', 'network', '重传'),
    ('disk_read_latency_ms', 'storage', '队列'),
])
def test_existing_metrics_are_partial_signals_not_confirmed_roots(key, domain, phrase):
    row = domains(monitored(key))[domain]
    assert row['status'] == 'partial'
    assert row['evidence_ids'] == ['monitoring.m1']
    assert phrase in ''.join(row['limitations'])


@pytest.mark.parametrize('value', [None, True, float('nan'), float('inf'), '20'])
def test_invalid_or_absent_points_never_prove_monitoring_available(value):
    row = domains(monitored('cpu_cores', points=[{'value': value}]))['cpu']
    assert row['status'] == 'missing'
    assert not row['evidence_ids']


def test_poor_coverage_and_host_scope_are_explicit():
    row = domains(monitored('cpu_percent', scope='host', coverage=.3))['cpu']
    assert row['status'] == 'partial'
    assert '覆盖' in ''.join(row['limitations'])
    assert '主机' in ''.join(row['limitations'])


def test_step_latency_does_not_invent_downstream_or_pool_telemetry():
    rows = domains({'steps': [{'id': 's1', 'requests': 10, 'p95_ms': 100, 'business_failure_rate': 0}]})
    assert rows['downstream']['status'] == 'missing'
    assert rows['application_pool']['status'] == 'missing'
    assert rows['test_data']['status'] == 'partial'
    assert rows['test_data']['evidence_ids'] == ['step.s1']


def test_generator_requires_every_completed_agent_and_valid_runtime_evidence():
    agent = {'id': 'a1', 'state': 'finished', 'load_generator_resources': {'samples': [
        {'cpu_percent': 30, 'memory_percent': 20} for _ in range(3)]}}
    report = {'agents': [agent], 'load_goal': {'reached': True}, 'evidence': {'complete': True}, 'dropped_iterations': {'count': 0}}
    assert domains(report)['load_generator']['status'] == 'available'
    report['agents'].append({'id': 'a2', 'state': 'finished'})
    assert domains(report)['load_generator']['status'] == 'partial'


def test_readonly_bounded_and_no_free_text_or_secrets_echoed():
    report = monitored('cpu_cores')
    report['monitoring']['services'][0]['name'] = 'SECRET_TOKEN'
    report['monitoring']['services'][0]['metrics'][0]['series'][0]['labels'] = {'password': 'SECRET_TOKEN'}
    original = deepcopy(report)
    first = build_bottleneck_evidence(report)
    assert first == build_bottleneck_evidence(report)
    assert report == original
    assert 'SECRET_TOKEN' not in json.dumps(first)
    assert len(first) <= 9


def test_partial_agent_cpu_only_is_not_absent_evidence():
    report = {'agents': [{'id': 'a1', 'load_generator_resources': {'samples': [{'cpu_percent': 30}]}}]}
    row = domains(report)['load_generator']
    assert row['status'] == 'partial'
    assert row['evidence_ids'] == ['agent.a1']


def test_extreme_numbers_do_not_crash_inventory():
    assert domains(monitored('cpu_cores', points=[{'value': 10 ** 1000}]))['cpu']['status'] == 'missing'


def test_sampling_budget_is_disclosed():
    report = monitored('cpu_cores')
    report['monitoring']['services'] *= 11
    assert any('截取' in text for row in build_bottleneck_evidence(report) for text in row['limitations'])
