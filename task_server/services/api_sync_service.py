"""Asynchronous Apifox source synchronization orchestration."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List

from task_server.storage import clean_id, read_json_file, safe_join, unique_millis_id, write_json_file
from task_server.services import (
    api_asset_service,
    api_module_service,
    api_schema_diff_service,
    api_source_service,
    api_test_plan_service,
)
from task_server.services.apifox_service import ApifoxSourceAdapter


TERMINAL_SYNC_STATES = {"succeeded", "no_change", "failed", "cancelled"}
ACTIVE_SYNC_STATES = {"queued", "running"}
SYNC_PHASES = (
    "fetch_source",
    "parse_document",
    "diff_revision",
    "persist_revision",
    "analyze_impact",
)
POLL_AFTER_MS = 1000
_SYNC_LOCK = threading.RLock()
_SCHEDULER_LOCK = threading.Lock()
_SCHEDULER_THREAD: threading.Thread | None = None


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _sync_path(sync_id: str) -> str:
    return safe_join(
        api_asset_service.API_TESTING_DIR,
        "syncs",
        f"{clean_id(sync_id, 'api_sync')}.json",
    )


def _sync_index_path() -> str:
    return safe_join(api_asset_service.API_TESTING_DIR, "syncs", "index.json")


def _sync_ids() -> List[str]:
    values = read_json_file(_sync_index_path(), default=[]) or []
    if not isinstance(values, list):
        return []
    return [str(item or "").strip() for item in values if str(item or "").strip()]


def _write_sync(record: Dict[str, Any]) -> None:
    sync_id = str(record.get("sync_id") or "")
    write_json_file(_sync_path(sync_id), record)
    ids = [item for item in _sync_ids() if item != sync_id]
    ids.insert(0, sync_id)
    write_json_file(_sync_index_path(), ids[:500])


def get_api_sync(sync_id: str) -> Dict[str, Any]:
    record = read_json_file(_sync_path(sync_id), default={}) or {}
    return record if isinstance(record, dict) else {}


def list_api_syncs(limit: int = 50, source_id: str = "") -> List[Dict[str, Any]]:
    try:
        size = max(1, int(limit))
    except Exception:
        size = 50
    target_source = str(source_id or "").strip()
    records: List[Dict[str, Any]] = []
    for sync_id in _sync_ids():
        record = get_api_sync(sync_id)
        if not record or (target_source and record.get("source_id") != target_source):
            continue
        records.append(record)
        if len(records) >= size:
            break
    return records


def _active_sync(source_id: str) -> Dict[str, Any]:
    for record in list_api_syncs(limit=200, source_id=source_id):
        if record.get("status") in ACTIVE_SYNC_STATES:
            return record
    return {}


def _redacted_error(error: Any, source: Dict[str, Any]) -> str:
    text = str(error or "").strip()
    for key in ("access_token", "token"):
        secret = str(source.get(key) or "").strip()
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(bearer\s+|authorization\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    return text[:500] or "API source 同步失败"


def _update_sync(sync_id: str, **changes: Any) -> Dict[str, Any]:
    with _SYNC_LOCK:
        record = get_api_sync(sync_id)
        if not record:
            raise ValueError("API sync 不存在")
        event = changes.pop("event", None)
        record.update(changes)
        record["updated_at"] = _now()
        if event:
            events = record.get("events") if isinstance(record.get("events"), list) else []
            events.append({"at": record["updated_at"], "phase": record.get("phase"), "message": str(event)})
            record["events"] = events[-100:]
        _write_sync(record)
        return record


def start_api_source_sync(
    source_id: str,
    *,
    spawn: bool = True,
    adapter: Any = None,
    trigger: str = "manual",
) -> Dict[str, Any]:
    target = str(source_id or "").strip()
    source = api_source_service.get_api_source(target, masked=False)
    if not source:
        raise ValueError("API source 不存在")
    if source.get("source_type") != "apifox":
        raise ValueError("当前 source 不支持远端同步")
    if not source.get("project_id") or not source.get("access_token"):
        raise ValueError("Apifox project_id 或访问令牌未配置")
    with _SYNC_LOCK:
        active = _active_sync(target)
        if active:
            result = dict(active)
            result.update({"created": False, "conflict": True})
            return result
        sync_id = unique_millis_id("api_sync")
        now = _now()
        record = {
            "sync_id": sync_id,
            "source_id": target,
            "trigger": str(trigger or "manual"),
            "status": "queued",
            "phase": "fetch_source",
            "poll_after_ms": POLL_AFTER_MS,
            "created_at": now,
            "updated_at": now,
            "started_at": "",
            "finished_at": "",
            "previous_revision_id": "",
            "asset_id": "",
            "revision_id": "",
            "diff_id": "",
            "summary": {"added": 0, "changed": 0, "removed": 0, "unchanged": 0, "affected_plans": 0},
            "sync_scope": api_source_service.normalized_sync_scope(source.get("sync_scope")),
            "module_count": 0,
            "scoped_module_count": 0,
            "scoped_endpoint_count": 0,
            "error": "",
            "events": [{"at": now, "phase": "fetch_source", "message": "同步已排队"}],
        }
        _write_sync(record)
        api_source_service.update_api_source_sync_state(
            target,
            last_sync_id=sync_id,
            last_attempt_at=now,
            last_sync_status="queued",
            last_error="",
        )
    if spawn:
        thread = threading.Thread(
            target=_run_api_source_sync_guarded,
            args=(sync_id, adapter),
            name=f"api-sync-{clean_id(sync_id, 'sync')}",
            daemon=True,
        )
        thread.start()
    result = dict(record)
    result.update({"created": True, "conflict": False})
    return result


def run_api_source_sync(sync_id: str, adapter: Any = None) -> Dict[str, Any]:
    record = get_api_sync(sync_id)
    if not record:
        raise ValueError("API sync 不存在")
    if record.get("status") in TERMINAL_SYNC_STATES:
        return record
    source = api_source_service.get_api_source(str(record.get("source_id") or ""), masked=False)
    if not source:
        return _update_sync(
            sync_id,
            status="failed",
            finished_at=_now(),
            error="API source 不存在",
            event="同步失败",
        )
    try:
        _update_sync(
            sync_id,
            status="running",
            phase="fetch_source",
            started_at=_now(),
            error="",
            event="开始读取 Apifox OpenAPI",
        )
        source_adapter = adapter or ApifoxSourceAdapter()
        fetched = source_adapter.fetch_openapi(source, timeout=30)
        full_document = fetched.get("document") or {}
        catalog = api_module_service.module_catalog(full_document)
        scope = api_source_service.normalized_sync_scope(source.get("sync_scope"))
        scoped_document = (
            full_document
            if scope["mode"] == "all"
            else api_module_service.filter_document(full_document, scope["module_paths"])
        )
        scope_fingerprint = api_module_service.scope_fingerprint(scope)
        scoped_catalog = api_module_service.module_catalog(scoped_document)
        _update_sync(sync_id, phase="parse_document", event="OpenAPI 已获取，正在解析")
        _update_sync(
            sync_id,
            sync_scope=scope,
            module_count=len(catalog),
            scoped_module_count=len(scoped_catalog),
            scoped_endpoint_count=sum(int(item.get("endpoint_count") or 0) for item in scoped_catalog),
        )
        staged = api_asset_service.stage_api_revision(
            source_id=str(source.get("source_id") or ""),
            source_name=str(source.get("name") or "Apifox 接口"),
            document=scoped_document,
            source_type="apifox",
            source_revision=str(fetched.get("source_revision") or ""),
            document_hash=str(fetched.get("document_hash") or ""),
            scope_fingerprint=scope_fingerprint,
            sync_scope=scope,
            module_catalog=catalog,
        )
        asset_id = str(staged.get("asset_id") or "")
        revision_id = str(staged.get("revision_id") or "")
        previous_revision_id = str(staged.get("previous_revision_id") or "")
        if staged.get("status") == "no_change":
            previous_revision_id = revision_id
        if staged.get("status") == "no_change":
            endpoint_count = int((staged.get("revision") or {}).get("endpoint_count") or 0)
            summary = {"added": 0, "changed": 0, "removed": 0, "unchanged": endpoint_count, "affected_plans": 0}
            finished_at = _now()
            result = _update_sync(
                sync_id,
                status="no_change",
                phase="analyze_impact",
                finished_at=finished_at,
                previous_revision_id=revision_id,
                asset_id=asset_id,
                revision_id=revision_id,
                summary=summary,
                event="内容未变化，继续使用当前版本",
            )
            api_source_service.update_api_source_sync_state(
                str(source.get("source_id") or ""),
                last_sync_id=sync_id,
                last_success_at=finished_at,
                last_sync_status="no_change",
                last_error="",
            )
            api_source_service.update_api_source_discovery_state(
                str(source.get("source_id") or ""), catalog, scope_fingerprint
            )
            return result
        revision = staged.get("revision") or {}
        previous_revision = api_asset_service.get_api_revision(previous_revision_id) if previous_revision_id else {}
        _update_sync(
            sync_id,
            phase="diff_revision",
            asset_id=asset_id,
            revision_id=revision_id,
            previous_revision_id=previous_revision_id,
            event="正在计算版本差异",
        )
        diff = api_schema_diff_service.compare_api_revisions(previous_revision, revision)
        _update_sync(sync_id, phase="persist_revision", event="不可变版本已持久化")
        _update_sync(sync_id, phase="analyze_impact", event="正在分析受影响计划")
        plans = api_test_plan_service.list_full_api_test_plans(limit=1000)
        impact = api_schema_diff_service.analyze_api_plan_impact(diff, plans)
        saved_diff = api_schema_diff_service.save_api_diff(asset_id, diff, impact)
        api_asset_service.activate_api_revision(asset_id, revision_id)
        summary = dict(diff.get("summary") or {})
        summary["affected_plans"] = int(impact.get("affected_plans") or 0)
        finished_at = _now()
        result = _update_sync(
            sync_id,
            status="succeeded",
            finished_at=finished_at,
            diff_id=str(saved_diff.get("diff_id") or ""),
            summary=summary,
            error="",
            event="同步完成并激活新版本",
        )
        api_source_service.update_api_source_sync_state(
            str(source.get("source_id") or ""),
            last_sync_id=sync_id,
            last_success_at=finished_at,
            last_sync_status="succeeded",
            last_error="",
        )
        api_source_service.update_api_source_discovery_state(
            str(source.get("source_id") or ""), catalog, scope_fingerprint
        )
        return result
    except Exception as exc:
        error = _redacted_error(exc, source)
        finished_at = _now()
        result = _update_sync(
            sync_id,
            status="failed",
            finished_at=finished_at,
            error=error,
            event="同步失败，保留上一活动版本",
        )
        api_source_service.update_api_source_sync_state(
            str(source.get("source_id") or ""),
            last_sync_id=sync_id,
            last_sync_status="failed",
            last_error=error,
        )
        return result


def _run_api_source_sync_guarded(sync_id: str, adapter: Any = None) -> None:
    try:
        run_api_source_sync(sync_id, adapter=adapter)
    except Exception as exc:
        record = get_api_sync(sync_id)
        if record and record.get("status") not in TERMINAL_SYNC_STATES:
            source_id = str(record.get("source_id") or "")
            source = api_source_service.get_api_source(source_id, masked=False)
            error = _redacted_error(f"同步线程异常：{exc}", source)
            _update_sync(
                sync_id,
                status="failed",
                finished_at=_now(),
                error=error,
                event="同步线程异常",
            )
            if source:
                api_source_service.update_api_source_sync_state(
                    source_id,
                    last_sync_id=sync_id,
                    last_sync_status="failed",
                    last_error=error,
                )


def recover_stale_api_syncs() -> List[str]:
    recovered: List[str] = []
    with _SYNC_LOCK:
        for record in list_api_syncs(limit=500):
            if record.get("status") not in ACTIVE_SYNC_STATES:
                continue
            sync_id = str(record.get("sync_id") or "")
            _update_sync(
                sync_id,
                status="failed",
                finished_at=_now(),
                error="服务重启时同步仍未完成，请重新同步",
                event="服务重启，遗留同步已终止",
            )
            source_id = str(record.get("source_id") or "")
            source = api_source_service.get_api_source(source_id, masked=False)
            if source:
                api_source_service.update_api_source_sync_state(
                    source_id,
                    last_sync_id=sync_id,
                    last_sync_status="failed",
                    last_error="服务重启时同步仍未完成，请重新同步",
                )
            recovered.append(sync_id)
    return recovered


def _timestamp(value: str) -> float:
    try:
        return time.mktime(time.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def due_api_source_ids(now: float | None = None) -> List[str]:
    current = time.time() if now is None else float(now)
    due: List[str] = []
    for source in api_source_service.list_api_sources():
        if source.get("source_type") != "apifox" or not source.get("sync_enabled") or not source.get("configured"):
            continue
        if _active_sync(str(source.get("source_id") or "")):
            continue
        last_success = _timestamp(str(source.get("last_success_at") or ""))
        last_attempt = _timestamp(str(source.get("last_attempt_at") or ""))
        last_reference = max(last_success, last_attempt)
        interval = max(15, int(source.get("sync_interval_minutes") or 60)) * 60
        if not last_reference or current - last_reference >= interval:
            due.append(str(source.get("source_id") or ""))
    return due


def schedule_due_api_sources() -> List[str]:
    started: List[str] = []
    for source_id in due_api_source_ids():
        try:
            sync = start_api_source_sync(source_id, spawn=True, trigger="schedule")
            if sync.get("created"):
                started.append(str(sync.get("sync_id") or ""))
        except Exception:
            continue
    return started


def _scheduler_loop() -> None:
    while True:
        schedule_due_api_sources()
        time.sleep(60)


def start_api_sync_scheduler() -> threading.Thread:
    global _SCHEDULER_THREAD
    with _SCHEDULER_LOCK:
        if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
            return _SCHEDULER_THREAD
        recover_stale_api_syncs()
        _SCHEDULER_THREAD = threading.Thread(
            target=_scheduler_loop,
            name="api-source-sync-scheduler",
            daemon=True,
        )
        _SCHEDULER_THREAD.start()
        return _SCHEDULER_THREAD


__all__ = [
    "ACTIVE_SYNC_STATES",
    "POLL_AFTER_MS",
    "SYNC_PHASES",
    "TERMINAL_SYNC_STATES",
    "due_api_source_ids",
    "get_api_sync",
    "list_api_syncs",
    "recover_stale_api_syncs",
    "run_api_source_sync",
    "schedule_due_api_sources",
    "start_api_source_sync",
    "start_api_sync_scheduler",
]
