import io
import json
import os
import subprocess
from http.client import HTTPConnection
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
import time

import pytest


def test_normal_json_limit_is_lower_than_streamed_upload_limit():
    from task_server import config

    assert config.MAX_BODY_SIZE == 64 * 1024 * 1024
    assert config.MAX_UPLOAD_BODY_SIZE == 300 * 1024 * 1024
    assert config.MAX_BODY_SIZE < config.MAX_UPLOAD_BODY_SIZE


def test_only_raw_report_uses_large_upload_limit():
    from task_server.response import request_body_limit
    from task_server import config

    assert request_body_limit("/report") == config.MAX_UPLOAD_BODY_SIZE
    assert request_body_limit("/api/report/chunk") == config.MAX_BODY_SIZE
    assert request_body_limit("/api/report/chunk-finish") == config.MAX_BODY_SIZE
    assert request_body_limit("/api/app-install/upload-chunk") == config.MAX_BODY_SIZE


def test_stream_body_to_file_does_not_call_unbounded_read(tmp_path):
    from task_server.response import ResponseMixin

    payload = b"abcdefghij"

    class GuardedReader(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 4
            return super().read(size)

    handler = SimpleNamespace(
        headers={"Content-Length": str(len(payload))},
        rfile=GuardedReader(payload),
        _qs=lambda: ({}, "/report"),
    )
    destination = tmp_path / "report.html"

    written = ResponseMixin._stream_body_to_file(handler, destination, chunk_size=4)

    assert written == len(payload)
    assert destination.read_bytes() == payload


def test_incomplete_stream_does_not_publish_or_leave_temporary_file(tmp_path):
    from task_server.response import ResponseMixin

    handler = SimpleNamespace(
        headers={"Content-Length": "10"},
        rfile=io.BytesIO(b"short"),
        _qs=lambda: ({}, "/report"),
    )
    destination = tmp_path / "report.html"

    with pytest.raises(ConnectionError, match="未完整接收"):
        ResponseMixin._stream_body_to_file(handler, destination, chunk_size=4)

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_negative_content_length_is_rejected_before_reading():
    from task_server.response import InvalidBodyLength, ResponseMixin

    class ReaderThatMustNotRun:
        def read(self, _size=-1):
            raise AssertionError("negative Content-Length must not read the socket")

    handler = SimpleNamespace(
        headers={"Content-Length": "-1"},
        rfile=ReaderThatMustNotRun(),
        _qs=lambda: ({}, "/api/sonic/suite-complete"),
    )

    with pytest.raises(InvalidBodyLength, match="不能为负数"):
        ResponseMixin._raw_body(handler)


def test_sonic_cache_prunes_expired_and_old_entries(monkeypatch):
    from task_server.services import sonic_service

    now = [100.0]
    monkeypatch.setattr(sonic_service.time, "time", lambda: now[0])
    monkeypatch.setattr(sonic_service, "_MEM_CACHE_MAX_ENTRIES", 2)
    sonic_service._cache_invalidate()

    sonic_service._cache_set("result:1", {"id": 1})
    now[0] += 1
    sonic_service._cache_set("result:2", {"id": 2})
    now[0] += 1
    sonic_service._cache_set("result:3", {"id": 3})

    assert sonic_service._cache_get("result:1", 60) is None
    assert sonic_service._cache_get("result:2", 60) == {"id": 2}
    now[0] += 61
    assert sonic_service._cache_get("result:2", 60) is None
    assert "result:2" not in sonic_service._MEM_CACHE


def test_runtime_metrics_report_current_process_shape():
    from task_server.runtime_metrics import process_runtime_metrics

    metrics = process_runtime_metrics()

    assert metrics["pid"] > 0
    assert metrics["rss_mb"] > 0
    assert metrics["peak_rss_mb"] >= metrics["rss_mb"] or metrics["peak_rss_mb"] > 0
    assert metrics["threads"] >= 1


def test_server_rejects_new_connection_when_request_capacity_is_full():
    from task_server.app import TaskHTTPHandler, ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), TaskHTTPHandler, max_requests=1)
    thread = Thread(target=server.serve_forever, daemon=True)
    assert server._request_slots.acquire(blocking=False)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        assert response.status == 503
        assert "稍后重试" in body["error"]
    finally:
        server._request_slots.release()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_server_bounds_large_inflight_requests():
    from task_server.app import TaskHTTPHandler, ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), TaskHTTPHandler)
    try:
        assert server.acquire_large_request() is True
        assert server.acquire_large_request() is True
        assert server.acquire_large_request() is False
        assert server.runtime_status()["active_large_requests"] == 2
        server.release_large_request()
        server.release_large_request()
        assert server.runtime_status()["active_large_requests"] == 0
    finally:
        server.server_close()


def test_api_testing_authentication_precedes_outer_large_request_limit():
    from task_server.app import TaskHTTPHandler, ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), TaskHTTPHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    assert server.acquire_large_request() is True
    assert server.acquire_large_request() is True
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        connection.putrequest("POST", "/api/api-testing/v1/projects")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(9 * 1024 * 1024))
        connection.endheaders()
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))

        assert response.status == 401
        assert body["error"]["code"] == "unauthorized"
    finally:
        server.release_large_request()
        server.release_large_request()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_persisted_background_dispatcher_bounds_running_and_queued_jobs():
    from task_server.background_jobs import PersistedJobDispatcher

    started = Event()
    release = Event()
    calls = []
    jobs = {
        job_id: {
            "job_id": job_id,
            "type": "generate",
            "status": "pending",
            "request_data": {"job": job_id},
        }
        for job_id in ("job-1", "job-2", "job-3")
    }

    def run_job(job_id, request_data):
        calls.append((job_id, request_data))
        if job_id == "job-1":
            started.set()
            assert release.wait(timeout=2)

    dispatcher = PersistedJobDispatcher(
        worker_count=1,
        queue_size=1,
        job_loader=jobs.get,
        runner_resolver=lambda job_type: run_job if job_type == "generate" else None,
        failure_recorder=lambda *_args: None,
    )
    dispatcher.start()
    try:
        assert dispatcher.submit("job-1") is True
        assert started.wait(timeout=2)
        assert dispatcher.submit("job-2") is True
        assert dispatcher.submit("job-3") is False

        metrics = dispatcher.metrics()
        assert metrics["background_running"] == 1
        assert metrics["background_queued"] == 1
        assert metrics["background_queue_capacity"] == 1
        assert metrics["background_rejected_total"] == 1

        release.set()
        deadline = time.time() + 2
        while len(calls) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert calls == [
            ("job-1", {"job": "job-1"}),
            ("job-2", {"job": "job-2"}),
        ]
    finally:
        release.set()
        dispatcher.stop()


def test_sync_and_background_heavy_work_share_one_capacity_limit():
    from task_server.background_jobs import HeavyWorkloadLimiter

    limiter = HeavyWorkloadLimiter(capacity=2)
    assert limiter.acquire("background", blocking=False) is True
    assert limiter.acquire("sync", blocking=False) is True
    assert limiter.acquire("sync", blocking=False) is False
    assert limiter.metrics() == {
        "heavy_workloads_active": 2,
        "heavy_workloads_max": 2,
        "sync_heavy_workloads_active": 1,
        "heavy_workloads_rejected_total": 1,
    }
    limiter.release("sync")
    limiter.release("background")


def test_async_generation_routes_use_the_bounded_dispatcher():
    source = Path("task_server/router.py").read_text(encoding="utf-8")

    assert "enqueue_persisted_background_job" in source
    assert "threading.Thread(target=run_generate_job" not in source
    assert "threading.Thread(target=run_mindmap_only_job" not in source
    assert "threading.Thread(target=run_figma_parse_job" not in source
    assert "threading.Thread(target=run_repair_job" not in source
    assert source.count("_acquire_sync_heavy_workload(handler)") >= 3
    assert source.count("release_heavy_workload_slot(\"sync\")") >= 3


def test_background_job_restore_resumes_pending_and_marks_interrupted(monkeypatch):
    from task_server import background_jobs
    from task_server.services import yaml_service

    jobs = [
        {
            "job_id": "running-job",
            "type": "generate",
            "status": "running",
            "request_data": {"title": "running"},
        },
        {
            "job_id": "pending-job",
            "type": "mindmap_only",
            "status": "pending",
            "request_data": {"title": "pending"},
        },
        {
            "job_id": "missing-request-job",
            "type": "repair",
            "status": "pending",
        },
    ]
    updates = {}
    submitted = []
    restore_limits = []

    class Dispatcher:
        def start(self):
            return True

        def submit(self, job_id):
            submitted.append(job_id)
            return True

    def update(job_id, **changes):
        updates[job_id] = changes
        return {"job_id": job_id, **changes}

    monkeypatch.setattr(background_jobs, "_DISPATCHER", Dispatcher())
    monkeypatch.setattr(
        yaml_service,
        "iter_raw_generate_jobs",
        lambda limit=300: restore_limits.append(limit) or jobs,
    )
    monkeypatch.setattr(yaml_service, "load_generate_job", lambda job_id: next(job for job in jobs if job["job_id"] == job_id))
    monkeypatch.setattr(yaml_service, "update_generate_job", update)

    result = background_jobs.restore_persisted_background_jobs()

    assert result == {"restored": 1, "interrupted": 2, "rejected": 0}
    assert restore_limits == [None]
    assert submitted == ["pending-job"]
    assert updates["running-job"]["status"] == "failed"
    assert "服务重启中断" in updates["running-job"]["step"]
    assert updates["missing-request-job"]["status"] == "failed"
    assert "缺少原始请求" in updates["missing-request-job"]["message"]


def test_active_job_restore_can_scan_beyond_history_display_limit(tmp_path, monkeypatch):
    from task_server.services import yaml_service

    monkeypatch.setattr(yaml_service, "GENERATE_JOB_DIR", str(tmp_path))
    for index in range(305):
        (tmp_path / f"gen-{index:03d}.json").write_text(
            json.dumps({"job_id": f"gen-{index:03d}", "status": "pending"}),
            encoding="utf-8",
        )

    assert len(yaml_service.iter_raw_generate_jobs(limit=None)) == 305


def test_figma_and_repair_jobs_do_not_overwrite_running_cancellation(monkeypatch):
    from task_server.services import repair_service, yaml_service

    figma_updates = []
    figma_checks = iter((False, True))
    monkeypatch.setattr(yaml_service, "generate_job_should_stop", lambda _job_id: next(figma_checks))
    monkeypatch.setattr(yaml_service, "update_generate_job", lambda _job_id, **changes: figma_updates.append(changes))
    monkeypatch.setattr(yaml_service, "parse_figma_design", lambda _request: {"drafts": []})

    yaml_service.run_figma_parse_job("figma-job", {})

    assert not any(update.get("status") == "success" for update in figma_updates)

    repair_updates = []
    repair_checks = iter((False, True))
    monkeypatch.setattr(repair_service, "generate_job_should_stop", lambda _job_id: next(repair_checks))
    monkeypatch.setattr(repair_service, "update_generate_job", lambda _job_id, **changes: repair_updates.append(changes))
    monkeypatch.setattr(repair_service, "repair_file_latest_result", lambda _request, job_id=None: {"next_job": None})

    repair_service.run_repair_job("repair-job", {"scope": "file"})

    assert not any(update.get("status") == "success" for update in repair_updates)


def test_installer_writes_configurable_systemd_resource_guard():
    source = Path("deploy/install-server.sh").read_text(encoding="utf-8")

    assert 'TASK_SERVICE_MEMORY_HIGH="${TASK_SERVICE_MEMORY_HIGH:-2G}"' in source
    assert 'TASK_SERVICE_MEMORY_MAX="${TASK_SERVICE_MEMORY_MAX:-3G}"' in source
    assert 'TASK_SERVICE_TASKS_MAX="${TASK_SERVICE_TASKS_MAX:-256}"' in source
    assert 'MemoryHigh=${TASK_SERVICE_MEMORY_HIGH}' in source
    assert 'MemoryMax=${TASK_SERVICE_MEMORY_MAX}' in source
    assert 'TasksMax=${TASK_SERVICE_TASKS_MAX}' in source
    assert 'ensure_env_default "TASK_BACKGROUND_WORKERS" "2"' in source
    assert 'ensure_env_default "TASK_BACKGROUND_QUEUE_SIZE" "8"' in source


def test_sonic_restart_script_is_explicit_and_non_destructive_by_default():
    source = Path("deploy/configure-sonic-restart.sh").read_text(encoding="utf-8")

    assert "docker update --restart unless-stopped" in source
    assert "--start-stopped" in source
    assert 'START_STOPPED="0"' in source
    assert "docker rm" not in source
    assert "docker compose down" not in source


def test_sonic_restart_script_only_starts_containers_when_explicit(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    calls = tmp_path / "docker-calls.log"
    docker.write_text(
        """#!/usr/bin/env bash
set -e
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
if [ "$1" = "ps" ]; then
  printf '%s\\n' 'sonic-server-272-sonic-server-eureka-1' 'sonic-server-272-sonic-server-gateway-1'
elif [ "$1" = "inspect" ] && printf '%s' "$*" | grep -q 'State.Running'; then
  printf '%s\\n' 'false'
elif [ "$1" = "inspect" ]; then
  printf '%s\\n' '容器=/fake 状态=running 重启策略=unless-stopped'
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "DOCKER_CALLS": str(calls),
    }

    subprocess.run(["bash", "deploy/configure-sonic-restart.sh"], check=True, env=env, capture_output=True, text=True)
    default_calls = calls.read_text(encoding="utf-8")
    assert default_calls.count("update --restart unless-stopped") == 2
    assert "start sonic-server" not in default_calls

    calls.write_text("", encoding="utf-8")
    subprocess.run(
        ["bash", "deploy/configure-sonic-restart.sh", "--start-stopped"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    explicit_calls = calls.read_text(encoding="utf-8")
    assert explicit_calls.count("start sonic-server") == 2


def test_platform_deploy_warns_about_non_restarting_sonic_without_changing_it():
    source = Path("deploy/update-main-server.sh").read_text(encoding="utf-8")

    assert "warn_sonic_restart_policies" in source
    assert "restart=no" in source
    assert "bash deploy/configure-sonic-restart.sh" in source
    assert "docker update --restart" not in source
