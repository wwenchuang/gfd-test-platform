from types import SimpleNamespace

import pytest

from task_server.api_testing.assertions import AssertionDefinitionError, evaluate_assertions


def evaluate(body, schema):
    assertion = SimpleNamespace(type="schema", operator="equals", expected=schema, enabled=True)
    return evaluate_assertions([assertion], SimpleNamespace(json_body=body))[0].passed


@pytest.mark.parametrize("schema,valid,invalid", [
    ({"const": True}, True, 1),
    ({"enum": ["ready", "standingBy"]}, "ready", "upgrading"),
    ({"minItems": 1}, [1], []),
    ({"maxItems": 1}, [1], [1, 2]),
    ({"minProperties": 1}, {"id": 1}, {}),
    ({"maxProperties": 1}, {"id": 1}, {"id": 1, "other": 2}),
    ({"allOf": [{"type": "integer"}, {"minimum": 1}]}, 1, 0),
    ({"anyOf": [{"const": "ready"}, {"const": "idle"}]}, "ready", "printing"),
    ({"oneOf": [{"type": "integer"}, {"const": 1}]}, 2, 1),
    ({"not": {"const": "upgrading"}}, "ready", "upgrading"),
    ({"type": "object", "required": ["data"], "properties": {"data": {"type": ["string", "null"]}}}, {"data": None}, {"data": 1}),
    ({"minLength": 1}, "abc", ""),
    ({"type": "object", "properties": {"id": {"type": "integer"}}, "additionalProperties": False}, {"id": 1}, {"id": 1, "other": 2}),
    ({"type": "array", "items": {"type": "object", "required": ["status"], "properties": {"status": {"const": "success"}}}}, [{"status": "success"}], [{"status": "failed"}]),
    ({"required": ["id"]}, {"id": "1"}, "not an object"),
    ({"properties": {"id": {"type": "string"}}}, {"id": "1"}, "not an object"),
    ({"properties": {"data": {"required": ["id"]}}}, {"data": {"id": 1}}, {"data": "not an object"}),
    ({"type": "array", "items": {"required": ["id"]}}, [{"id": 1}], ["not an object"]),
    ({"type": "object", "properties": {"data": {"type": ["object", "null"], "properties": {"id": {"type": "string"}}}}}, {"data": None}, {"data": {"id": 1}}),
])
def test_response_schema_enforces_value_and_collection_constraints(schema, valid, invalid):
    assert evaluate(valid, schema) is True
    assert evaluate(invalid, schema) is False


@pytest.mark.parametrize("schema", [
    {"$ref": "http://127.0.0.1/private-schema"},
    {"properties": {"data": {"$ref": "#/$defs/cycle"}}},
    {"minItem": 1},
    {"minItems": "one"},
    {"required": "id"},
    {"items": {"format": "email"}},
    {"properties": {"data": {"type": "string", "pattern": "^(a+)+$"}}},
])
def test_response_schema_rejects_invalid_or_unsupported_rules_instead_of_passing(schema):
    with pytest.raises(AssertionDefinitionError):
        evaluate({}, schema)


def test_response_schema_still_bounds_nesting():
    schema = {"type": "string"}
    for _ in range(22):
        schema = {"not": schema}
    with pytest.raises(AssertionDefinitionError, match="层级"):
        evaluate("value", schema)


def test_response_schema_treats_const_objects_as_data_not_schema_keywords():
    assert evaluate({"$ref": "a-data-value"}, {"const": {"$ref": "a-data-value"}})
