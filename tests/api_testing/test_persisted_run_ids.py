"""Exercise persisted execution IDs with isolated PostgreSQL, Redis and real loopback HTTP."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from task_server.api_testing.contracts.case import CasePayloadError, parse_case_payload
from task_server.api_testing.events import EventStream
from task_server.api_testing.models.execution import ApiExecutionAttempt
from task_server.api_testing.models.environment import ApiEnvironmentRevision, ApiEnvironmentService
from task_server.api_testing.repositories.execution_repository import ExecutionRepository
from task_server.api_testing.services.case_service import CaseService
from task_server.api_testing.services.execution_service import ExecutionService
from tests.api_testing.test_case_service import valid_list_case
from tests.api_testing.test_execution_service import (
    _audit, _request, execution_context, execution_database, redis_client, session_factory,
)


def test_persisted_ids_reach_http_and_are_isolated(session_factory, redis_client, execution_context, execution_database, monkeypatch):
    monkeypatch.setenv("TASK_APP_ENV", "test")
    monkeypatch.setenv("API_TESTING_TEST_ALLOWED_HOSTS", "127.0.0.1")
    seen = []

    class Capture(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            seen.append((self.headers.get_all("X-Execution-ID"), parse_qs(urlsplit(self.path).query)["label"]))
            self.send_response(200)
            self.send_header("Content-Length", "10")
            self.end_headers()
            self.wfile.write(b'{"code":0}')

    endpoint = execution_context["endpoint"]
    payload = valid_list_case(endpoint)
    payload["request"].update(run_id_binding=1, headers={"X-Execution-ID": "{{@execution_id}}"},
                              query={"label": "test-{{@execution_case_id}}"})
    payload["assertions"] = payload["extractions"] = []
    payload["data_rows"][0]["values"]["execution_id"] = "forged-row"
    case_service = CaseService(session_factory)
    version = case_service.create_draft(endpoint.id, payload, "manual", "admin")
    validation = case_service.validate_case(version.id, {"services":["default"], "variables":["Biz"]})
    assert not any(issue.code in {"placeholder_invalid", "run_id_binding"} for issue in validation.errors)
    from task_server.api_testing.services.load_scenario_service import LoadScenarioService
    load = LoadScenarioService.copy_from_case_versions(name="功能执行 ID", description="", cases=[version])
    assert not load.accepted and load.issues[0].code == "execution_id_unavailable"
    with ThreadingHTTPServer(("127.0.0.1", 0), Capture) as target:
        server = Thread(target=target.serve_forever, daemon=True)
        server.start()
        try:
            revision_id = execution_context["environment_revision"].id
            with session_factory.begin() as session:
                revision = session.get(ApiEnvironmentRevision, revision_id)
                revision.default_headers = {"x-execution-id": "forged-environment"}
                session.add(ApiEnvironmentService(revision_id=revision_id, service_name="default",
                                                  base_url=f"http://127.0.0.1:{target.server_port}", **_audit()))
            service = ExecutionService(session_factory, event_stream=EventStream(session_factory, redis_client=redis_client))
            request = _request(execution_context, case_version_ids=[version.id], overrides={"execution_case_id":"forged-override"})
            ids = [service.submit(request, "admin", str(uuid4())).id for _ in range(2)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(service.run, ids))
            with session_factory() as session:
                repository = ExecutionRepository(session)
                children = [repository.get_execution_cases(parent)[0] for parent in ids]
                assert [child.status for child in children] == ["PASSED", "PASSED"]
                assert sorted(seen) == sorted(([parent], ["test-" + child.id]) for parent, child in zip(ids, children))
            assert case_service.get_version(version.id).request["headers"]["X-Execution-ID"] == "{{@execution_id}}"
            # No persisted child exists for direct preview/execution. The resolver refuses the token before HTTP.
            direct = service.executor.execute_case(version.id, revision_id, {})
            assert direct.status == "BROKEN"
            assert "运行标识" in direct.error_message
            assert len(seen) == 2
            # A release check may supply an independently archived older worker checkout.
            if os.environ.get("TEST_LEGACY_RUN_ID_WORKER"):
                old = Path(os.environ["TEST_LEGACY_RUN_ID_WORKER"]).resolve(strict=True)
                old_id = service.submit(request, "admin", str(uuid4())).id
                script = (
                    "import os,sys,redis; from sqlalchemy import create_engine; from sqlalchemy.orm import sessionmaker; "
                    "from task_server.api_testing.services.execution_service import ExecutionService; "
                    "from task_server.api_testing.events import EventStream; "
                    "factory=sessionmaker(bind=create_engine(os.environ['TEST_SCHEMA_URL']),expire_on_commit=False); "
                    "ExecutionService(factory,event_stream=EventStream(factory,redis.Redis.from_url(os.environ['TEST_REDIS_URL']))).run(sys.argv[1])"
                )
                env = {**os.environ, "TEST_SCHEMA_URL":execution_database, "PYTHONPATH":str(old)}
                subprocess.run([sys.executable, "-c", script, old_id], cwd=old, env=env, check=True, timeout=30)
                with session_factory() as session:
                    legacy = ExecutionRepository(session).get_execution_cases(old_id)[0]
                    assert legacy.status == "BROKEN"
                    assert legacy.failure_category == "environment"
                    attempt = session.query(ApiExecutionAttempt).filter_by(execution_case_id=legacy.id).one()
                    assert "invalid environment placeholder syntax" in attempt.error_message
                assert len(seen) == 2
        finally:
            target.shutdown()
            server.join(timeout=2)


@pytest.mark.parametrize("change", ["method", "no-token", "duplicate-header", "wrong-field", "version"])
def test_reject_invalid_opt_in_before_persistence(execution_context, change):
    payload = valid_list_case(execution_context["endpoint"])
    request = payload["request"]
    request.update(run_id_binding=1, headers={"X-Execution-ID": "{{@execution_id}}"})
    if change == "method":
        request["method"] = "POST"
    elif change == "no-token":
        request["headers"] = {}
    elif change == "duplicate-header":
        request["headers"]["x-execution-id"] = "different"
    elif change == "wrong-field":
        request["cookies"] = {"id": "{{@execution_case_id}}"}
    elif change == "version":
        request["run_id_binding"] = True
    with pytest.raises(CasePayloadError):
        parse_case_payload(payload)


@pytest.mark.parametrize("header", ["Cookie", "Host", "Authorization", "Content-Length", "Connection",
                                   "X-Forwarded-For", "X-HTTP-Method-Override", "X-User-Id", "Location"])
def test_binding_refuses_controls_before_sending_or_persisting(execution_context, header):
    payload = valid_list_case(execution_context["endpoint"])
    payload["request"].update(run_id_binding=1, headers={header: "trace={{@execution_id}}"},
                              cookies={"trace": "another-record"})
    with pytest.raises(CasePayloadError, match="追踪请求头"):
        parse_case_payload(payload)
