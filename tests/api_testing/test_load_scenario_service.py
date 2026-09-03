"""Admission tests for safe, immutable load-test scenarios."""

import copy

import pytest

from task_server.api_testing.contracts.load_testing import (
    LoadScenarioPayloadError,
    load_testing_option_catalog,
    parse_load_scenario_definition,
)
from task_server.api_testing.services.load_scenario_service import LoadScenarioService


def _request(method="GET", path="/print3d/api/v1/models/search"):
    return {
        "method": method,
        "path": path,
        "service": "default",
        "path_params": {},
        "query": {},
        "headers": {"Authorization": {"$env": "ACCESS_TOKEN"}},
        "cookies": {},
        "body": None,
    }


def _step(
    step_id="search",
    *,
    method="GET",
    path="/print3d/api/v1/models/search",
    scope="iteration",
    side_effect="readonly",
    cleanup_step_id=None,
):
    value = {
        "id": step_id,
        "name": "搜索模型",
        "scope": scope,
        "action": "http_request",
        "request": _request(method, path),
        "assertions": [
            {"type": "status_code", "operator": "equals", "expected": 200},
            {"type": "json_path", "path": "$.code", "operator": "equals", "expected": 0},
        ],
        "extractions": [],
        "sleep_ms": 0,
        "side_effect": side_effect,
    }
    if cleanup_step_id:
        value["cleanup_step_id"] = cleanup_step_id
    return value


def _definition(steps=None, mode="single_interface"):
    return {
        "name": "模型搜索容量测试",
        "description": "验证搜索接口的稳定吞吐。",
        "mode": mode,
        "steps": steps or [_step()],
        "dataset_contract": {
            "dataset_id": None,
            "usage_mode": "cycle",
            "variables": [],
        },
        "risk": {
            "level": "low",
            "ownership_variable": None,
            "notes": "只读查询",
        },
        "source_snapshot": {
            "type": "case_version",
            "version_ids": ["case-v7"],
            "items": [{"id": "case-v7", "name": "搜索模型", "version": 7}],
        },
    }


def test_contract_rejects_unknown_fields_and_unknown_k6_action():
    payload = _definition()
    payload["javascript"] = "while (true) {}"
    with pytest.raises(LoadScenarioPayloadError, match="不支持字段.*javascript"):
        parse_load_scenario_definition(payload)

    payload = _definition()
    payload["steps"][0]["action"] = "eval_javascript"
    with pytest.raises(LoadScenarioPayloadError, match="只允许 HTTP 请求"):
        parse_load_scenario_definition(payload)


def test_contract_supports_only_named_scopes_and_bounded_sleep():
    payload = _definition()
    payload["steps"][0]["scope"] = "global"
    with pytest.raises(LoadScenarioPayloadError, match="步骤作用域"):
        parse_load_scenario_definition(payload)

    payload = _definition()
    payload["steps"][0]["sleep_ms"] = 60_001
    with pytest.raises(LoadScenarioPayloadError, match="等待时间"):
        parse_load_scenario_definition(payload)


def test_contract_rejects_literal_credentials_in_any_request_field():
    payload = _definition()
    payload["steps"][0]["request"]["headers"]["Authorization"] = "Bearer secret-value"

    with pytest.raises(LoadScenarioPayloadError, match="必须改用 \\$env"):
        parse_load_scenario_definition(payload)


def test_contract_rejects_future_or_undeclared_variable_references():
    payload = _definition()
    payload["steps"][0]["request"]["query"] = {"keyword": {"$data": "keyword"}}
    with pytest.raises(LoadScenarioPayloadError, match="数据变量 keyword 没有在数据契约中声明"):
        parse_load_scenario_definition(payload)

    payload = _definition()
    payload["steps"][0]["request"]["path"] = "/models/{{future_id}}"
    with pytest.raises(LoadScenarioPayloadError, match="变量 future_id 在使用前尚未提取"):
        parse_load_scenario_definition(payload)


def test_contract_rejects_assertions_that_k6_cannot_evaluate_meaningfully():
    payload = _definition()
    payload["steps"][0]["assertions"] = [
        {"type": "status_code", "operator": "contains", "expected": 200}
    ]

    with pytest.raises(LoadScenarioPayloadError, match="status_code 不支持 contains"):
        parse_load_scenario_definition(payload)


@pytest.mark.parametrize(
    ("path", "tags", "expected_code", "expected_message"),
    [
        ("/printJob/create", ["真实打印"], "hardware_action_blocked", "真实打印不能进入压测场景"),
        ("/device/command", ["设备控制"], "device_control_blocked", "设备控制不能进入压测场景"),
        ("/order/pay", ["支付"], "payment_blocked", "支付接口不能进入压测场景"),
        ("/sms/send", ["短信验证码"], "sms_blocked", "短信接口不能进入压测场景"),
        ("/ai/image/generate", ["高成本AI"], "high_cost_ai_blocked", "高成本 AI 接口不能进入压测场景"),
    ],
)
def test_case_copy_blocks_protected_business_actions(path, tags, expected_code, expected_message):
    case = {
        "id": "case-v1",
        "name": "受保护动作",
        "version": 1,
        "request": _request("POST", path),
        "assertions": [],
        "extractions": [],
        "processing": {"setup_steps": [], "cleanup_steps": []},
        "tags": tags,
    }

    result = LoadScenarioService.copy_from_case_versions(
        name="受保护动作压测",
        description="必须阻断",
        cases=[case],
    )

    assert result.accepted is False
    assert result.issues[0].code == expected_code
    assert result.issues[0].message == expected_message
    assert result.issues[0].remedy


def test_case_copy_keeps_tag_based_protection_when_source_id_needs_normalizing():
    case = {
        "id": "123456",
        "name": "动作接口",
        "version": 1,
        "request": _request("POST", "/actions/start"),
        "assertions": [],
        "extractions": [],
        "processing": {"setup_steps": [], "cleanup_steps": []},
        "tags": ["真实打印"],
    }

    result = LoadScenarioService.copy_from_case_versions(
        name="硬件动作",
        description="应当阻断",
        cases=[case],
    )

    assert result.issues[0].code == "hardware_action_blocked"


def test_write_action_requires_owned_resource_and_cleanup():
    create = _step(
        "create-model",
        method="POST",
        path="/models/create",
        side_effect="creates_owned_resource",
    )
    result = LoadScenarioService.validate_definition(_definition([create]))

    assert result.accepted is False
    assert {issue.code for issue in result.issues} == {"cleanup_required", "ownership_required"}
    assert all(issue.message and issue.remedy for issue in result.issues)


def test_owned_create_and_cleanup_chain_is_admitted_and_ordered():
    create = _step(
        "create-model",
        method="POST",
        path="/models/create",
        side_effect="creates_owned_resource",
        cleanup_step_id="delete-model",
    )
    create["extractions"] = [
        {"target": "created_model_id", "type": "json_path", "path": "$.data.id", "required": True}
    ]
    cleanup = _step(
        "delete-model",
        method="DELETE",
        path="/models/{{created_model_id}}",
        scope="cleanup_once",
        side_effect="cleanup_owned_resource",
    )
    payload = _definition([create, cleanup], mode="workflow")
    payload["risk"] = {
        "level": "medium",
        "ownership_variable": "created_model_id",
        "notes": "仅删除本轮创建的模型",
    }

    result = LoadScenarioService.validate_definition(payload)

    assert result.accepted is True
    assert result.definition["steps"][0]["id"] == "create-model"
    assert result.definition["steps"][1]["scope"] == "cleanup_once"


def test_cleanup_must_reference_the_id_extracted_by_the_write_step():
    create = _step(
        "create-model",
        method="POST",
        path="/models/create",
        side_effect="creates_owned_resource",
        cleanup_step_id="delete-model",
    )
    create["extractions"] = [
        {"target": "created_model_id", "type": "json_path", "path": "$.data.id", "required": True}
    ]
    cleanup = _step(
        "delete-model",
        method="DELETE",
        path="/models/static-id",
        scope="cleanup_once",
        side_effect="cleanup_owned_resource",
    )
    payload = _definition([create, cleanup], mode="workflow")
    payload["risk"] = {
        "level": "medium",
        "ownership_variable": "created_model_id",
        "notes": "只能删除本轮数据",
    }

    result = LoadScenarioService.validate_definition(payload)

    assert result.accepted is False
    assert [issue.code for issue in result.issues] == ["cleanup_ownership_mismatch"]
    assert "归属变量" in result.issues[0].message


def test_case_copy_keeps_a_deep_immutable_source_snapshot():
    case = {
        "id": "case-v7",
        "name": "中文搜索",
        "version": 7,
        "request": _request(),
        "assertions": [{"type": "status_code", "operator": "equals", "expected": 200}],
        "extractions": [],
        "processing": {"setup_steps": [], "cleanup_steps": []},
        "tags": ["只读"],
    }
    result = LoadScenarioService.copy_from_case_versions(
        name="搜索链路",
        description="复制已有功能用例",
        cases=[case],
    )
    case["request"]["path"] = "/changed"

    assert result.accepted is True
    assert result.definition["source_snapshot"]["items"][0]["request"]["path"] == "/print3d/api/v1/models/search"
    assert result.definition["steps"][0]["request"]["path"] == "/print3d/api/v1/models/search"


def test_all_load_options_have_chinese_name_usage_and_risk_help():
    catalog = load_testing_option_catalog()

    assert {item["value"] for item in catalog["executors"]} == {
        "constant-vus",
        "ramping-vus",
        "constant-arrival-rate",
        "ramping-arrival-rate",
    }
    for section in (
        "executors",
        "scenario_modes",
        "dataset_modes",
        "queue_priorities",
        "agent_tiers",
        "calibration_states",
        "capacity_fields",
        "thresholds",
    ):
        for item in catalog[section]:
            assert any("\u4e00" <= character <= "\u9fff" for character in item["name"])
            assert item["description"]
            assert item["risk_tip"]
