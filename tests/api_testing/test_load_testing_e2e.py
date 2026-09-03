"""Controlled-target integration evidence for the distributed load workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from sqlalchemy import select

from task_server.api_testing import access, load_agent_http
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision, ApiEnvironmentService
from task_server.api_testing.models.load_testing import ApiLoadDataset, ApiLoadRun, ApiLoadRunShard
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.repositories.load_testing_repository import LoadTestingRepository
from task_server.api_testing.services.load_agent_service import LoadAgentService
from task_server.api_testing.services.load_ai_analysis_service import LoadAiAnalysisService
from task_server.api_testing.services.load_metric_service import LoadMetricService
from task_server.api_testing.services.load_preflight_service import PreflightResult
from task_server.api_testing.services.load_report_service import LoadReportService
from task_server.api_testing.services.load_run_service import LoadRunService
from tests.api_testing.test_load_testing_repository import load_factory


NOW = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
BOUNDS = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
HARD_LIMITS = {
    "max_processes": 1,
    "max_vus": 1,
    "max_iterations_per_second": 500,
    "max_duration_seconds": 900,
    "cpu_cores": 2,
    "memory_mb": 2048,
}


class _ControlledTarget(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/fast"):
            time.sleep(0.02)
            self._json(200, {"code": 0, "data": {"ok": True}})
        elif self.path.startswith("/delayed"):
            time.sleep(0.2)
            self._json(200, {"code": 0, "data": {"ok": True}})
        elif self.path.startswith("/http-error"):
            self._json(503, {"code": 503, "message": "受控服务不可用"})
        elif self.path.startswith("/business-error"):
            self._json(200, {"code": 1001, "message": "受控业务失败"})
        else:
            self._json(404, {"code": 404})


@pytest.fixture(scope="module")
def controlled_target():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ControlledTarget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.fixture(autouse=True)
def unrestricted_test_access(monkeypatch):
    monkeypatch.setattr(access, "get_access_profile", lambda _actor: None)


class _Preflight:
    def run_once(self, _definition, _revision_id, agents):
        return PreflightResult(
            passed=True,
            failure_code="",
            message="受控目标预检通过",
            iteration_count=1,
            observed_duration_ms=25,
            cleanup_status="not_needed",
            steps=(),
            connectivity=tuple(
                {"agent_id": item.id, "agent_name": item.name, "reachable": True}
                for item in agents
            ),
        )


def _audit(actor="load-e2e"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


def _register_agent(service, name, tier):
    enrollment = service.create_enrollment(
        {"name": name, "scheduling_tier": tier, "expires_in_seconds": 900}, "admin"
    )
    registration = service.register(
        enrollment.token,
        {"agent_version": "0.1.0", "k6_version": "k6 v0.52.0", "hard_limits": HARD_LIMITS, "labels": {}},
    )
    calibration = {
        "state": "valid",
        "calibrated_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(days=7)).isoformat(),
        "agent_version": "0.1.0",
        "k6_version": "k6 v0.52.0",
        "hardware_signature": name,
        "max_vus": 1,
        "max_iterations_per_second": 500,
    }
    service.heartbeat(
        registration.secret,
        {
            "agent_version": "0.1.0",
            "k6_version": "k6 v0.52.0",
            "hard_limits": HARD_LIMITS,
            "current_usage": {"processes": 0, "vus": 0, "iterations_per_second": 0},
            "health": {"schedulable": True, "calibration": calibration},
            "egress_ip": "127.0.0.1",
        },
    )
    return registration


def _definition(dataset_id):
    return {
        "name": "受控目标核心链路",
        "description": "用于验证双节点执行交接与报告分类",
        "mode": "single_interface",
        "steps": [{
            "id": "fast",
            "name": "快速接口",
            "scope": "iteration",
            "action": "http_request",
            "request": {
                "method": "GET", "path": "/fast", "service": "default",
                "path_params": {}, "query": {"item": {"$data": "item_id"}},
                "headers": {}, "cookies": {}, "body": None,
            },
            "assertions": [
                {"type": "status_code", "operator": "equals", "expected": 200, "enabled": True},
                {"type": "json_path", "path": "$.code", "operator": "equals", "expected": 0, "enabled": True},
            ],
            "extractions": [],
            "sleep_ms": 0,
            "side_effect": "readonly",
        }],
        "dataset_contract": {
            "dataset_id": dataset_id,
            "usage_mode": "exclusive_per_iteration",
            "variables": ["item_id"],
        },
        "risk": {"level": "low", "ownership_variable": None, "notes": "受控目标"},
        "source_snapshot": {"type": "manual", "version_ids": [], "items": []},
    }


def _metric(batch, started_at, *, requests, http_failures=0, business_failures=0):
    return {
        "batch_id": batch,
        "buckets": [{
            "step_id": "fast",
            "started_at": started_at.isoformat(),
            "bucket_seconds": 5,
            "metrics": {
                "requests": requests,
                "iterations": requests,
                "dropped_iterations": 0,
                "http_failures": http_failures,
                "business_assertions": requests,
                "business_failures": business_failures,
                "workflow_iterations": requests,
                "workflow_failures": http_failures + business_failures,
                "latency_histogram": {
                    "bounds_ms": BOUNDS,
                    "counts": [0, requests - 1, 0, 0, 1, 0, 0, 0, 0, 0, 0],
                    "count": requests,
                    "sum_ms": requests * 30,
                    "max_ms": 210,
                },
            },
        }],
    }


def test_controlled_target_distinguishes_transport_business_and_latency(controlled_target):
    started = time.monotonic()
    with urlopen(controlled_target + "/fast", timeout=2) as response:
        assert json.load(response)["code"] == 0
    fast_seconds = time.monotonic() - started

    started = time.monotonic()
    with urlopen(controlled_target + "/delayed", timeout=2) as response:
        assert json.load(response)["code"] == 0
    delayed_seconds = time.monotonic() - started
    with pytest.raises(HTTPError) as failed:
        urlopen(controlled_target + "/http-error", timeout=2)
    with urlopen(controlled_target + "/business-error", timeout=2) as response:
        business = json.load(response)

    assert fast_seconds >= 0.015
    assert delayed_seconds >= 0.18
    assert failed.value.code == 503
    assert business == {"code": 1001, "message": "受控业务失败"}


def test_two_agent_handoff_metrics_report_and_ai_failure_isolation(
    load_factory, controlled_target, tmp_path, monkeypatch
):
    agent_service = LoadAgentService(load_factory, now=lambda: NOW)
    first = _register_agent(agent_service, "受控压测节点一", "preferred")
    second = _register_agent(agent_service, "受控压测节点二", "normal")
    rows = [{"item_id": f"item-{index:02d}"} for index in range(20)]
    dataset_bytes = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    dataset_path = tmp_path / "rows.json"
    dataset_path.write_bytes(dataset_bytes)

    with load_factory.begin() as session:
        project = ApiProject(name="受控压测项目", slug="load-e2e-" + first.agent.id[:8], **_audit())
        session.add(project)
        session.flush()
        environment = ApiEnvironment(project_id=project.id, name="受控性能环境", **_audit())
        session.add(environment)
        session.flush()
        revision = ApiEnvironmentRevision(
            environment_id=environment.id,
            revision_number=1,
            name="受控性能环境 v1",
            default_headers={"X-Controlled-Run": "yes"},
            **_audit(),
        )
        session.add(revision)
        session.flush()
        environment.active_revision_id = revision.id
        session.add(ApiEnvironmentService(
            revision_id=revision.id,
            service_name="default",
            module_name="受控服务",
            base_url=controlled_target,
            metadata_json={},
            **_audit(),
        ))
        dataset = ApiLoadDataset(
            project_id=project.id,
            name="双节点独占数据",
            filename="rows.json",
            field_schema={"fields": [{"name": "item_id", "types": ["string"]}]},
            row_count=len(rows),
            storage_ref=str(dataset_path),
            content_hash=sha256(dataset_bytes).hexdigest(),
            sensitivity="normal",
            usage_mode="exclusive_per_iteration",
            **_audit(),
        )
        session.add(dataset)
        session.flush()
        project_id, revision_id, dataset_id = project.id, revision.id, dataset.id

    repository = LoadTestingRepository.from_factory(load_factory)
    scenario = repository.create_scenario(project_id, "受控目标核心链路", "single_interface", "load-e2e")
    version = repository.create_scenario_version(scenario.id, _definition(dataset_id), "k6-safe-v1", "load-e2e")
    service = LoadRunService(load_factory, preflight_service=_Preflight(), now=lambda: NOW)
    run = service.create(
        {
            "scenario_version_id": version.id,
            "environment_revision_id": revision_id,
            "workload": {"executor": "constant-vus", "vus": 2, "duration_seconds": 10},
            "thresholds": {
                "http_error_rate": {"operator": "less_than", "value": 0.1, "required": True},
                "business_failure_rate": {"operator": "less_than", "value": 0.1, "required": True},
            },
            "priority": "high",
            "allocation_policy": {
                "allow_fallback": False,
                "allow_run_anyway": False,
                "agent_ids": [first.agent.id, second.agent.id],
            },
        },
        "load-e2e",
    )
    assert run.configuration["compiler"]["content_hash"]
    assert sum(item["allocation"]["vus"] for item in run.configuration["agents"]) == 2

    service.preflight(run.id, "load-e2e")
    service.start(run.id, "load-e2e")
    monkeypatch.setattr(load_agent_http, "_factory", lambda: load_factory)
    first_claim = load_agent_http._claim_shard(first.agent.id)
    second_claim = load_agent_http._claim_shard(second.agent.id)

    assert first_claim["script"] == second_claim["script"]
    assert '"vus":1' in first_claim["script"]
    assert first_claim["workload"] == {"executor": "constant-vus", "vus": 1, "duration_seconds": 10}
    assert second_claim["workload"] == first_claim["workload"]
    assert first_claim["environment"]["BASE_URL_DEFAULT"] == controlled_target
    assert first_claim["environment"]["BASE_URL"] == controlled_target
    assert json.loads(first_claim["environment"]["LOAD_DEFAULT_HEADERS_JSON"]) == {"X-Controlled-Run": "yes"}
    assert "Object.assign({}, defaultHeaders, request.headers)" in first_claim["script"]
    assert first_claim["dataset_rows"] and second_claim["dataset_rows"]
    first_items = {item["item_id"] for item in first_claim["dataset_rows"]}
    second_items = {item["item_id"] for item in second_claim["dataset_rows"]}
    assert first_items.isdisjoint(second_items)
    assert first_items | second_items == {item["item_id"] for item in rows}

    for registration, claim in ((first, first_claim), (second, second_claim)):
        shard_id = claim["id"]
        load_agent_http._start_shard(registration.agent.id, shard_id, {"process_info": {"runtime": "controlled"}})
    first_result = LoadMetricService(load_factory).ingest(first.agent.id, first_claim["id"], _metric("first", NOW, requests=10, http_failures=1))
    duplicate = LoadMetricService(load_factory).ingest(first.agent.id, first_claim["id"], _metric("first", NOW, requests=10, http_failures=1))
    LoadMetricService(load_factory).ingest(second.agent.id, second_claim["id"], _metric("second", NOW, requests=10, business_failures=1))
    assert first_result["duplicate"] is False
    assert duplicate["duplicate"] is True
    service.finish_shard(first.agent.id, first_claim["id"], "finished", summary={"requests": 10})
    service.finish_shard(second.agent.id, second_claim["id"], "finished", summary={"requests": 10})

    report_service = LoadReportService(load_factory)
    report = report_service.build(run.id, "load-e2e")
    assert report["transport"]["requests"] == 20
    assert report["transport"]["http_failures"] == 1
    assert report["business"]["failures"] == 1
    assert report["workflow"]["failures"] == 2
    assert report["evidence"]["total_shards"] == 2
    assert report["evidence"]["finished_shards"] == 2

    analysis = LoadAiAnalysisService(
        load_factory,
        report_service=report_service,
        analyzer=lambda _evidence: (_ for _ in ()).throw(TimeoutError("controlled timeout")),
    ).request(run.id, "load-e2e")
    failed_analysis = LoadAiAnalysisService(
        load_factory,
        report_service=report_service,
        analyzer=lambda _evidence: (_ for _ in ()).throw(TimeoutError("controlled timeout")),
    ).process(analysis.id)
    assert failed_analysis.state == "failed"
    assert "超时" in failed_analysis.error
    assert report_service.build(run.id, "load-e2e")["transport"]["requests"] == 20
    with load_factory() as session:
        persisted = session.get(ApiLoadRun, run.id)
        shards = tuple(session.scalars(select(ApiLoadRunShard).where(ApiLoadRunShard.run_id == run.id)))
    assert persisted.state == "finished"
    assert persisted.ai_analysis_state == "failed"
    assert len(shards) == 2
