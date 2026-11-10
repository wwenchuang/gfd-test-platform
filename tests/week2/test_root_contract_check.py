import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from task_server.services import week2_real as service


@pytest.fixture
def operation():
    ref = '#/components/schemas/Result'
    return {'responses': {'200': {'content': {'application/json': {'schema': {'$ref': ref}}}}},
            'resolved_dependencies': {ref: {'type': 'object', 'required': ['code', 'msg', 'data'],
                                               'properties': {'data': {'$ref': '#/components/schemas/Cycle'}}}}}


def test_root_required_only_uses_actual_contract_and_offline_negative_controls(operation):
    result = service._summarize_root_contract(operation, json.dumps({'code': 0, 'msg': 'private', 'data': []}))
    assert result == {'status': 'checked', 'scope': 'root_required_only', 'fields': [
        {'name': name, 'present': True, 'negative_control_rejected': True} for name in ('code', 'msg', 'data')]}
    assert 'private' not in json.dumps(result)


def test_missing_root_field_is_not_reported_as_a_successful_negative_control(operation):
    result = service._summarize_root_contract(operation, '{"code":0,"data":[]}')
    assert result['status'] == 'checked'
    assert [field['present'] for field in result['fields']] == [True, False, True]
    assert all(field['negative_control_rejected'] is None for field in result['fields'])


@pytest.mark.parametrize('change', ['missing_ref', 'external_ref', 'ref_siblings', 'wrong_required',
                                   'duplicate_required', 'not_object', 'invalid_body', 'unknown_status', 'empty',
                                   'root_composition', 'duplicate_body_keys', 'nan', 'target_not_mapping'])
def test_unverifiable_contract_is_not_treated_as_checked(operation, change):
    op = copy.deepcopy(operation)
    schema = op['responses']['200']['content']['application/json']['schema']
    ref = schema['$ref']
    body, status = '{"code":0,"msg":"ok","data":[]}', 200
    if change == 'missing_ref': op['resolved_dependencies'].clear()
    elif change == 'external_ref': schema['$ref'] = 'https://example.com/private'
    elif change == 'ref_siblings': schema['allOf'] = []
    elif change == 'wrong_required': op['resolved_dependencies'][ref]['required'].append('private_field')
    elif change == 'duplicate_required': op['resolved_dependencies'][ref]['required'].append('msg')
    elif change == 'not_object': op['resolved_dependencies'][ref]['type'] = 'array'
    elif change == 'invalid_body': body = '{"code":'
    elif change == 'unknown_status': status = 500
    elif change == 'empty': op = {}
    elif change == 'root_composition': op['resolved_dependencies'][ref]['allOf'] = []
    elif change == 'duplicate_body_keys': body = '{"code":0,"code":1,"msg":"ok","data":[]}'
    elif change == 'nan': body = '{"code":0,"msg":"ok","data":NaN}'
    elif change == 'target_not_mapping': op['resolved_dependencies'][ref] = None
    result = service._summarize_root_contract(op, body, status=status)
    assert result == {'status': 'unavailable', 'scope': 'root_required_only', 'fields': []}


def test_contract_scope_binds_case_endpoint_version_and_project(operation):
    execution = SimpleNamespace(id='run', source_revision_id='rev', project_id='project', environment_revision_id='env')
    child = SimpleNamespace(execution_id='run', endpoint_id='endpoint', environment_revision_id='env')
    endpoint = SimpleNamespace(id='endpoint', revision_id='rev', method='GET', path='/print3d/api/v1/feedback/category/first', operation=operation)
    revision = SimpleNamespace(id='rev', source_id='source')
    source = SimpleNamespace(id='source', project_id='project')
    env_revision = SimpleNamespace(id='env', environment_id='environment')
    environment = SimpleNamespace(id='environment', project_id='project')
    with patch('task_server.api_testing.http._factory', return_value='factory'), \
         patch('task_server.api_testing.http._scope_execution', return_value=execution), \
         patch('task_server.api_testing.http._scope_execution_case', return_value=child), \
         patch('task_server.api_testing.http._scope_endpoint', return_value=endpoint), \
         patch('task_server.api_testing.http._scope_source_revision', return_value=revision), \
         patch('task_server.api_testing.http._scope_source', return_value=source), \
         patch('task_server.api_testing.http._scope_environment_revision', return_value=env_revision), \
         patch('task_server.api_testing.http._scope_environment', return_value=environment):
        assert service._contract_operation_authorized('user', 'run', 'case') == operation
        for obj, attr, bad in ((child, 'execution_id', 'different'), (child, 'environment_revision_id', 'different'),
                               (endpoint, 'revision_id', 'different'), (source, 'project_id', 'different'),
                               (endpoint, 'method', 'POST'), (endpoint, 'path', '/different'),
                               (environment, 'project_id', 'different')):
            old = getattr(obj, attr)
            setattr(obj, attr, bad)
            with pytest.raises(ValueError): service._contract_operation_authorized('user', 'run', 'case')
            setattr(obj, attr, old)
