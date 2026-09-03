"""One-user functional and per-Agent connectivity checks before load starts."""

import copy
from dataclasses import asdict, dataclass, is_dataclass
import math
import time
from typing import Mapping, Tuple

from ..contracts.load_testing import parse_load_scenario_definition


@dataclass(frozen=True)
class PreflightResult:
    passed: bool
    failure_code: str
    message: str
    iteration_count: int
    observed_duration_ms: int
    cleanup_status: str
    steps: Tuple[dict, ...]
    connectivity: Tuple[dict, ...]

    def estimated_vus_for_rate(self, iterations_per_second):
        """Estimate concurrency from one full iteration, rounded up."""
        if not isinstance(iterations_per_second, (int, float)) or iterations_per_second <= 0:
            return 0
        return max(1, math.ceil(float(iterations_per_second) * self.observed_duration_ms / 1000))

    def to_dict(self):
        safe_steps = []
        for step in self.steps:
            safe = copy.deepcopy(step)
            extracted = safe.pop("extracted_variables", {})
            safe["extracted_variable_names"] = sorted(extracted) if isinstance(extracted, dict) else []
            safe_steps.append(safe)
        return {
            "passed": self.passed,
            "failure_code": self.failure_code,
            "message": self.message,
            "iteration_count": self.iteration_count,
            "observed_duration_ms": self.observed_duration_ms,
            "cleanup_status": self.cleanup_status,
            "steps": safe_steps,
            "connectivity": copy.deepcopy(list(self.connectivity)),
        }


class FunctionalLoadStepRunner:
    """Small adapter over the existing bounded HTTP request primitive."""

    def __init__(self, http_executor):
        self.http_executor = http_executor

    def execute(self, step, environment_revision_id, variables):
        outcome = self.http_executor._execute_http_step(  # noqa: SLF001
            step["request"],
            step.get("assertions", ()),
            step.get("extractions", ()),
            environment_revision_id,
            variables,
            lambda _phase: False,
            None,
            [],
            record_main_phases=False,
        )
        assertions = [
            asdict(item) if is_dataclass(item) else copy.deepcopy(item)
            for item in outcome.assertions
        ]
        from ..executor import redact

        return {
            "status": outcome.status,
            "duration_ms": int((outcome.response or {}).get("duration_ms") or 0),
            "failure_category": outcome.failure_category,
            "error_message": redact(outcome.error, outcome.secrets),
            "extracted_variables": copy.deepcopy(outcome.extracted),
            "assertions": assertions,
        }


class LoadPreflightService:
    def __init__(self, step_runner, *, connectivity_probe, sleeper=None):
        if connectivity_probe is None:
            raise ValueError("必须配置压测节点目标连通性检查")
        self.step_runner = step_runner
        self.connectivity_probe = connectivity_probe
        self.sleeper = sleeper or time.sleep

    def run_once(self, definition, environment_revision_id, agents):
        definition = parse_load_scenario_definition(definition)
        connectivity = self._probe_agents(agents, environment_revision_id)
        unreachable = next((item for item in connectivity if not item["reachable"]), None)
        if unreachable:
            return PreflightResult(
                passed=False,
                failure_code="agent_target_unreachable",
                message=(
                    f"压测节点“{unreachable['agent_name']}”无法访问目标环境："
                    f"{unreachable.get('message') or '请检查 DNS、网络、防火墙或 TLS 证书'}"
                ),
                iteration_count=0,
                observed_duration_ms=0,
                cleanup_status="not_needed",
                steps=(),
                connectivity=connectivity,
            )

        variables = {}
        results = []
        body_failed = False
        cleanup_failed = False
        cleanup_attempted = False
        observed_duration_ms = 0
        body_scopes = {"setup_once", "agent_setup", "vu_once", "iteration"}

        for step in definition["steps"]:
            if step["scope"] == "cleanup_once":
                continue
            if step["scope"] not in body_scopes or body_failed:
                continue
            outcome = self._execute(step, environment_revision_id, variables)
            results.append(outcome)
            observed_duration_ms += outcome["duration_ms"]
            variables.update(copy.deepcopy(outcome["extracted_variables"]))
            if outcome["status"] == "PASSED":
                self._sleep(step)
                observed_duration_ms += int(step.get("sleep_ms") or 0)
            else:
                body_failed = True

        # Cleanup is deliberately outside the main flow so assertions, timeouts,
        # and extraction failures cannot skip removal of resources from this run.
        for step in definition["steps"]:
            if step["scope"] != "cleanup_once":
                continue
            cleanup_attempted = True
            outcome = self._execute(step, environment_revision_id, variables)
            results.append(outcome)
            cleanup_failed = cleanup_failed or outcome["status"] != "PASSED"
            if outcome["status"] == "PASSED":
                variables.update(copy.deepcopy(outcome["extracted_variables"]))

        passed = not body_failed and not cleanup_failed
        failure_code = ""
        message = "预检通过：已完成一轮业务请求、断言和清理"
        if cleanup_failed:
            failure_code = "preflight_cleanup_failed"
            message = "预检清理失败，已阻止压测；请先确认本轮临时资源已清理"
        elif body_failed:
            failure_code = "functional_preflight_failed"
            first_failure = next(item for item in results if item["status"] != "PASSED")
            message = first_failure.get("error_message") or "单用户业务预检失败"
        return PreflightResult(
            passed=passed,
            failure_code=failure_code,
            message=message,
            iteration_count=1,
            observed_duration_ms=observed_duration_ms,
            cleanup_status=("failed" if cleanup_failed else "passed" if cleanup_attempted else "not_needed"),
            steps=tuple(results),
            connectivity=connectivity,
        )

    def _execute(self, step, environment_revision_id, variables):
        try:
            raw = self.step_runner.execute(step, environment_revision_id, copy.deepcopy(variables))
        except Exception as error:  # preflight must return evidence and still reach cleanup
            raw = {
                "status": "BROKEN",
                "duration_ms": 0,
                "failure_category": "environment",
                "error_message": str(error),
                "extracted_variables": {},
                "assertions": [],
            }
        raw = raw if isinstance(raw, Mapping) else {}
        return {
            "step_id": step["id"],
            "step_name": step["name"],
            "scope": step["scope"],
            "status": str(raw.get("status") or "BROKEN").upper(),
            "duration_ms": max(0, int(raw.get("duration_ms") or 0)),
            "failure_category": str(raw.get("failure_category") or ""),
            "error_message": str(raw.get("error_message") or ""),
            "extracted_variables": copy.deepcopy(raw.get("extracted_variables") or {}),
            "assertions": copy.deepcopy(raw.get("assertions") or []),
        }

    def _probe_agents(self, agents, environment_revision_id):
        results = []
        for agent in agents:
            try:
                raw = self.connectivity_probe(agent, environment_revision_id)
                raw = raw if isinstance(raw, Mapping) else {}
                reachable = raw.get("reachable") is True
                message = str(raw.get("message") or "")
            except Exception as error:
                raw = {}
                reachable = False
                message = str(error)
            results.append(
                {
                    "agent_id": str(agent.id),
                    "agent_name": str(agent.name),
                    "reachable": reachable,
                    "stage": str(raw.get("stage") or ("complete" if reachable else "unknown")),
                    "dns_ms": raw.get("dns_ms"),
                    "connect_ms": raw.get("connect_ms"),
                    "tls_ms": raw.get("tls_ms"),
                    "message": message,
                }
            )
        return tuple(results)

    def _sleep(self, step):
        milliseconds = int(step.get("sleep_ms") or 0)
        if milliseconds > 0:
            self.sleeper(milliseconds / 1000)
