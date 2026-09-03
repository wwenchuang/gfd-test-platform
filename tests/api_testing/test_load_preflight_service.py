"""Single-iteration safety checks before distributed load begins."""

from types import SimpleNamespace

from task_server.api_testing.services.load_preflight_service import LoadPreflightService


def _definition():
    def step(identifier, scope):
        return {
            "id": identifier,
            "name": identifier,
            "scope": scope,
            "action": "http_request",
            "request": {
                "method": "GET",
                "path": "/" + identifier,
                "service": "default",
                "path_params": {},
                "query": {},
                "headers": {},
                "cookies": {},
                "body": None,
            },
            "assertions": [],
            "extractions": [],
            "sleep_ms": 0,
            "side_effect": "cleanup_owned_resource" if scope == "cleanup_once" else "readonly",
        }

    return {
        "name": "搜索核心链路",
        "description": "预检只执行一轮",
        "mode": "workflow",
        "steps": [
            step("setup", "setup_once"),
            step("agent-setup", "agent_setup"),
            step("vu-setup", "vu_once"),
            step("main", "iteration"),
            step("cleanup", "cleanup_once"),
        ],
        "dataset_contract": {"dataset_id": None, "usage_mode": "cycle", "variables": []},
        "risk": {"level": "low", "ownership_variable": None, "notes": ""},
        "source_snapshot": {"type": "manual", "version_ids": [], "items": []},
    }


class _StepRunner:
    def __init__(self, failed_step=None):
        self.calls = []
        self.variables = []
        self.failed_step = failed_step

    def execute(self, step, environment_revision_id, variables):
        self.calls.append(step["id"])
        self.variables.append(dict(variables))
        status = "FAILED" if step["id"] == self.failed_step else "PASSED"
        return {
            "status": status,
            "duration_ms": 25,
            "failure_category": "product_assertion" if status == "FAILED" else "",
            "error_message": "业务断言失败" if status == "FAILED" else "",
            "extracted_variables": {step["id"] + "_id": "owned-1"},
            "assertions": [],
        }


def _agents():
    return [
        SimpleNamespace(id="agent-a", name="上海专用节点"),
        SimpleNamespace(id="agent-b", name="北京专用节点"),
    ]


def test_preflight_executes_every_scope_once_and_checks_each_agent_connectivity():
    runner = _StepRunner()
    probes = []

    def probe(agent, revision_id):
        probes.append((agent.id, revision_id))
        return {"reachable": True, "dns_ms": 2, "connect_ms": 8, "tls_ms": 12}

    result = LoadPreflightService(runner, connectivity_probe=probe).run_once(
        _definition(), "environment-v3", _agents()
    )

    assert result.passed is True
    assert runner.calls == ["setup", "agent-setup", "vu-setup", "main", "cleanup"]
    assert probes == [("agent-a", "environment-v3"), ("agent-b", "environment-v3")]
    assert result.iteration_count == 1
    assert result.observed_duration_ms == 100
    assert result.cleanup_status == "passed"
    assert result.estimated_vus_for_rate(40) == 4
    assert result.to_dict()["steps"][0]["extracted_variable_names"] == ["setup_id"]
    assert "extracted_variables" not in result.to_dict()["steps"][0]


def test_preflight_always_attempts_cleanup_after_main_failure():
    runner = _StepRunner(failed_step="main")

    result = LoadPreflightService(
        runner,
        connectivity_probe=lambda _agent, _revision: {"reachable": True},
    ).run_once(_definition(), "environment-v3", _agents()[:1])

    assert result.passed is False
    assert result.failure_code == "functional_preflight_failed"
    assert runner.calls[-1] == "cleanup"
    assert runner.variables[-1]["main_id"] == "owned-1"
    assert result.cleanup_status == "passed"


def test_unreachable_target_from_any_selected_agent_is_a_hard_block():
    runner = _StepRunner()

    def probe(agent, _revision):
        if agent.id == "agent-b":
            return {"reachable": False, "stage": "tls", "message": "证书校验失败"}
        return {"reachable": True, "connect_ms": 4}

    result = LoadPreflightService(runner, connectivity_probe=probe).run_once(
        _definition(), "environment-v3", _agents()
    )

    assert result.passed is False
    assert result.failure_code == "agent_target_unreachable"
    assert result.connectivity[1]["agent_name"] == "北京专用节点"
    assert result.connectivity[1]["message"] == "证书校验失败"
