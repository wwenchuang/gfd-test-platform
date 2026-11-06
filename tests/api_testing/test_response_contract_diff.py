"""Comparison of saved API response definitions; no business requests."""
import copy
from types import SimpleNamespace

import pytest

from task_server.api_testing.services.response_contract_diff import compare_response_contracts
from task_server.api_testing.services.source_service import _change_from_payload, _change_payload, _compute_diff


def feedback():
    return {
        'responses': {'200': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/CategoryListResponse'}}}}},
        'resolved_dependencies': {
            '#/components/schemas/CategoryListResponse': {'type': 'object', 'required': ['code', 'msg', 'data'], 'properties': {
                'code': {'type': 'integer'}, 'msg': {'type': 'string'},
                'data': {'type': 'array', 'items': {'$ref': '#/components/schemas/FeedbackFirstCategory'}},
            }},
            '#/components/schemas/FeedbackFirstCategory': {'type': 'object', 'required': ['sn', 'name'], 'properties': {
                'sn': {'type': 'string'}, 'name': {'type': 'string'},
                'children': {'type': 'array', 'nullable': True, 'items': {'$ref': '#/components/schemas/FeedbackFirstCategory'}},
            }},
        },
    }


def kinds(result):
    return {(item['kind'], item['status'], item['path']) for item in result['items']}


def test_feedback_required_and_property_changes_are_separate_and_recursive_is_incomplete():
    old = feedback(); new = copy.deepcopy(old)
    root = new['resolved_dependencies']['#/components/schemas/CategoryListResponse']
    del root['properties']['code']
    result = compare_response_contracts(old, new)
    assert ('property_removed', '200', '/code') in kinds(result)
    assert ('required_removed', '200', '/code') not in kinds(result)
    assert result['complete'] is False
    assert not any(item['path'].startswith('/data/*/children/*/') for item in result['items'])
    new = feedback(); new['resolved_dependencies']['#/components/schemas/CategoryListResponse']['required'].remove('code')
    result = compare_response_contracts(old, new)
    assert ('required_removed', '200', '/code') in kinds(result)
    assert ('property_removed', '200', '/code') not in kinds(result)


def test_http_status_and_json_pointer_escape_are_reported_as_declared_differences():
    old = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'object', 'required': ['a/b~c'], 'properties': {'a/b~c': {'type': 'string'}}}}}}}}
    new = copy.deepcopy(old); schema = new['responses']['200']['content']['application/json']['schema']; schema['required'] = []
    new['responses']['201'] = new['responses'].pop('200')
    status = compare_response_contracts(old, new)
    assert ('status_removed', '200', '') in kinds(status)
    assert ('status_added', '201', '') in kinds(status)
    new['responses']['200'] = new['responses'].pop('201')
    assert ('required_removed', '200', '/a~1b~0c') in kinds(compare_response_contracts(old, new))


def test_enum_order_is_ignored_boolean_and_number_differ_values_never_exposed():
    old = {'responses': {'200': {'content': {'application/json': {'schema': {'enum': ['secret-example', True]}}}}}}
    new = copy.deepcopy(old); new['responses']['200']['content']['application/json']['schema']['enum'] = [True, 'secret-example']
    assert not compare_response_contracts(old, new)['items']
    new['responses']['200']['content']['application/json']['schema']['enum'] = [1, 'secret-example']
    result = compare_response_contracts(old, new)
    assert ('enum_changed', '200', '') in kinds(result)
    assert 'secret-example' not in repr(result)


@pytest.mark.parametrize('schema', [
    {'$ref': '#/components/schemas/Missing'},
    {'type': 'object', 'nullable': True},
    {'allOf': [{'type': 'object'}]},
    {'type': 'string', 'pattern': '.*'},
    {'type': 'object', 'required': [42]},
])
def test_unsupported_or_invalid_schema_is_incomplete_without_fabricated_changes(schema):
    old = {'responses': {'200': {'content': {'application/json': {'schema': schema}}}}}
    result = compare_response_contracts(old, copy.deepcopy(old))
    assert result['complete'] is False
    assert not result['items']


def test_non_json_unexpected_status_and_type_changes_are_incomplete():
    old = {'responses': {'default': {}, '200': {'content': {'application/json': {'schema': {'type': 'string'}}}}}}
    new = copy.deepcopy(old); new['responses']['200']['content']['application/json']['schema']['type'] = 'object'
    assert compare_response_contracts(old, new)['complete'] is False


def test_comparison_is_bounded_and_reports_truncation():
    old = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'object', 'properties': {str(i): {'type': 'string'} for i in range(150)}}}}}}}
    new = copy.deepcopy(old); new['responses']['200']['content']['application/json']['schema']['properties'] = {}
    result = compare_response_contracts(old, new)
    assert result['complete'] is False
    assert len(result['items']) <= 100


def test_existing_source_change_round_trips_new_detail_and_old_payload():
    old, new = feedback(), feedback()
    new['resolved_dependencies']['#/components/schemas/CategoryListResponse']['required'].remove('code')
    base = dict(stable_key='one', operation_id='op', method='GET', path='/feedback', summary='反馈', tags=[])
    change = _compute_diff([SimpleNamespace(**base, operation=old)], [SimpleNamespace(**base, operation=new)])[0]
    assert ('required_removed', '200', '/code') in kinds(change.response_contract_diff)
    assert _change_from_payload(_change_payload(change)) == change
    legacy = _change_payload(change); legacy.pop('response_contract_diff')
    assert _change_from_payload(legacy).response_contract_diff is None


def test_unpaired_schema_reference_never_fabricates_removed_properties():
    old = feedback(); new = copy.deepcopy(old)
    new['responses']['200']['content']['application/json']['schema'] = {'$ref': '#/components/schemas/Unknown'}
    result = compare_response_contracts(old, new)
    assert result['complete'] is False
    assert result['items'] == []


def test_deep_reference_chain_and_array_type_are_not_silently_complete():
    chain = {f'#/components/schemas/R{i}': {'$ref': f'#/components/schemas/R{i+1}'} for i in range(35)}
    chain['#/components/schemas/R35'] = {'type': ['null', 'string']}
    op = {'responses': {'200': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/R0'}}}}}, 'resolved_dependencies': chain}
    assert compare_response_contracts(op, copy.deepcopy(op))['complete'] is False
    short = {'responses': {'200': {'content': {'application/json': {'schema': {'type': ['null', 'string']}}}}}}
    assert compare_response_contracts(short, copy.deepcopy(short))['complete'] is False


def test_enum_number_equivalence_and_non_json_reference_do_not_fabricate_changes():
    left = {'responses': {'200': {'content': {'application/json': {'schema': {'enum': [1, 2]}}}}}}
    right = copy.deepcopy(left); right['responses']['200']['content']['application/json']['schema']['enum'] = [2.0, 1.0]
    assert compare_response_contracts(left, right)['items'] == []


def test_ref_with_semantic_sibling_cannot_claim_required_removed():
    left = feedback(); right = copy.deepcopy(left)
    for op in (left, right):
        schema = op['responses']['200']['content']['application/json']['schema']
        schema['allOf'] = [{'required': ['code']}]
    right['resolved_dependencies']['#/components/schemas/CategoryListResponse']['required'].remove('code')
    result = compare_response_contracts(left, right)
    assert result['complete'] is False
    assert result['items'] == []


def test_deleted_schema_with_unsupported_constraint_marks_comparison_incomplete():
    old = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'object', 'properties': {'foo': {'type': 'string', 'nullable': True}}}}}}}}
    new = copy.deepcopy(old); new['responses']['200']['content']['application/json']['schema']['properties'] = {}
    result = compare_response_contracts(old, new)
    assert result['complete'] is False
    assert ('property_removed', '200', '/foo') in kinds(result)


def test_invalid_duplicate_required_or_read_write_annotations_are_incomplete():
    for schema in ({'type': 'object', 'required': ['code', 'code']}, {'type': 'string', 'writeOnly': True}, {'type': 'nonsense'}):
        operation = {'responses': {'200': {'content': {'application/json': {'schema': schema}}}}}
        assert compare_response_contracts(operation, copy.deepcopy(operation))['complete'] is False
    assert compare_response_contracts({'responses': {}}, {'responses': {}})['complete'] is False


def test_uncompared_headers_and_one_sided_responses_are_incomplete():
    op = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'string'}}}, 'headers': {'X-T': {'schema': {'type': 'string'}}}}}}
    assert compare_response_contracts(op, copy.deepcopy(op))['complete'] is False
    changed = copy.deepcopy(op); changed['responses']['404'] = {'content': {'application/json': {'schema': {'type': 'string'}}}}
    result = compare_response_contracts(op, changed)
    assert ('status_added', '404', '') in kinds(result)
    assert result['complete'] is False


def test_huge_json_number_comparison_does_not_round_and_star_field_is_not_ambiguous():
    old = {'responses': {'200': {'content': {'application/json': {'schema': {'enum': [10**29 + 1]}}}}}}
    new = copy.deepcopy(old); new['responses']['200']['content']['application/json']['schema']['enum'] = [10**29 + 2]
    assert ('enum_changed', '200', '') in kinds(compare_response_contracts(old, new))
    old = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'object', 'required': ['*'], 'properties': {'*': {'type': 'string'}}}}}}}}
    new = copy.deepcopy(old); new['responses']['200']['content']['application/json']['schema']['required'] = []
    result = compare_response_contracts(old, new)
    assert result['complete'] is False
    assert result['items'] == []


def test_object_constraints_under_nonobject_or_unspecified_type_are_not_claimed():
    left = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'array', 'items': {'type': 'string'}}}}}}}
    right = copy.deepcopy(left); right['responses']['200']['content']['application/json']['schema']['required'] = ['x']
    result = compare_response_contracts(left, right)
    assert result['complete'] is False
    assert result['items'] == []


def test_shared_total_comparison_budget_limits_many_changed_endpoints():
    left = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'object', 'properties': {'x': {'type': 'string'}}}}}}}}
    right = copy.deepcopy(left); right['responses']['200']['content']['application/json']['schema']['properties'] = {}
    previous, candidate = [], []
    for number in range(1050):
        base = dict(stable_key=str(number).zfill(4), operation_id=f'op{number}', method='GET', path=f'/x/{number}', summary='', tags=[])
        previous.append(SimpleNamespace(**base, operation=left)); candidate.append(SimpleNamespace(**base, operation=right))
    changes = _compute_diff(previous, candidate)
    assert len(changes) == 1050
    assert sum(len(change.response_contract_diff['items']) for change in changes) == 1000
    assert changes[-1].response_contract_diff == {'complete': False, 'items': []}


def test_items_under_non_array_are_incomplete_and_not_reported():
    left = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'string', 'items': {'type': 'object', 'required': ['x']}}}}}}}
    right = copy.deepcopy(left); right['responses']['200']['content']['application/json']['schema']['items']['required'] = []
    result = compare_response_contracts(left, right)
    assert result['complete'] is False
    assert result['items'] == []


def test_duplicate_or_empty_enum_values_mark_incomplete():
    for values in ([], [1, 1.0], ['x', 'x']):
        operation = {'responses': {'200': {'content': {'application/json': {'schema': {'enum': values}}}}}}
        assert compare_response_contracts(operation, copy.deepcopy(operation))['complete'] is False
    operation = {'responses': {'200': {'content': {'application/json': {'schema': {'enum': [True, 1]}}}}}}
    assert compare_response_contracts(operation, copy.deepcopy(operation))['complete'] is True


def test_unhandled_media_encoding_is_not_silently_complete():
    op = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'string'}, 'encoding': {'foo': {'contentType': 'application/json'}}}}}}}
    assert compare_response_contracts(op, copy.deepcopy(op))['complete'] is False


def test_apifox_ignore_properties_extension_is_not_silently_accepted():
    op = {'responses': {'200': {'content': {'application/json': {'schema': {'type': 'object', 'x-apifox-ignore-properties': ['secret']}}}}}}
    assert compare_response_contracts(op, copy.deepcopy(op))['complete'] is False


def test_explicit_null_type_is_incomplete():
    op = {'responses': {'200': {'content': {'application/json': {'schema': {'type': None}}}}}}
    assert compare_response_contracts(op, copy.deepcopy(op))['complete'] is False
