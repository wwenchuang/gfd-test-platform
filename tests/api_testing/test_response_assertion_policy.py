from task_server.api_testing.services.response_assertion_policy import (
    ResponseAssertionPolicy,
)


def _json_operation(*, example=None, schema=None, status=200):
    media = {}
    if example is not None:
        media["example"] = example
    if schema is not None:
        media["schema"] = schema
    return {
        "responses": {
            str(status): {
                "content": {
                    "application/json": media,
                }
            }
        }
    }


def _assertion(assertion_type, operator, *, expected=None, path=None, enabled=True):
    item = {
        "type": assertion_type,
        "operator": operator,
        "timeout_ms": 0,
        "enabled": enabled,
    }
    if expected is not None:
        item["expected"] = expected
    if path is not None:
        item["path"] = path
    return item


def test_default_positive_assertions_include_contract_business_envelope():
    operation = _json_operation(example={"code": 0, "data": []})

    assertions = ResponseAssertionPolicy.default_positive_assertions(operation)

    assert assertions == [
        _assertion("status_code", "equals", expected=200),
        _assertion("json_path", "equals", expected=0, path="$.code"),
        _assertion("json_path", "exists", path="$.data"),
    ]


def test_ai_status_only_candidate_receives_business_assertion_defaults():
    operation = _json_operation(
        schema={
            "type": "object",
            "properties": {
                "code": {"type": "integer"},
                "data": {"type": "object"},
            },
        }
    )
    source = [_assertion("status_code", "equals", expected=200)]

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(source, operation)

    assert source == [_assertion("status_code", "equals", expected=200)]
    assert assertions == [
        _assertion("status_code", "equals", expected=200),
        _assertion("json_path", "equals", expected=0, path="$.code"),
        _assertion("json_path", "exists", path="$.data"),
    ]


def test_documented_code_200_is_used_instead_of_platform_default():
    operation = _json_operation(
        example={"code": 200, "message": "success", "data": {"id": "1"}}
    )

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(
        [_assertion("status_code", "equals", expected=200)],
        operation,
    )

    assert _assertion("json_path", "equals", expected=200, path="$.code") in assertions
    assert _assertion("json_path", "equals", expected=0, path="$.code") not in assertions


def test_specific_ai_data_assertion_suppresses_generic_data_exists_only():
    operation = _json_operation(example={"code": 0, "data": {"id": "1"}})
    specific = _assertion("json_path", "equals", expected="1", path="$.data.id")

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(
        [_assertion("status_code", "equals", expected=200), specific],
        operation,
    )

    assert assertions.count(specific) == 1
    assert _assertion("json_path", "exists", path="$.data") not in assertions
    assert _assertion("json_path", "equals", expected=0, path="$.code") in assertions


def test_descendant_not_exists_does_not_suppress_parent_data_exists():
    operation = _json_operation(example={"code": 0, "data": {}})
    descendant_absence = _assertion(
        "json_path",
        "not_exists",
        path="$.data.optionalField",
    )

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(
        [_assertion("status_code", "equals", expected=200), descendant_absence],
        operation,
    )

    assert descendant_absence in assertions
    assert _assertion("json_path", "exists", path="$.data") in assertions


def test_explicit_data_absence_is_preserved_as_negative_scenario():
    operation = _json_operation(example={"code": 0, "data": {}})
    source = [
        _assertion("status_code", "equals", expected=200),
        _assertion("json_path", "not_exists", path="$.data"),
    ]

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(source, operation)

    assert assertions == source


def test_weak_business_code_set_receives_exact_success_assertion():
    operation = _json_operation(example={"code": 0, "data": []})
    broad_code_assertion = _assertion(
        "json_path",
        "in",
        expected=[0, 1001],
        path="$.code",
    )

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(
        [_assertion("status_code", "equals", expected=200), broad_code_assertion],
        operation,
    )

    assert broad_code_assertion in assertions
    assert _assertion("json_path", "equals", expected=0, path="$.code") in assertions


def test_documented_alternate_success_status_uses_its_response_contract():
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {"example": {"code": 0, "data": {}}}
                }
            },
            "201": {
                "content": {
                    "application/json": {"example": {"code": 200, "data": {}}}
                }
            },
        }
    }

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(
        [_assertion("status_code", "equals", expected=201)],
        operation,
    )

    assert _assertion("status_code", "equals", expected=201) in assertions
    assert _assertion("status_code", "equals", expected=200) not in assertions
    assert _assertion("json_path", "equals", expected=200, path="$.code") in assertions
    assert _assertion("json_path", "exists", path="$.data") in assertions


def test_broad_status_set_receives_exact_documented_success_status():
    operation = _json_operation(example={"code": 0, "data": []})
    broad_status = _assertion("status_code", "in", expected=[200, 400])

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(
        [broad_status],
        operation,
    )

    assert broad_status in assertions
    assert _assertion("status_code", "equals", expected=200) in assertions
    assert _assertion("json_path", "equals", expected=0, path="$.code") in assertions


def test_explicit_negative_http_candidate_does_not_receive_success_defaults():
    operation = _json_operation(example={"code": 0, "data": []})
    source = [_assertion("status_code", "equals", expected=400)]

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(source, operation)

    assert assertions == source


def test_explicit_business_failure_candidate_does_not_receive_success_defaults():
    operation = _json_operation(example={"code": 0, "data": []})
    source = [
        _assertion("status_code", "equals", expected=200),
        _assertion("json_path", "equals", expected=1001, path="$.code"),
    ]

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(source, operation)

    assert assertions == source


def test_non_success_business_operators_do_not_receive_success_defaults():
    operation = _json_operation(example={"code": 0, "data": []})
    cases = [
        [
            _assertion("status_code", "equals", expected=200),
            _assertion("json_path", "greater_than", expected=0, path="$.code"),
        ],
        [
            _assertion("status_code", "equals", expected=200),
            _assertion("json_path", "equals", expected=False, path="$.success"),
        ],
    ]

    for source in cases:
        assertions = ResponseAssertionPolicy.complete_candidate_assertions(
            source,
            operation,
        )
        assert assertions == source


def test_cross_field_business_failure_is_preserved_for_success_true_contract():
    operation = _json_operation(example={"success": True, "data": {}})
    source = [
        _assertion("status_code", "equals", expected=200),
        _assertion("json_path", "equals", expected=1001, path="$.code"),
    ]

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(source, operation)

    assert assertions == source


def test_non_json_response_keeps_only_status_assertion():
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                }
            }
        }
    }

    assertions = ResponseAssertionPolicy.default_positive_assertions(operation)

    assert assertions == [_assertion("status_code", "equals", expected=200)]


def test_json_array_example_without_schema_does_not_receive_object_assertions():
    operation = _json_operation(example=[{"id": "1"}])

    assertions = ResponseAssertionPolicy.default_positive_assertions(operation)

    assert assertions == [_assertion("status_code", "equals", expected=200)]


def test_empty_json_contract_does_not_assume_platform_response_envelope():
    operation = _json_operation()

    assertions = ResponseAssertionPolicy.default_positive_assertions(operation)

    assert assertions == [_assertion("status_code", "equals", expected=200)]


def test_streaming_json_media_type_does_not_receive_document_assertions():
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/x-ndjson": {
                        "schema": {
                            "type": "object",
                            "properties": {"code": {"type": "integer"}},
                        },
                        "example": {"code": 0},
                    }
                }
            }
        }
    }

    assertions = ResponseAssertionPolicy.default_positive_assertions(operation)

    assert assertions == [_assertion("status_code", "equals", expected=200)]


def test_completion_returns_a_deep_copy_when_no_defaults_apply():
    operation = {"responses": {"204": {"description": "No Content"}}}
    source = [_assertion("status_code", "equals", expected=204)]

    assertions = ResponseAssertionPolicy.complete_candidate_assertions(source, operation)
    assertions[0]["expected"] = 200

    assert source == [_assertion("status_code", "equals", expected=204)]
