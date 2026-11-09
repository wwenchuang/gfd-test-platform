from types import SimpleNamespace

import pytest

from task_server.api_testing.assertions import _schema_matches
from task_server.api_testing.contracts.case import _parse_assertions
from task_server.api_testing.services.basic_case_service import BasicCaseService
from task_server.api_testing.services.response_schema_draft import response_schema_draft


def payload(schema, dependencies=None, openapi_version=None):
    endpoint = SimpleNamespace(
        method='GET', path='/feedback/category/first', summary='反馈分类', tags=[],
        operation={
            'responses': {'200': {'content': {'application/json': {'schema': schema}}}},
            'resolved_dependencies': dependencies or {},
        },
    )
    return BasicCaseService.build_case_payload(endpoint, SimpleNamespace(default_headers={}), (), openapi_version=openapi_version)


def test_referenced_response_contract_becomes_disabled_reviewable_assertion():
    ref = '#/components/schemas/CategoryList'
    schema = {'$ref': ref}
    dependencies = {ref: {'type': 'object', 'required': ['code', 'msg', 'data'], 'properties': {
        'code': {'type': 'integer'}, 'msg': {'type': 'string'},
        'data': {'type': 'array', 'items': {'type': 'object', 'required': ['name'], 'properties': {'name': {'type': 'string'}}}},
    }}}
    result = payload(schema, dependencies)
    assertion = next(item for item in result['assertions'] if item['type'] == 'schema')
    assert assertion['enabled'] is False
    assert _parse_assertions([assertion])[0]['enabled'] is False
    assert '审核后启用' in result['purpose']
    assert _schema_matches({'code': 0, 'msg': 'ok', 'data': [{'name': 'A'}]}, assertion['expected'])
    assert not _schema_matches({'code': 0, 'msg': 'ok', 'data': [{}]}, assertion['expected'])
    assert dependencies[ref]['properties']['data']['items']['required'] == ['name']


@pytest.mark.parametrize('schema,dependencies', [
    ({'$ref': 'https://example.com/schema'}, {}),
    ({'$ref': '#/components/schemas/Missing'}, {}),
    ({'$ref': '#/components/schemas/Recursive'}, {'#/components/schemas/Recursive': {'type': 'array', 'items': {'$ref': '#/components/schemas/Recursive'}}}),
    ({'type': 'string', 'format': 'email'}, {}),
    ({'$ref': '#/components/schemas/A', 'description': 'sibling'}, {'#/components/schemas/A': {'type': 'string'}}),
    ({'type': 'object', 'properties': {'secret': {'type': 'string', 'writeOnly': True}}}, {}),
    ({'type': 'string', 'readOnly': 'true'}, {}),
    ({'properties': {'name': {'type': 'string'}}}, {}),
    ({'type': 'string', 'enum': ['x' * (256 * 1024)]}, {}),
])
def test_unsupported_contract_is_never_partially_enabled_or_silently_accepted(schema, dependencies):
    result = payload(schema, dependencies)
    assert not any(item['type'] == 'schema' for item in result['assertions'])
    assert '未生成响应结构断言' in result['purpose']


def test_response_object_reference_requires_review():
    operation = {'responses': {'200': {'$ref': '#/components/responses/Success',
                                       'description': 'sibling'}}}
    assertion, explanation = response_schema_draft(operation)
    assert assertion is None
    assert '响应引用需人工核对' in explanation


def test_nullable_type_is_converted_without_weakening_required_properties():
    result = payload({'type': 'object', 'required': ['value'], 'properties': {
        'value': {'type': 'string', 'nullable': True},
    }}, openapi_version='3.0.3')
    assertion = next(item for item in result['assertions'] if item['type'] == 'schema')
    assert _schema_matches({'value': None}, assertion['expected'])
    assert not _schema_matches({}, assertion['expected'])


@pytest.mark.parametrize('schema,version', [
    ({'type': 'string', 'nullable': True}, '3.0.3'),
    ({'type': ['string', 'null']}, '3.1.0'),
    ({'anyOf': [{'type': 'string'}, {'type': 'null'}]}, '3.1.0'),
    ({'enum': [None, 'ready']}, '3.1.0'),
])
def test_top_level_null_contract_is_rejected_until_executor_can_distinguish_json_null(schema, version):
    result = payload(schema, openapi_version=version)
    assert not any(item['type'] == 'schema' for item in result['assertions'])
    assert '未生成响应结构断言' in result['purpose']


@pytest.mark.parametrize('schema,dependencies', [
    ({'type': 'string', 'const': '{{recipient}}'}, {}),
    ({'type': 'object', 'properties': {'{{recipient}}': {'type': 'string'}}}, {}),
    ({'type': 'object', 'enum': [{'value': '{{recipient}}'}]}, {}),
    ({'$ref': '#/components/schemas/A'}, {'#/components/schemas/A': {'type': 'string', 'enum': ['{{recipient}}']}}),
    ({'type': 'string', 'const': 'incomplete {{'}, {}),
])
def test_draft_rejects_any_potential_variable_placeholder(schema, dependencies):
    result = payload(schema, dependencies)
    assert not any(item['type'] == 'schema' for item in result['assertions'])
    assert '变量占位符' in result['purpose']


@pytest.mark.parametrize('version', [None, '3.1.0'])
def test_nullable_is_not_reinterpreted_without_known_openapi_30(version):
    result = payload({'type': 'string', 'nullable': True}, openapi_version=version)
    assert not any(item['type'] == 'schema' for item in result['assertions'])
    assert '未生成响应结构断言' in result['purpose']


def test_annotation_examples_are_not_copied_into_draft():
    result = payload({'type': 'string', 'minLength': 1, 'example': 'sample', 'default': 'sample'})
    assertion = next(item for item in result['assertions'] if item['type'] == 'schema')
    assert assertion['expected'] == {'type': 'string', 'minLength': 1}


def test_basic_candidate_preview_uses_saved_revision_version_and_shows_draft(monkeypatch):
    endpoint = SimpleNamespace(
        id='endpoint-1', method='GET', path='/feedback/category/first', summary='反馈分类', tags=[],
        operation={'responses': {'200': {'content': {'application/json': {'schema': {
            'type': 'object', 'required': ['value'], 'properties': {
                'value': {'type': 'string', 'nullable': True},
            },
        }}}}}},
    )
    service = BasicCaseService(lambda: None)
    monkeypatch.setattr('task_server.api_testing.access.require_permission', lambda *args: None)
    monkeypatch.setattr(service, '_generation_context', lambda *args: (
        [endpoint], SimpleNamespace(default_headers={}), (), (), '3.0.3',
    ))
    preview = service.preview(['endpoint-1'], 'environment-1', 'actor-1')[0]
    assertion = next(item for item in preview['case']['assertions'] if item['type'] == 'schema')
    assert assertion['enabled'] is False
    assert '审核后启用' in preview['case']['purpose']
    assert _schema_matches({'value': None}, assertion['expected'])
