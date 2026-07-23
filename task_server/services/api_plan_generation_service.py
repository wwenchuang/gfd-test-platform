"""Persisted, source-scoped asynchronous API plan generation batches."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, List

from task_server.storage import clean_id, read_json_file, safe_join, unique_millis_id, write_json_file
from task_server.services import (
    api_asset_service,
    api_case_contract_service,
    api_module_service,
    api_source_service,
    api_test_plan_service,
    api_workspace_service,
)


API_TESTING_DIR = api_asset_service.API_TESTING_DIR
MAX_ENDPOINTS = 60
AI_BATCH_SIZE = 12
POLL_AFTER_MS = 1000
TERMINAL_STATES = {"succeeded", "partial", "failed", "cancelled"}

_GENERATION_LOCK = threading.RLock()
_RUNNING_GENERATIONS: set[str] = set()
_SCHEDULED_GENERATIONS: set[str] = set()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _generation_path(generation_id: str) -> str:
    return safe_join(
        API_TESTING_DIR,
        "plan-generations",
        f"{clean_id(generation_id, 'api_plan_generation')}.json",
    )


def _safe(value: Any) -> Any:
    return api_case_contract_service.sanitize_sensitive_data(value)


def _write_generation(record: Dict[str, Any]) -> None:
    write_json_file(
        _generation_path(str(record.get("generation_id") or "")),
        _safe(record),
    )


def _append_event(
    record: Dict[str, Any],
    status: str,
    message: str,
    *,
    batch_index: int = 0,
) -> None:
    event = {
        "event_id": unique_millis_id("api_plan_generation_event"),
        "at": _now(),
        "status": str(status or ""),
        "message": str(_safe(message) or ""),
    }
    if batch_index:
        event["batch_index"] = int(batch_index)
    events = record.get("events") if isinstance(record.get("events"), list) else []
    events.append(event)
    record["events"] = events[-200:]


def _normalized_module_paths(values: List[str] | None) -> List[str]:
    return sorted({
        normalized
        for value in (values or [])
        for normalized in [api_module_service.normalize_module_path(value)]
        if normalized
    })


def _source_scope_fingerprint(source: Dict[str, Any]) -> str:
    scope = api_source_service.normalized_sync_scope(source.get("sync_scope"))
    return api_module_service.scope_fingerprint(scope)


def _validated_generation_scope(
    source_id: str,
    revision_id: str,
    endpoint_ids: List[str],
    module_paths: List[str],
) -> Dict[str, Any]:
    source = api_source_service.get_api_source(source_id, masked=True)
    if not source:
        raise ValueError("API source 不存在")
    revision = api_asset_service.get_api_revision(revision_id)
    if not revision:
        raise ValueError("API revision 不存在")
    if str(revision.get("source_id") or "").strip() != source_id:
        raise ValueError("API revision 不属于当前 source")
    asset = api_asset_service.get_api_asset(str(revision.get("asset_id") or ""))
    if str(asset.get("active_revision_id") or "").strip() != revision_id:
        raise ValueError("API revision 不是当前 source 的活动版本")
    selected_ids = [
        str(item or "").strip()
        for item in endpoint_ids
        if str(item or "").strip()
    ]
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("所选接口不能重复")
    if not 1 <= len(selected_ids) <= MAX_ENDPOINTS:
        raise ValueError("一次 AI 计划生成必须选择 1-60 个接口")
    endpoint_by_id = {
        str(endpoint.get("endpoint_id") or "").strip(): endpoint
        for endpoint in (revision.get("endpoints") or [])
        if isinstance(endpoint, dict) and str(endpoint.get("endpoint_id") or "").strip()
    }
    if any(endpoint_id not in endpoint_by_id for endpoint_id in selected_ids):
        raise ValueError("所选接口不存在或不属于当前 revision")
    selected_endpoints = [endpoint_by_id[endpoint_id] for endpoint_id in selected_ids]
    selected_modules = list(module_paths)
    if not selected_modules:
        selected_modules = sorted({
            api_module_service.normalize_module_path(
                endpoint.get("module_path") or endpoint.get("module")
            )
            for endpoint in selected_endpoints
            if api_module_service.normalize_module_path(
                endpoint.get("module_path") or endpoint.get("module")
            )
        })
    if not selected_modules or any(
        not api_module_service.module_selected(
            endpoint.get("module_path") or endpoint.get("module"),
            selected_modules,
        )
        for endpoint in selected_endpoints
    ):
        raise ValueError("所选接口不属于指定模块范围")
    scope_fingerprint = _source_scope_fingerprint(source)
    revision_scope = str(revision.get("scope_fingerprint") or "").strip()
    if revision_scope and revision_scope != scope_fingerprint:
        raise ValueError("API source 同步范围已变更，请先同步最新 revision")
    workspace_binding = api_workspace_service.get_api_workspace_binding(
        source_id,
        allow_legacy=True,
    )
    if not workspace_binding:
        raise ValueError("请先绑定当前 source 的 MeterSphere 项目和环境")
    return {
        "source": source,
        "revision": revision,
        "asset": asset,
        "endpoint_ids": selected_ids,
        "endpoints": selected_endpoints,
        "module_paths": selected_modules,
        "scope_fingerprint": scope_fingerprint,
        "workspace_binding": workspace_binding,
        "auth_binding": api_workspace_service.get_api_auth_binding(source_id),
    }


def get_api_plan_generation(generation_id: str) -> Dict[str, Any]:
    record = read_json_file(_generation_path(generation_id), default={}) or {}
    return _safe(record) if isinstance(record, dict) else {}


def _generation_records() -> List[Dict[str, Any]]:
    root = safe_join(API_TESTING_DIR, "plan-generations")
    if not os.path.isdir(root):
        return []
    records = []
    for name in os.listdir(root):
        if not name.endswith(".json"):
            continue
        record = read_json_file(safe_join(root, name), default={}) or {}
        if isinstance(record, dict) and record.get("generation_id"):
            records.append(record)
    records.sort(
        key=lambda item: str(item.get("created_at") or item.get("updated_at") or "")
    )
    return records


def start_api_plan_generation(
    source_id: str,
    revision_id: str,
    endpoint_ids: List[str],
    module_paths: List[str],
    model_config: Dict[str, Any] | None = None,
    spawn: bool = True,
) -> Dict[str, Any]:
    selected_source_id = str(source_id or "").strip()
    selected_revision_id = str(revision_id or "").strip()
    normalized_modules = _normalized_module_paths(module_paths)
    scope = _validated_generation_scope(
        selected_source_id,
        selected_revision_id,
        endpoint_ids or [],
        normalized_modules,
    )
    selected_ids = scope["endpoint_ids"]
    selected_endpoints = scope["endpoints"]
    batch_count = (len(selected_ids) + AI_BATCH_SIZE - 1) // AI_BATCH_SIZE
    generation_id = unique_millis_id("api_plan_generation")
    now = _now()
    batches = []
    for offset in range(0, len(selected_ids), AI_BATCH_SIZE):
        batch_index = len(batches) + 1
        batch_endpoints = selected_endpoints[offset:offset + AI_BATCH_SIZE]
        batches.append({
            "batch_index": batch_index,
            "status": "queued",
            "endpoint_count": len(batch_endpoints),
            "endpoint_ids": selected_ids[offset:offset + AI_BATCH_SIZE],
            "selected_endpoint_keys": [
                str(endpoint.get("endpoint_key") or "")
                for endpoint in batch_endpoints
            ],
            "plan_id": "",
            "attempts": 0,
            "started_at": "",
            "finished_at": "",
            "error": "",
        })
    binding = scope["workspace_binding"]
    record = {
        "generation_id": generation_id,
        "source_id": selected_source_id,
        "asset_id": str(scope["revision"].get("asset_id") or ""),
        "asset_revision_id": selected_revision_id,
        "module_paths": scope["module_paths"],
        "selected_endpoint_keys": [
            str(endpoint.get("endpoint_key") or "")
            for endpoint in selected_endpoints
        ],
        "scope_fingerprint": scope["scope_fingerprint"],
        "execution_binding_id": str(binding.get("binding_id") or ""),
        "binding_fingerprint": str(binding.get("config_fingerprint") or ""),
        "auth_binding": scope["auth_binding"],
        "model_config": _safe(model_config or {}),
        "status": "queued",
        "batch_size": AI_BATCH_SIZE,
        "batch_count": batch_count,
        "completed_batches": 0,
        "failed_batches": 0,
        "retry_count": 0,
        "poll_after_ms": POLL_AFTER_MS,
        "created_at": now,
        "started_at": "",
        "updated_at": now,
        "finished_at": "",
        "error": "",
        "batches": batches,
        "events": [],
    }
    _append_event(record, "queued", "AI 计划生成已排队")
    with _GENERATION_LOCK:
        _write_generation(record)
    if spawn:
        _spawn_generation_worker(generation_id)
    return get_api_plan_generation(generation_id)


def _save_transition(record: Dict[str, Any]) -> None:
    record["completed_batches"] = sum(
        1 for batch in record.get("batches") or []
        if batch.get("status") == "succeeded"
    )
    record["failed_batches"] = sum(
        1 for batch in record.get("batches") or []
        if batch.get("status") == "failed"
    )
    record["updated_at"] = _now()
    _write_generation(record)


def _finalize_generation(record: Dict[str, Any]) -> Dict[str, Any]:
    succeeded = sum(
        1 for batch in record.get("batches") or []
        if batch.get("status") == "succeeded"
    )
    failed = sum(
        1 for batch in record.get("batches") or []
        if batch.get("status") == "failed"
    )
    record["completed_batches"] = succeeded
    record["failed_batches"] = failed
    if failed and succeeded:
        record["status"] = "partial"
    elif failed:
        record["status"] = "failed"
    else:
        record["status"] = "succeeded"
    record["finished_at"] = _now()
    record["error"] = (
        f"{failed} 个 AI 批次失败"
        if failed
        else ""
    )
    _append_event(record, record["status"], "AI 计划批次处理完成")
    _save_transition(record)
    return record


def run_api_plan_generation(
    generation_id: str,
    *,
    generate_plan: Callable[..., Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    selected_generation_id = str(generation_id or "").strip()
    with _GENERATION_LOCK:
        _SCHEDULED_GENERATIONS.discard(selected_generation_id)
        if selected_generation_id in _RUNNING_GENERATIONS:
            return get_api_plan_generation(selected_generation_id)
        record = get_api_plan_generation(selected_generation_id)
        if not record:
            raise ValueError("API plan generation 不存在")
        if record.get("status") in TERMINAL_STATES:
            return record
        _RUNNING_GENERATIONS.add(selected_generation_id)
        record["status"] = "running"
        record["started_at"] = str(record.get("started_at") or _now())
        record["finished_at"] = ""
        record["error"] = ""
        _append_event(record, "running", "开始串行生成 AI 计划批次")
        _save_transition(record)
    generator = generate_plan or api_test_plan_service.generate_api_test_plan
    try:
        for position in range(len(record.get("batches") or [])):
            with _GENERATION_LOCK:
                record = get_api_plan_generation(selected_generation_id)
                batch = record["batches"][position]
                if batch.get("status") != "queued":
                    continue
                batch["status"] = "running"
                batch["attempts"] = int(batch.get("attempts") or 0) + 1
                batch["started_at"] = _now()
                batch["finished_at"] = ""
                batch["error"] = ""
                _append_event(
                    record,
                    "running",
                    f"开始生成第 {batch['batch_index']} 个 AI 批次",
                    batch_index=int(batch["batch_index"]),
                )
                _save_transition(record)
            try:
                plan = generator(
                    str(record.get("asset_revision_id") or ""),
                    list(batch.get("endpoint_ids") or []),
                    model_config=record.get("model_config") or None,
                    use_ai=True,
                    source_id=str(record.get("source_id") or ""),
                    module_paths=list(record.get("module_paths") or []),
                    generation_id=selected_generation_id,
                    batch_index=int(batch.get("batch_index") or 0),
                    batch_count=int(record.get("batch_count") or 0),
                    require_ai_success=True,
                )
                plan_id = str((plan or {}).get("plan_id") or "").strip()
                if not plan_id:
                    raise ValueError("AI 批次未返回 plan_id")
                with _GENERATION_LOCK:
                    record = get_api_plan_generation(selected_generation_id)
                    batch = record["batches"][position]
                    batch["status"] = "succeeded"
                    batch["plan_id"] = plan_id
                    batch["finished_at"] = _now()
                    batch["error"] = ""
                    _append_event(
                        record,
                        "succeeded",
                        f"第 {batch['batch_index']} 个 AI 批次已生成计划",
                        batch_index=int(batch["batch_index"]),
                    )
                    _save_transition(record)
            except Exception as exc:
                with _GENERATION_LOCK:
                    record = get_api_plan_generation(selected_generation_id)
                    batch = record["batches"][position]
                    batch["status"] = "failed"
                    batch["plan_id"] = ""
                    batch["finished_at"] = _now()
                    batch["error"] = str(_safe(str(exc)) or "AI 批次生成失败")[:500]
                    _append_event(
                        record,
                        "failed",
                        batch["error"],
                        batch_index=int(batch["batch_index"]),
                    )
                    _save_transition(record)
        with _GENERATION_LOCK:
            record = get_api_plan_generation(selected_generation_id)
            return get_api_plan_generation(
                _finalize_generation(record)["generation_id"]
            )
    finally:
        with _GENERATION_LOCK:
            _RUNNING_GENERATIONS.discard(selected_generation_id)


def _run_api_plan_generation_guarded(generation_id: str) -> None:
    try:
        run_api_plan_generation(generation_id)
    except Exception as exc:
        with _GENERATION_LOCK:
            record = get_api_plan_generation(generation_id)
            if not record or record.get("status") in TERMINAL_STATES:
                return
            record["status"] = "failed"
            record["failed_batches"] = max(1, int(record.get("failed_batches") or 0))
            record["finished_at"] = _now()
            record["error"] = str(_safe(str(exc)) or "AI 计划生成失败")[:500]
            _append_event(record, "failed", record["error"])
            _save_transition(record)


def _spawn_generation_worker(generation_id: str) -> None:
    selected_generation_id = str(generation_id or "").strip()
    with _GENERATION_LOCK:
        if (
            not selected_generation_id
            or selected_generation_id in _SCHEDULED_GENERATIONS
            or selected_generation_id in _RUNNING_GENERATIONS
        ):
            return
        _SCHEDULED_GENERATIONS.add(selected_generation_id)
    thread = threading.Thread(
        target=_run_api_plan_generation_guarded,
        args=(selected_generation_id,),
        name=f"api-plan-generation-{clean_id(selected_generation_id, 'generation')}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        with _GENERATION_LOCK:
            _SCHEDULED_GENERATIONS.discard(selected_generation_id)
        raise


def recover_api_plan_generations() -> Dict[str, int]:
    """Recover persisted generations without replaying successful AI batches."""
    queued_ids = []
    interrupted = 0
    with _GENERATION_LOCK:
        for record in _generation_records():
            generation_id = str(record.get("generation_id") or "").strip()
            status = str(record.get("status") or "").strip()
            if status == "queued":
                queued_ids.append(generation_id)
                continue
            if status != "running":
                continue
            for batch in record.get("batches") or []:
                if not isinstance(batch, dict):
                    continue
                if batch.get("status") in {"succeeded", "failed"}:
                    continue
                batch["status"] = "failed"
                batch["plan_id"] = ""
                batch["finished_at"] = _now()
                batch["error_code"] = "restart_interrupted"
                batch["error"] = "服务重启中断当前 AI 批次，可重试失败批次"
                batch["recoverable"] = True
            record["recoverable"] = True
            record["error_code"] = "restart_interrupted"
            _append_event(
                record,
                "failed",
                "服务重启中断未完成的 AI 批次，已保留成功计划并开放失败批次重试",
            )
            _finalize_generation(record)
            interrupted += 1
    for generation_id in queued_ids:
        _spawn_generation_worker(generation_id)
    return {
        "resumed_queued": len(queued_ids),
        "interrupted_running": interrupted,
    }


def retry_api_plan_generation(
    generation_id: str,
    *,
    spawn: bool = True,
) -> Dict[str, Any]:
    selected_generation_id = str(generation_id or "").strip()
    with _GENERATION_LOCK:
        record = get_api_plan_generation(selected_generation_id)
        if not record:
            raise ValueError("API plan generation 不存在")
        if record.get("status") not in {"partial", "failed"}:
            raise ValueError("只有失败或部分失败的 generation 可以重试")
        failed_batches = [
            batch for batch in (record.get("batches") or [])
            if batch.get("status") == "failed"
        ]
        if not failed_batches:
            raise ValueError("当前 generation 没有可重试的失败批次")
        for batch in failed_batches:
            batch["status"] = "queued"
            batch["plan_id"] = ""
            batch["started_at"] = ""
            batch["finished_at"] = ""
            batch["error"] = ""
        record["status"] = "queued"
        record["failed_batches"] = 0
        record["retry_count"] = int(record.get("retry_count") or 0) + 1
        record["finished_at"] = ""
        record["error"] = ""
        _append_event(record, "queued", "失败的 AI 批次已重新排队")
        _save_transition(record)
    if spawn:
        _spawn_generation_worker(selected_generation_id)
    return get_api_plan_generation(selected_generation_id)


__all__ = [
    "AI_BATCH_SIZE",
    "API_TESTING_DIR",
    "MAX_ENDPOINTS",
    "POLL_AFTER_MS",
    "TERMINAL_STATES",
    "get_api_plan_generation",
    "recover_api_plan_generations",
    "retry_api_plan_generation",
    "run_api_plan_generation",
    "start_api_plan_generation",
]
