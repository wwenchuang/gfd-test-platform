"""Deterministic k6 compiler tests."""

import pytest

from task_server.api_testing.services.load_scenario_compiler import (
    LoadScenarioCompileError,
    compile_scenario,
)


SEARCH_CHAIN = {
    "name": "登录搜索详情链路",
    "description": "中文关键字也必须安全进入脚本。",
    "mode": "workflow",
    "steps": [
        {
            "id": "login",
            "name": "登录",
            "scope": "vu_once",
            "action": "http_request",
            "request": {
                "method": "POST",
                "path": "/login",
                "service": "default",
                "path_params": {},
                "query": {},
                "headers": {"Content-Type": "application/json"},
                "cookies": {},
                "body": {"username": {"$data": "username"}, "password": {"$env": "LOGIN_PASSWORD"}},
            },
            "assertions": [
                {"type": "status_code", "operator": "equals", "expected": 200},
                {"type": "json_path", "path": "$.code", "operator": "equals", "expected": 0},
            ],
            "extractions": [
                {"target": "access_token", "type": "json_path", "path": "$.data.token", "required": True}
            ],
            "sleep_ms": 0,
            "side_effect": "readonly",
        },
        {
            "id": "search",
            "name": "搜索收纳盒",
            "scope": "iteration",
            "action": "http_request",
            "request": {
                "method": "GET",
                "path": "/models/search",
                "service": "default",
                "path_params": {},
                "query": {"keyword": "收纳盒"},
                "headers": {"Authorization": {"$extract": "access_token"}},
                "cookies": {},
                "body": None,
            },
            "assertions": [
                {"type": "status_code", "operator": "equals", "expected": 200},
                {"type": "json_path", "path": "$.code", "operator": "equals", "expected": 0},
            ],
            "extractions": [
                {"target": "model_id", "type": "json_path", "path": "$.data.list.0.id", "required": True}
            ],
            "sleep_ms": 100,
            "side_effect": "readonly",
        },
        {
            "id": "detail",
            "name": "查看详情",
            "scope": "iteration",
            "action": "http_request",
            "request": {
                "method": "GET",
                "path": "/models/{{model_id}}",
                "service": "default",
                "path_params": {},
                "query": {},
                "headers": {"Authorization": {"$extract": "access_token"}},
                "cookies": {},
                "body": None,
            },
            "assertions": [{"type": "status_code", "operator": "equals", "expected": 200}],
            "extractions": [],
            "sleep_ms": 0,
            "side_effect": "readonly",
        },
    ],
    "dataset_contract": {
        "dataset_id": "dataset-users",
        "usage_mode": "fixed_per_vu",
        "variables": ["username"],
    },
    "risk": {"level": "low", "ownership_variable": None, "notes": "只读链路"},
    "source_snapshot": {"type": "case_version", "version_ids": ["v1", "v2"], "items": []},
}

FIXED_RATE = {
    "executor": "constant-arrival-rate",
    "rate": 20,
    "time_unit": "1s",
    "duration_seconds": 60,
    "pre_allocated_vus": 10,
    "max_vus": 40,
}


def test_compiler_separates_transport_business_and_iteration_checks():
    compiled = compile_scenario(SEARCH_CHAIN, FIXED_RATE)

    assert 'check(response, {"HTTP状态符合预期"' in compiled.script
    assert "业务断言：$.code" in compiled.script
    assert "workflow_iteration_success" in compiled.script
    assert '"constant-arrival-rate"' in compiled.script
    assert "搜索收纳盒" in compiled.script
    assert compiled.options["scenarios"]["main"]["rate"] == 20


def test_compiler_keeps_secrets_as_environment_references():
    compiled = compile_scenario(SEARCH_CHAIN, FIXED_RATE)

    assert '__ENV["LOGIN_PASSWORD"]' in compiled.script
    assert "secret-value" not in compiled.script
    assert "password\":\"" not in compiled.script
    assert compiled.required_environment_variables == ("LOGIN_PASSWORD",)


def test_compiler_is_deterministic_for_equivalent_mapping_order():
    reordered = {key: SEARCH_CHAIN[key] for key in reversed(tuple(SEARCH_CHAIN))}

    first = compile_scenario(SEARCH_CHAIN, FIXED_RATE)
    second = compile_scenario(reordered, dict(reversed(tuple(FIXED_RATE.items()))))

    assert first.content_hash == second.content_hash
    assert first.script == second.script
    assert first.step_manifest == second.step_manifest


@pytest.mark.parametrize(
    ("workload", "executor"),
    [
        ({"executor": "constant-vus", "vus": 5, "duration_seconds": 30}, "constant-vus"),
        (
            {
                "executor": "ramping-vus",
                "start_vus": 0,
                "stages": [{"duration_seconds": 30, "target": 10}, {"duration_seconds": 30, "target": 0}],
            },
            "ramping-vus",
        ),
        (FIXED_RATE, "constant-arrival-rate"),
        (
            {
                "executor": "ramping-arrival-rate",
                "start_rate": 5,
                "time_unit": "1s",
                "pre_allocated_vus": 10,
                "max_vus": 50,
                "stages": [{"duration_seconds": 30, "target": 20}, {"duration_seconds": 30, "target": 5}],
            },
            "ramping-arrival-rate",
        ),
    ],
)
def test_compiler_supports_all_four_load_models(workload, executor):
    compiled = compile_scenario(SEARCH_CHAIN, workload)
    assert compiled.options["scenarios"]["main"]["executor"] == executor
    assert f'"{executor}"' in compiled.script


def test_compiler_rejects_unknown_or_unbounded_workload_fields():
    with pytest.raises(LoadScenarioCompileError, match="不支持的负载模型"):
        compile_scenario(SEARCH_CHAIN, {"executor": "shared-iterations"})

    with pytest.raises(LoadScenarioCompileError, match="不支持字段.*javascript"):
        compile_scenario(SEARCH_CHAIN, {**FIXED_RATE, "javascript": "eval('x')"})


def test_compiler_adds_stable_request_name_tags_and_csv_data_access():
    compiled = compile_scenario(SEARCH_CHAIN, FIXED_RATE)

    assert 'name: "login 登录"' in compiled.script
    assert 'name: "search 搜索收纳盒"' in compiled.script
    assert "datasetRows" in compiled.script
    assert 'open(__ENV["LOAD_DATASET_FILE"])' in compiled.script
    assert "LOAD_DATASET_JSON" not in compiled.script
    assert 'dataValue("username")' in compiled.script
    assert "exec.scenario.iterationInTest" in compiled.script
    assert "const datasetVariables" not in compiled.script


def test_compiler_avoids_javascript_syntax_rejected_by_bundled_k6():
    compiled = compile_scenario(SEARCH_CHAIN, FIXED_RATE)

    assert "??" not in compiled.script
    assert "?." not in compiled.script
    assert 'state[name] === undefined || state[name] === null' in compiled.script


def test_compiler_passes_the_persistent_vu_state_to_runtime_steps():
    compiled = compile_scenario(SEARCH_CHAIN, FIXED_RATE)
    default_body = compiled.script.split("export default function(data)", 1)[1]

    assert "step_login(vuState, data)" in default_body
    assert "step_search(vuState, data)" in default_body
    assert "step_detail(vuState, data)" in default_body
    assert "step_login(state, data)" not in default_body
    assert "step_search(state, data)" not in default_body
    assert "step_detail(state, data)" not in default_body


def test_control_setup_is_not_executed_by_agent_and_agent_setup_runs_once_per_shard():
    control_setup = dict(
        SEARCH_CHAIN["steps"][0],
        id="control-setup",
        name="控制服务全局初始化",
        scope="setup_once",
    )
    definition = {
        **SEARCH_CHAIN,
        "steps": [
            control_setup,
            dict(SEARCH_CHAIN["steps"][0], scope="agent_setup"),
            *SEARCH_CHAIN["steps"][1:],
        ],
    }

    compiled = compile_scenario(definition, FIXED_RATE)

    setup_body = compiled.script.split("export function setup()", 1)[1].split(
        "export default function", 1
    )[0]
    assert "step_login(state, data)" in setup_body
    assert "step_control_setup" not in compiled.script
    assert "RUN_SETUP_ONCE" not in compiled.script
    assert 'LOAD_SETUP_JSON' in setup_body
    assert "finally" in compiled.script
