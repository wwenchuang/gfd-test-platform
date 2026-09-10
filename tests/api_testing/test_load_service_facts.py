"""Service capabilities are explicit versioned declarations, not inferred topology."""
from copy import deepcopy
import pytest

from task_server.api_testing.services.environment_service import _normalize_services, EnvironmentInputError


SOURCE = {'kind': 'source_review', 'reference': 'deploy/load-demo/server.py @ reviewed revision'}


def facts(state='absent'):
    return {'version': 1, 'components': [{'key': 'database', 'state': state, 'source': SOURCE}], 'behaviors': []}


def test_environment_normalizes_operator_provenance_without_discarding_other_metadata():
    value = {'default': {'base_url': 'https://example.com', 'metadata': {'allow_private_network': True, 'load_service_facts': facts()}}}
    original = deepcopy(value)
    result = _normalize_services(value)['default']['metadata']
    assert result['load_service_facts']['components'][0]['source']['recorded_by_operator'] is True
    assert result['allow_private_network'] is True
    assert value == original


@pytest.mark.parametrize('bad', [
    {'version': 2},
    {'version': True},
    {'version': 1, 'components': [{'key': 'database', 'state': 'absent'}]},
    {'version': 1, 'components': [{'key': 'database', 'state': 'maybe'}]},
    {'version': 1, 'components': [{'key': 'database', 'state': 'absent', 'source': {'kind': 'automatic', 'reference': 'x'}}]},
    {'version': 1, 'components': [{'key': 'database', 'state': 'unknown', 'limit': 4}]},
    {'version': 1, 'behaviors': [{'kind': 'bounded_work_slots', 'state': 'present', 'source': SOURCE, 'limit': True}]},
    {'version': 1, 'behaviors': [{'kind': 'bounded_work_slots', 'state': 'unknown', 'limit': 4}]},
    {'version': 1, 'components': facts()['components'] * 2},
])
def test_invalid_fact_contract_cannot_be_saved(bad):
    with pytest.raises(EnvironmentInputError, match='服务能力'):
        _normalize_services({'default': {'base_url': 'https://example.com', 'metadata': {'load_service_facts': bad}}})


def test_unknown_and_missing_are_not_absent():
    from task_server.api_testing.services.load_service_facts import snapshot_service_facts, fact_catalog
    snapshot = snapshot_service_facts({}, {'steps': [{'id': 's1', 'request': {'service': 'default', 'path': '/cpu'}}]})
    assert snapshot['services']['default']['components'] == []
    catalog = fact_catalog({'evidence': {'environment_snapshot': {'service_facts': snapshot}}})
    assert next(x for x in catalog if x['component'] == 'database')['state'] == 'unknown'


def test_source_reference_credentials_are_redacted_before_persistence():
    value = facts()
    value['components'][0]['source'] = {'kind': 'operator_declaration', 'reference': 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456'}
    result = _normalize_services({'default': {'base_url': 'https://example.com', 'metadata': {'load_service_facts': value}}})
    assert 'abcdefghijklmnopqrstuvwxyz123456' not in str(result)


def test_truncated_service_inventory_cannot_prove_all_database_dependencies_absent():
    from task_server.api_testing.services.load_service_facts import fact_catalog
    services = {str(i): {'components': facts()['components']} for i in range(31)}
    services['30'] = {'components': []}
    catalog = fact_catalog({'evidence': {'environment_snapshot': {'service_facts': {'version': 1, 'services': services}}}})
    assert any(x['component'] == 'database' and x['state'] == 'unknown' for x in catalog)


def test_snapshot_scopes_behaviors_to_endpoints_and_does_not_copy_unrelated_metadata():
    from task_server.api_testing.services.load_service_facts import snapshot_service_facts
    source = {'default': {'load_service_facts': {**facts(), 'behaviors': [
        {'kind': 'cpu_wall_time_work', 'state': 'present', 'duration_ms': 150, 'endpoints': ['/cpu'], 'source': SOURCE},
        {'kind': 'shared_ttl_allocation', 'state': 'present', 'bytes': 67108864, 'ttl_seconds': 30,
         'refresh_on_request': False, 'endpoints': ['/memory'], 'source': SOURCE},
    ]}, 'token': 'never-copy'}, 'other': {'load_service_facts': facts('present')}}
    snapshot = snapshot_service_facts(source, {'steps': [{'id': 's1', 'request': {'service': 'default', 'path': '/cpu'}}]})
    source['default']['load_service_facts']['components'][0]['state'] = 'present'
    assert list(snapshot['services']) == ['default']
    assert snapshot['services']['default']['components'][0]['state'] == 'absent'
    assert [b['kind'] for b in snapshot['services']['default']['behaviors']] == ['cpu_wall_time_work']
    assert 'never-copy' not in str(snapshot)
