import pytest
from types import SimpleNamespace

from task_server.api_testing.assertions import JsonPathError, json_path_get
from task_server.api_testing.executor import HttpExecutor


def test_owned_record_can_be_selected_after_another_record_is_inserted():
    payload = {"data": {"records": [{"sn": "someone-else", "id": 12}, {"sn": "my-task", "id": 34}]}}
    assert json_path_get(payload, '$.data.records[?(@.sn=="my-task")].id') == 34


@pytest.mark.parametrize("records", [[], [{"sn": "someone-else", "id": 12}], [{"sn": "my-task", "id": 1}, {"sn": "my-task", "id": 2}]])
def test_missing_or_ambiguous_record_cannot_be_selected(records):
    with pytest.raises(JsonPathError):
        json_path_get({"records": records}, '$.records[?(@.sn=="my-task")].id')


def test_selector_rejects_untrusted_expression_syntax():
    with pytest.raises(JsonPathError):
        json_path_get({"records": []}, '$.records[?(@.sn=="x" || true)].id')


def test_runtime_renders_selector_for_assertion_and_extraction():
    path = '$.records[?(@.sn=="{{task_id}}")].id'
    runtime = SimpleNamespace(render=lambda value: value.replace("{{task_id}}", "my-task") if isinstance(value, str) else value)
    assertion = SimpleNamespace(type="json_path", operator="exists", expected=None, path=path)
    extraction = SimpleNamespace(type="json_path", target="record_id", path=path, required=True)
    rendered_assertion = HttpExecutor._render_assertion_expected(assertion, runtime)
    rendered_extraction = HttpExecutor._render_extraction_path(extraction, runtime)
    assert rendered_assertion.path == '$.records[?(@.sn=="my-task")].id'
    assert rendered_extraction.path == rendered_assertion.path
