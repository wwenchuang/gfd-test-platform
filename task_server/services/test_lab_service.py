"""SQLite-backed one-page API test lab.

The legacy API services already know how to read Apifox, generate cases and run
HTTP checks.  This module adds the product-facing persistence layer used by the
simple runner UI: local snapshots, environment overrides, cases, executions and
log/report summaries live in one small database.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List

from task_server.config import LEARNING_DIR, TASK_DIR, safe_int
from task_server.storage import clean_id, read_text_file, safe_join, unique_millis_id
from task_server.services import (
    api_asset_service,
    api_case_contract_service,
    api_execution_service,
    api_report_service,
    api_source_service,
    api_sync_service,
    api_test_plan_service,
    api_workspace_service,
    api_workbench_service,
)


TEST_LAB_DIR = os.getenv("TEST_LAB_DIR", safe_join(LEARNING_DIR, "test-lab"))
TEST_LAB_DB_PATH = os.getenv("TEST_LAB_DB_PATH", safe_join(TEST_LAB_DIR, "test_lab.sqlite3"))
MAX_API_ENDPOINTS_PER_AI_BATCH = 60
_DB_LOCK = threading.RLock()
_SENSITIVE_NAME_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "pwd",
    "authorization",
    "cookie",
    "session",
    "apikey",
    "api_key",
    "private",
)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_load(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return {} if default is None else default
    try:
        return json.loads(str(value))
    except Exception:
        return {} if default is None else default


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(TEST_LAB_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(TEST_LAB_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_test_lab_db() -> Dict[str, Any]:
    with _DB_LOCK, _db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS test_sources (
              source_id TEXT PRIMARY KEY,
              source_type TEXT NOT NULL,
              name TEXT NOT NULL,
              project_id TEXT,
              project_name TEXT,
              branch_id TEXT,
              branch_name TEXT,
              environment_id TEXT,
              environment_name TEXT,
              endpoint_count INTEGER DEFAULT 0,
              raw_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_sync_at TEXT
            );
            CREATE TABLE IF NOT EXISTS test_environments (
              env_key TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              name TEXT NOT NULL,
              base_url TEXT,
              variables_json TEXT NOT NULL,
              raw_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_endpoints (
              endpoint_id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              module_path TEXT NOT NULL,
              method TEXT NOT NULL,
              path TEXT NOT NULL,
              name TEXT NOT NULL,
              endpoint_key TEXT,
              schema_hash TEXT,
              content_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_cases (
              case_id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              endpoint_id TEXT,
              plan_id TEXT,
              module_path TEXT,
              name TEXT NOT NULL,
              priority TEXT,
              status TEXT,
              readiness TEXT,
              content_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_runs (
              execution_id TEXT PRIMARY KEY,
              source_id TEXT,
              plan_id TEXT,
              run_type TEXT,
              status TEXT,
              stats_json TEXT NOT NULL,
              content_json TEXT NOT NULL,
              created_at TEXT,
              updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ui_yaml_cases (
              yaml_id TEXT PRIMARY KEY,
              app_name TEXT,
              module_path TEXT,
              file_path TEXT NOT NULL UNIQUE,
              file_name TEXT NOT NULL,
              content_hash TEXT,
              content_text TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_test_endpoints_source_module
              ON test_endpoints(source_id, module_path);
            CREATE INDEX IF NOT EXISTS idx_test_cases_source_plan
              ON test_cases(source_id, plan_id);
            CREATE INDEX IF NOT EXISTS idx_test_runs_source_time
              ON test_runs(source_id, updated_at);
            """
        )
    return {"path": TEST_LAB_DB_PATH, "ready": True}


def _rows(query: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    init_test_lab_db()
    with _DB_LOCK, _db() as conn:
        return [dict(row) for row in conn.execute(query, tuple(params)).fetchall()]


def _one(query: str, params: Iterable[Any] = ()) -> Dict[str, Any]:
    rows = _rows(query, params)
    return rows[0] if rows else {}


def _is_sensitive_name(name: Any) -> bool:
    text = str(name or "").strip().lower()
    return any(fragment in text for fragment in _SENSITIVE_NAME_FRAGMENTS)


def _preview_value(name: str, value: Any, sensitive: bool = False) -> str:
    text = str(value or "")
    if not text:
        return ""
    if sensitive or _is_sensitive_name(name):
        return "已配置"
    return text if len(text) <= 120 else f"{text[:80]}...{text[-16:]}"


def _public_variable(row: Dict[str, Any]) -> Dict[str, Any]:
    name = str(row.get("name") or "").strip()
    value = row.get("value")
    sensitive = bool(row.get("sensitive") or _is_sensitive_name(name))
    configured = bool(row.get("configured")) or value not in (None, "")
    return {
        "name": name,
        "scope": str(row.get("scope") or "environment"),
        "configured": configured,
        "sensitive": sensitive,
        "value_preview": _preview_value(name, value, sensitive),
        "group_placeholder": bool(row.get("group_placeholder")),
        "note": str(row.get("note") or ""),
    }


def _is_internal_variable_name(name: str) -> bool:
    return str(name or "").strip().startswith("MTP_API_AUTH_")


def _source_label(source: Dict[str, Any]) -> str:
    metadata = source.get("provider_metadata") if isinstance(source.get("provider_metadata"), dict) else {}
    return (
        str(metadata.get("project_name") or "").strip()
        or str(source.get("name") or "").strip()
        or str(source.get("project_id") or "").strip()
        or str(source.get("source_id") or "").strip()
    )


def _select_existing_source(source_id: str = "") -> Dict[str, Any]:
    target = str(source_id or "").strip()
    if target:
        return api_source_service.get_api_source(target, masked=True)
    sources = api_source_service.list_api_sources()
    return sources[0] if sources else {}


def _active_revision_for_source(source_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    selected = str(source_id or "").strip()
    if not selected:
        return {}, {}
    for item in api_asset_service.list_api_assets(limit=1000):
        if str(item.get("source_id") or "").strip() != selected:
            continue
        asset = api_asset_service.get_api_asset(str(item.get("asset_id") or ""))
        revision_id = str(asset.get("active_revision_id") or "").strip()
        revision = api_asset_service.get_api_revision(revision_id) if revision_id else {}
        if revision:
            return asset, revision
    return {}, {}


def _source_from_revision(source: Dict[str, Any], revision: Dict[str, Any]) -> Dict[str, Any]:
    metadata = source.get("provider_metadata") if isinstance(source.get("provider_metadata"), dict) else {}
    return {
        "source_id": str(source.get("source_id") or ""),
        "source_type": str(source.get("source_type") or "apifox"),
        "name": _source_label(source) or "API 来源",
        "project_id": str(source.get("project_id") or ""),
        "project_name": str(metadata.get("project_name") or _source_label(source)),
        "branch_id": str(source.get("branch_id") or ""),
        "branch_name": str(metadata.get("branch_name") or ""),
        "environment_id": str(source.get("environment_id") or ""),
        "environment_name": str(metadata.get("environment_name") or ""),
        "endpoint_count": int(revision.get("endpoint_count") or len(revision.get("endpoints") or [])),
        "raw_json": api_case_contract_service.sanitize_sensitive_data(source),
        "created_at": str(source.get("created_at") or _now()),
        "updated_at": _now(),
        "last_sync_at": str(source.get("last_success_at") or revision.get("created_at") or ""),
    }


def _base_urls_from_source(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = source.get("environment_snapshot") if isinstance(source.get("environment_snapshot"), dict) else {}
    base_urls = snapshot.get("base_urls") if isinstance(snapshot.get("base_urls"), list) else []
    rows: List[Dict[str, Any]] = []
    metadata = source.get("provider_metadata") if isinstance(source.get("provider_metadata"), dict) else {}
    fallback_name = str(metadata.get("environment_name") or source.get("environment_id") or "默认环境").strip()
    for index, item in enumerate(base_urls, start=1):
        raw = item if isinstance(item, dict) else {}
        url = str(raw.get("url") or raw.get("value") or "").strip().rstrip("/")
        if not url:
            continue
        name = str(raw.get("display_name") or raw.get("displayName") or raw.get("name") or fallback_name or f"环境 {index}").strip()
        rows.append({
            "id": str(raw.get("id") or raw.get("name") or source.get("environment_id") or f"env-{index}").strip(),
            "name": name,
            "base_url": url,
        })
    if not rows and str(source.get("base_url") or "").startswith(("http://", "https://")):
        rows.append({"id": "default", "name": fallback_name or "默认环境", "base_url": str(source.get("base_url")).rstrip("/")})
    return rows


def _variables_from_source(source_id: str, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = source.get("environment_snapshot") if isinstance(source.get("environment_snapshot"), dict) else {}
    rows = snapshot.get("variables") if isinstance(snapshot.get("variables"), list) else []
    result: List[Dict[str, Any]] = []
    index_by_name: Dict[str, int] = {}
    for item in rows:
        raw = item if isinstance(item, dict) else {}
        name = str(raw.get("name") or "").strip()
        if not name or name in index_by_name:
            continue
        index_by_name[name] = len(result)
        result.append({
            "name": name,
            "scope": str(raw.get("scope") or "environment"),
            "value": str(raw.get("value") or ""),
            "sensitive": bool(raw.get("sensitive") or _is_sensitive_name(name)),
            "configured": bool(raw.get("configured")) or str(raw.get("value") or "") != "",
            "group_placeholder": bool(raw.get("group_placeholder")),
            "note": str(raw.get("note") or ""),
        })
    auth = api_workspace_service.get_api_auth_secret(source_id)
    if auth.get("configured"):
        header_name = str(auth.get("header_name") or "Authorization").strip()
        variable_name = str(auth.get("variable_name") or "").strip()
        for name, note, scope in (
            (header_name, "平台业务鉴权", "header"),
            (variable_name, "平台业务鉴权变量", "environment"),
        ):
            if not name:
                continue
            row = {
                "name": header_name,
                "scope": scope,
                "value": auth.get("secret") or "",
                "sensitive": True,
                "configured": True,
                "group_placeholder": False,
                "note": note,
            }
            row["name"] = name
            if name in index_by_name:
                result[index_by_name[name]].update(row)
            else:
                index_by_name[name] = len(result)
                result.append(row)
    return result


def _variables_from_openapi_document(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    paths = document.get("paths") if isinstance(document.get("paths"), dict) else {}
    variables: Dict[str, Dict[str, Any]] = {}
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        shared_parameters = path_item.get("parameters") if isinstance(path_item.get("parameters"), list) else []
        for operation_key, operation in path_item.items():
            if operation_key.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if not isinstance(operation, dict):
                continue
            parameters = list(shared_parameters)
            if isinstance(operation.get("parameters"), list):
                parameters.extend(operation.get("parameters") or [])
            for parameter in parameters:
                if not isinstance(parameter, dict):
                    continue
                name = str(parameter.get("name") or "").strip()
                location = str(parameter.get("in") or "environment").strip() or "environment"
                if not name or name in variables:
                    continue
                variables[name] = {
                    "name": name,
                    "scope": location,
                    "value": "",
                    "sensitive": _is_sensitive_name(name),
                    "configured": False,
                    "group_placeholder": False,
                    "note": "从 OpenAPI 参数自动发现",
                }
    return list(variables.values())


def _selected_auth_header_name(data: Dict[str, Any], source_id: str, variable_map: Dict[str, Dict[str, Any]]) -> str:
    explicit = str(
        data.get("auth_header_name")
        or data.get("authHeaderName")
        or data.get("header_name")
        or data.get("headerName")
        or ""
    ).strip()
    if explicit:
        return explicit
    current_auth = api_workspace_service.get_api_auth_binding(source_id)
    if current_auth.get("header_name"):
        return str(current_auth.get("header_name") or "").strip()
    if "ZXBToken" in variable_map:
        return "ZXBToken"
    return "Authorization"


def _auth_type_for_header(header_name: str) -> str:
    return "bearer" if str(header_name or "").strip().casefold() == "authorization" else "api_key"


def _endpoint_public(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    method = str(endpoint.get("method") or "").upper()
    path = str(endpoint.get("path") or "")
    return {
        "endpoint_id": str(endpoint.get("endpoint_id") or ""),
        "endpoint_key": str(endpoint.get("endpoint_key") or ""),
        "method": method,
        "path": path,
        "name": str(endpoint.get("name") or endpoint.get("summary") or path),
        "summary": str(endpoint.get("summary") or endpoint.get("name") or ""),
        "module_path": str(endpoint.get("module_path") or endpoint.get("module") or "未分组"),
        "module": str(endpoint.get("module") or endpoint.get("module_path") or "未分组"),
        "required_fields": endpoint.get("required_fields") if isinstance(endpoint.get("required_fields"), list) else [],
        "requires_auth": api_case_contract_service.endpoint_requires_auth(endpoint),
        "schema_hash": str(endpoint.get("schema_hash") or ""),
    }


def _upsert_source(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO test_sources (
          source_id, source_type, name, project_id, project_name, branch_id,
          branch_name, environment_id, environment_name, endpoint_count,
          raw_json, created_at, updated_at, last_sync_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          source_type=excluded.source_type,
          name=excluded.name,
          project_id=excluded.project_id,
          project_name=excluded.project_name,
          branch_id=excluded.branch_id,
          branch_name=excluded.branch_name,
          environment_id=excluded.environment_id,
          environment_name=excluded.environment_name,
          endpoint_count=excluded.endpoint_count,
          raw_json=excluded.raw_json,
          updated_at=excluded.updated_at,
          last_sync_at=excluded.last_sync_at
        """,
        (
            row["source_id"],
            row["source_type"],
            row["name"],
            row.get("project_id", ""),
            row.get("project_name", ""),
            row.get("branch_id", ""),
            row.get("branch_name", ""),
            row.get("environment_id", ""),
            row.get("environment_name", ""),
            int(row.get("endpoint_count") or 0),
            _json_dump(row.get("raw_json") or {}),
            row.get("created_at") or _now(),
            row.get("updated_at") or _now(),
            row.get("last_sync_at") or "",
        ),
    )


def _upsert_environment(conn: sqlite3.Connection, source_id: str, source: Dict[str, Any]) -> None:
    base_urls = _base_urls_from_source(source)
    variables = _variables_from_source(source_id, source)
    if not base_urls:
        base_urls = [{"id": str(source.get("environment_id") or "default"), "name": "默认环境", "base_url": ""}]
    for item in base_urls:
        env_key = f"{source_id}:{item.get('id') or item.get('name') or 'default'}"
        conn.execute(
            """
            INSERT INTO test_environments (
              env_key, source_id, name, base_url, variables_json, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(env_key) DO UPDATE SET
              name=excluded.name,
              base_url=excluded.base_url,
              variables_json=excluded.variables_json,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (
                env_key,
                source_id,
                str(item.get("name") or item.get("id") or "默认环境"),
                str(item.get("base_url") or ""),
                _json_dump(variables),
                _json_dump({"base_url": item, "variables": api_case_contract_service.sanitize_sensitive_data(variables)}),
                _now(),
            ),
        )


def _upsert_endpoint(conn: sqlite3.Connection, source_id: str, endpoint: Dict[str, Any]) -> None:
    public = _endpoint_public(endpoint)
    endpoint_id = public["endpoint_id"] or clean_id(f"{public['method']}_{public['path']}", "api_endpoint")
    conn.execute(
        """
        INSERT INTO test_endpoints (
          endpoint_id, source_id, module_path, method, path, name,
          endpoint_key, schema_hash, content_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(endpoint_id) DO UPDATE SET
          source_id=excluded.source_id,
          module_path=excluded.module_path,
          method=excluded.method,
          path=excluded.path,
          name=excluded.name,
          endpoint_key=excluded.endpoint_key,
          schema_hash=excluded.schema_hash,
          content_json=excluded.content_json,
          updated_at=excluded.updated_at
        """,
        (
            endpoint_id,
            source_id,
            public["module_path"],
            public["method"],
            public["path"],
            public["name"],
            public["endpoint_key"],
            public["schema_hash"],
            _json_dump(public),
            _now(),
        ),
    )


def _upsert_plan_cases(conn: sqlite3.Connection, source_id: str, plan: Dict[str, Any]) -> None:
    plan_id = str(plan.get("plan_id") or "").strip()
    if not plan_id:
        return
    for item in plan.get("cases") or []:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or "").strip()
        if not case_id:
            continue
        readiness = item.get("readiness") if isinstance(item.get("readiness"), dict) else {}
        conn.execute(
            """
            INSERT INTO test_cases (
              case_id, source_id, endpoint_id, plan_id, module_path, name,
              priority, status, readiness, content_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
              source_id=excluded.source_id,
              endpoint_id=excluded.endpoint_id,
              plan_id=excluded.plan_id,
              module_path=excluded.module_path,
              name=excluded.name,
              priority=excluded.priority,
              status=excluded.status,
              readiness=excluded.readiness,
              content_json=excluded.content_json,
              updated_at=excluded.updated_at
            """,
            (
                case_id,
                source_id,
                str(item.get("endpoint_id") or ""),
                plan_id,
                str(item.get("module") or ""),
                str(item.get("name") or case_id),
                str(item.get("priority") or ""),
                str(item.get("status") or plan.get("status") or "draft"),
                str(readiness.get("state") or ""),
                _json_dump(api_case_contract_service.sanitize_sensitive_data(item)),
                _now(),
            ),
        )


def _upsert_execution(conn: sqlite3.Connection, execution: Dict[str, Any]) -> None:
    execution_id = str(execution.get("execution_id") or "").strip()
    if not execution_id:
        return
    conn.execute(
        """
        INSERT INTO test_runs (
          execution_id, source_id, plan_id, run_type, status, stats_json,
          content_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(execution_id) DO UPDATE SET
          source_id=excluded.source_id,
          plan_id=excluded.plan_id,
          run_type=excluded.run_type,
          status=excluded.status,
          stats_json=excluded.stats_json,
          content_json=excluded.content_json,
          updated_at=excluded.updated_at
        """,
        (
            execution_id,
            str(execution.get("source_id") or ""),
            str(execution.get("plan_id") or ""),
            str(execution.get("run_mode") or execution.get("run_type") or ""),
            str(execution.get("status") or ""),
            _json_dump(execution.get("stats") or {}),
            _json_dump(api_case_contract_service.sanitize_sensitive_data(execution)),
            str(execution.get("created_at") or _now()),
            str(execution.get("updated_at") or _now()),
        ),
    )


def mirror_existing_api_data(source_id: str = "") -> Dict[str, Any]:
    """Copy current Apifox/API-service data into the SQLite test lab."""
    init_test_lab_db()
    source = _select_existing_source(source_id)
    if not source:
        return {"ok": True, "source": {}, "endpoint_count": 0}
    selected_source_id = str(source.get("source_id") or "").strip()
    asset, revision = _active_revision_for_source(selected_source_id)
    endpoints = revision.get("endpoints") if isinstance(revision.get("endpoints"), list) else []
    raw_source = api_source_service.get_api_source(selected_source_id, masked=False) or source
    source_row = _source_from_revision(source, revision or {"endpoints": endpoints})
    with _DB_LOCK, _db() as conn:
        _upsert_source(conn, source_row)
        _upsert_environment(conn, selected_source_id, raw_source)
        conn.execute("DELETE FROM test_endpoints WHERE source_id=?", (selected_source_id,))
        for endpoint in endpoints:
            if isinstance(endpoint, dict):
                _upsert_endpoint(conn, selected_source_id, endpoint)
        for plan in api_test_plan_service.list_full_api_test_plans(limit=1000, source_id=selected_source_id):
            _upsert_plan_cases(conn, selected_source_id, plan)
        for row in api_execution_service.list_api_executions(limit=200, source_id=selected_source_id):
            try:
                execution = api_execution_service.get_api_execution(str(row.get("execution_id") or ""))
            except Exception:
                execution = row
            if isinstance(execution, dict):
                _upsert_execution(conn, execution)
    return {
        "ok": True,
        "source_id": selected_source_id,
        "asset_id": str(asset.get("asset_id") or ""),
        "revision_id": str(revision.get("revision_id") or revision.get("snapshot_id") or ""),
        "endpoint_count": len(endpoints),
    }


def _db_sources() -> List[Dict[str, Any]]:
    rows = _rows("SELECT * FROM test_sources ORDER BY updated_at DESC")
    return [
        {
            "source_id": row["source_id"],
            "source_type": row["source_type"],
            "name": row["name"],
            "project_id": row.get("project_id") or "",
            "project_name": row.get("project_name") or row["name"],
            "branch_id": row.get("branch_id") or "",
            "branch_name": row.get("branch_name") or "",
            "environment_id": row.get("environment_id") or "",
            "environment_name": row.get("environment_name") or "",
            "endpoint_count": int(row.get("endpoint_count") or 0),
            "last_sync_at": row.get("last_sync_at") or "",
            "updated_at": row.get("updated_at") or "",
        }
        for row in rows
    ]


def _environment(source_id: str) -> Dict[str, Any]:
    rows = _rows(
        "SELECT * FROM test_environments WHERE source_id=? ORDER BY updated_at DESC",
        (source_id,),
    )
    if not rows:
        return {"base_urls": [], "variables": [], "auth": {}}
    base_urls = []
    all_variables: List[Dict[str, Any]] = []
    seen_vars = set()
    for row in rows:
        base_urls.append({
            "env_key": row["env_key"],
            "name": row["name"],
            "base_url": row.get("base_url") or "",
        })
        for variable in _json_load(row.get("variables_json"), []):
            if not isinstance(variable, dict):
                continue
            name = str(variable.get("name") or "").strip()
            if not name or name in seen_vars or _is_internal_variable_name(name):
                continue
            seen_vars.add(name)
            all_variables.append(_public_variable(variable))
    auth = api_workspace_service.get_api_auth_binding(source_id)
    return {
        "base_urls": base_urls,
        "variables": all_variables,
        "auth": auth,
    }


def _endpoints(source_id: str, module_path: str = "") -> List[Dict[str, Any]]:
    if module_path:
        rows = _rows(
            "SELECT * FROM test_endpoints WHERE source_id=? AND module_path=? ORDER BY path, method",
            (source_id, module_path),
        )
    else:
        rows = _rows(
            "SELECT * FROM test_endpoints WHERE source_id=? ORDER BY module_path, path, method",
            (source_id,),
        )
    return [
        {
            **_json_load(row.get("content_json"), {}),
            "endpoint_id": row["endpoint_id"],
            "source_id": row["source_id"],
            "module_path": row["module_path"],
            "method": row["method"],
            "path": row["path"],
            "name": row["name"],
        }
        for row in rows
    ]


def _latest_plan_for_module(source_id: str, module_path: str) -> Dict[str, Any]:
    normalized = str(module_path or "").strip()
    for plan in api_test_plan_service.list_full_api_test_plans(limit=1000, source_id=source_id):
        paths = [str(item or "").strip() for item in (plan.get("module_paths") or [])]
        if normalized and normalized not in paths:
            continue
        return plan
    return {}


def _modules(source_id: str) -> List[Dict[str, Any]]:
    rows = _rows(
        """
        SELECT module_path, COUNT(*) AS endpoint_count
        FROM test_endpoints
        WHERE source_id=?
        GROUP BY module_path
        ORDER BY endpoint_count DESC, module_path
        """,
        (source_id,),
    )
    modules = []
    for row in rows:
        module_path = str(row.get("module_path") or "未分组")
        plan = _latest_plan_for_module(source_id, module_path)
        readiness = plan.get("execution_readiness") if isinstance(plan.get("execution_readiness"), dict) else {}
        modules.append({
            "module_path": module_path,
            "name": module_path.split("/")[-1] if module_path else "未分组",
            "endpoint_count": int(row.get("endpoint_count") or 0),
            "plan_id": str(plan.get("plan_id") or ""),
            "plan_status": str(plan.get("status") or ""),
            "case_count": int(plan.get("case_count") or 0),
            "executable_case_count": int(readiness.get("executable_case_count") or plan.get("executable_case_count") or 0),
            "needs_review_case_count": int(readiness.get("needs_review_case_count") or plan.get("needs_review_case_count") or 0),
            "action": "执行调试" if plan else "AI 生成用例",
        })
    return modules


def _runs(source_id: str) -> List[Dict[str, Any]]:
    rows = _rows(
        "SELECT * FROM test_runs WHERE source_id=? ORDER BY updated_at DESC LIMIT 20",
        (source_id,),
    )
    return [
        {
            "execution_id": row["execution_id"],
            "plan_id": row.get("plan_id") or "",
            "run_type": row.get("run_type") or "",
            "status": row.get("status") or "",
            "stats": _json_load(row.get("stats_json"), {}),
            "created_at": row.get("created_at") or "",
            "updated_at": row.get("updated_at") or "",
        }
        for row in rows
    ]


def _run_detail(execution_id: str) -> Dict[str, Any]:
    target = str(execution_id or "").strip()
    if not target:
        rows = _rows("SELECT execution_id FROM test_runs ORDER BY updated_at DESC LIMIT 1")
        target = str(rows[0].get("execution_id") or "") if rows else ""
    if not target:
        return {}
    try:
        execution = api_execution_service.get_api_execution(target)
    except Exception:
        row = _one("SELECT content_json FROM test_runs WHERE execution_id=?", (target,))
        execution = _json_load(row.get("content_json"), {}) if row else {}
    if execution:
        with _DB_LOCK, _db() as conn:
            _upsert_execution(conn, execution)
    return api_case_contract_service.sanitize_sensitive_data(execution)


def _reports(source_id: str) -> List[Dict[str, Any]]:
    return api_report_service.list_api_reports(limit=20, source_id=source_id)


def api_lab_state(source_id: str = "", module_path: str = "", execution_id: str = "") -> Dict[str, Any]:
    init_test_lab_db()
    sources = _db_sources()
    if not sources:
        mirror_existing_api_data(source_id)
        sources = _db_sources()
    selected_source_id = str(source_id or (sources[0]["source_id"] if sources else "")).strip()
    if selected_source_id and not any(item["source_id"] == selected_source_id for item in sources):
        mirror_existing_api_data(selected_source_id)
        sources = _db_sources()
    source = next((item for item in sources if item["source_id"] == selected_source_id), sources[0] if sources else {})
    modules = _modules(selected_source_id) if selected_source_id else []
    selected_module = str(module_path or "").strip()
    if selected_module and not any(item["module_path"] == selected_module for item in modules):
        selected_module = ""
    if not selected_module and modules:
        selected_module = modules[0]["module_path"]
    endpoints = _endpoints(selected_source_id, selected_module) if selected_source_id else []
    latest_run = _run_detail(execution_id) if execution_id else _run_detail("")
    return {
        "ok": True,
        "mode": "test_lab",
        "db": init_test_lab_db(),
        "source": source,
        "sources": sources,
        "environment": _environment(selected_source_id) if selected_source_id else {"base_urls": [], "variables": [], "auth": {}},
        "modules": modules,
        "selected_module_path": selected_module,
        "endpoints": endpoints[:MAX_API_ENDPOINTS_PER_AI_BATCH],
        "endpoint_count": len(_endpoints(selected_source_id)) if selected_source_id else 0,
        "selected_endpoint_count": len(endpoints),
        "runs": _runs(selected_source_id) if selected_source_id else [],
        "latest_run": latest_run,
        "reports": _reports(selected_source_id) if selected_source_id else [],
        "limits": {"max_ai_endpoint_count": MAX_API_ENDPOINTS_PER_AI_BATCH},
    }


def refresh_apifox_to_test_lab(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    source_id = str(data.get("source_id") or data.get("sourceId") or "").strip()
    source_payload = {
        "source_id": source_id,
        "source_type": "apifox",
        "name": str(data.get("name") or "Apifox 接口").strip(),
        "base_url": str(data.get("base_url") or data.get("baseUrl") or "https://api.apifox.com").strip(),
        "project_id": str(data.get("project_id") or data.get("projectId") or "").strip(),
        "branch_id": str(data.get("branch_id") or data.get("branchId") or "").strip(),
        "environment_id": str(data.get("environment_id") or data.get("environmentId") or "").strip(),
        "access_token": str(data.get("access_token") or data.get("accessToken") or data.get("token") or "").strip(),
        "sync_enabled": False,
        "preserve_missing_environment_variables": True,
    }
    if source_payload["access_token"]:
        api_source_service.save_apifox_credential(source_payload)
    if not source_payload["access_token"]:
        source_payload = api_source_service.apply_saved_apifox_credential(source_payload)
    current_source = api_source_service.get_api_source(source_id, masked=False) if source_id else {}
    if current_source:
        for key in ("name", "project_id", "branch_id", "environment_id"):
            if not source_payload.get(key):
                source_payload[key] = current_source.get(key) or source_payload[key]
    source = api_source_service.save_api_source(source_payload)
    selected_source_id = str(source.get("source_id") or source_id).strip()
    environment_warning = ""
    try:
        api_workbench_service.refresh_apifox_environment_snapshot(selected_source_id, force=True)
    except Exception as exc:
        environment_warning = str(exc)
    sync = api_sync_service.start_api_source_sync(
        selected_source_id,
        spawn=False,
        trigger="test_lab_manual_refresh",
    )
    mirror = mirror_existing_api_data(selected_source_id)
    state = api_lab_state(selected_source_id)
    return {
        "ok": True,
        "source": source,
        "sync": sync,
        "mirror": mirror,
        "environment_warning": environment_warning,
        "state": state,
    }


def import_openapi_to_test_lab(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    document = data.get("document") or data.get("openapi") or data.get("content") or data.get("raw") or {}
    if isinstance(document, str):
        document = json.loads(document)
    if not isinstance(document, dict):
        raise ValueError("OpenAPI 内容必须是 JSON 对象")
    source_id = str(data.get("source_id") or data.get("sourceId") or unique_millis_id("api_source_openapi")).strip()
    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    name = str(data.get("name") or info.get("title") or "OpenAPI 导入").strip()
    base_url = str(data.get("base_url") or data.get("baseUrl") or "").strip().rstrip("/")
    api_source_service.save_api_source({
        "source_id": source_id,
        "source_type": "openapi_upload",
        "name": name,
        "base_url": "https://api.apifox.com",
        "environment_snapshot": {
            "base_urls": [{"name": "本地环境", "url": base_url}] if base_url else [],
            "variables": _variables_from_openapi_document(document),
        },
        "sync_enabled": False,
    })
    staged = api_asset_service.stage_api_revision(
        source_id=source_id,
        source_name=name,
        document=document,
        source_type="openapi_upload",
        source_revision="manual",
    )
    api_asset_service.activate_api_revision(
        str(staged.get("asset_id") or ""),
        str(staged.get("revision_id") or ""),
    )
    mirror = mirror_existing_api_data(source_id)
    return {"ok": True, "mirror": mirror, "state": api_lab_state(source_id)}


def save_environment(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    source_id = str(data.get("source_id") or data.get("sourceId") or "").strip()
    if not source_id:
        raise ValueError("请选择接口来源")
    source = api_source_service.get_api_source(source_id, masked=False)
    if not source:
        raise ValueError("接口来源不存在")
    snapshot = source.get("environment_snapshot") if isinstance(source.get("environment_snapshot"), dict) else {}
    base_urls = snapshot.get("base_urls") if isinstance(snapshot.get("base_urls"), list) else []
    base_url = str(data.get("base_url") or data.get("baseUrl") or "").strip().rstrip("/")
    if base_url:
        if base_urls:
            base_urls[0] = {**(base_urls[0] if isinstance(base_urls[0], dict) else {}), "name": base_urls[0].get("name") or "default", "url": base_url}
        else:
            base_urls = [{"name": "default", "url": base_url}]
    variables = snapshot.get("variables") if isinstance(snapshot.get("variables"), list) else []
    variable_map: Dict[str, Dict[str, Any]] = {
        str((item if isinstance(item, dict) else {}).get("name") or "").strip(): dict(item)
        for item in variables
        if str((item if isinstance(item, dict) else {}).get("name") or "").strip()
    }
    for item in data.get("variables") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        current = variable_map.get(name, {"name": name, "scope": item.get("scope") or "environment"})
        value = str(item.get("value") if item.get("value") is not None else item.get("localValue") or "")
        sensitive = bool(item.get("sensitive") or current.get("sensitive") or _is_sensitive_name(name))
        if not value and sensitive and current.get("configured"):
            variable_map[name] = current
            continue
        current.update({
            "name": name,
            "value": value,
            "sensitive": sensitive,
            "configured": value != "",
            "group_placeholder": False,
            "note": str(item.get("note") or current.get("note") or "本地环境变量"),
        })
        variable_map[name] = current
    biz = str(data.get("biz") or data.get("Biz") or "").strip()
    if biz:
        variable_map["Biz"] = {"name": "Biz", "value": biz, "scope": "header", "sensitive": False, "configured": True}
    auth_header_name = _selected_auth_header_name(data, source_id, variable_map)
    auth_type = _auth_type_for_header(auth_header_name)
    business_token = str(data.get("business_token") or data.get("businessToken") or data.get("token") or "").strip()
    current_secret = api_workspace_service.get_api_auth_secret(source_id)
    token_to_bind = business_token or str(current_secret.get("secret") or "").strip()
    if token_to_bind:
        variable_map[auth_header_name] = {
            "name": auth_header_name,
            "value": token_to_bind if auth_type != "bearer" or token_to_bind.lower().startswith("bearer ") else f"Bearer {token_to_bind}",
            "scope": "header",
            "sensitive": True,
            "configured": True,
            "group_placeholder": False,
            "note": "平台业务鉴权",
        }
    environment_snapshot = {
        **snapshot,
        "base_urls": base_urls,
        "variables": list(variable_map.values()),
    }
    saved_source = api_source_service.save_api_source({
        **source,
        "environment_snapshot": environment_snapshot,
        "preserve_missing_environment_variables": True,
        "sync_enabled": False,
    })
    metadata = source.get("provider_metadata") if isinstance(source.get("provider_metadata"), dict) else {}
    api_workspace_service.save_api_workspace_binding(
        source_id,
        str(source.get("project_id") or source_id),
        str(source.get("environment_id") or (base_urls[0].get("name") if base_urls else "default")),
        project_name=str(metadata.get("project_name") or source.get("name") or source_id),
        environment_name=str(metadata.get("environment_name") or source.get("environment_id") or "默认环境"),
        connection_identity="platform-native-api",
    )
    if token_to_bind:
        api_execution_service.save_api_auth_binding(source_id, auth_type, auth_header_name, token_to_bind)
    mirror_existing_api_data(source_id)
    return {"ok": True, "source": saved_source, "state": api_lab_state(source_id)}


def _selected_endpoint_ids(source_id: str, module_path: str, endpoint_ids: List[str] | None) -> List[str]:
    requested = [str(item or "").strip() for item in (endpoint_ids or []) if str(item or "").strip()]
    if requested:
        return requested[:MAX_API_ENDPOINTS_PER_AI_BATCH]
    return [
        endpoint["endpoint_id"]
        for endpoint in _endpoints(source_id, module_path)
        if endpoint.get("endpoint_id")
    ][:MAX_API_ENDPOINTS_PER_AI_BATCH]


def generate_cases(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    source_id = str(data.get("source_id") or data.get("sourceId") or "").strip()
    module_path = str(data.get("module_path") or data.get("modulePath") or "").strip()
    if not source_id:
        raise ValueError("请选择接口来源")
    _, revision = _active_revision_for_source(source_id)
    snapshot_id = str(revision.get("revision_id") or revision.get("snapshot_id") or "").strip()
    if not snapshot_id:
        raise ValueError("请先从 Apifox 更新并保存接口数据")
    endpoint_ids = _selected_endpoint_ids(source_id, module_path, data.get("endpoint_ids") or data.get("endpointIds"))
    if not endpoint_ids:
        raise ValueError("请选择要测试的接口")
    plan = api_test_plan_service.generate_api_test_plan(
        snapshot_id,
        endpoint_ids,
        use_ai=_bool_value(
            data.get("use_ai", data.get("useAi")),
            _bool_value(os.getenv("API_TESTING_AI_ENABLED"), True),
        ),
        source_id=source_id,
        module_paths=[module_path] if module_path else [],
        require_ai_success=False,
    )
    with _DB_LOCK, _db() as conn:
        _upsert_plan_cases(conn, source_id, plan)
    return {"ok": True, "plan": plan, "state": api_lab_state(source_id, module_path)}


def run_cases(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    source_id = str(data.get("source_id") or data.get("sourceId") or "").strip()
    module_path = str(data.get("module_path") or data.get("modulePath") or "").strip()
    plan_id = str(data.get("plan_id") or data.get("planId") or "").strip()
    if not source_id:
        raise ValueError("请选择接口来源")
    if not plan_id:
        plan = _latest_plan_for_module(source_id, module_path)
        plan_id = str(plan.get("plan_id") or "")
    if not plan_id:
        raise ValueError("请先让 AI 生成测试用例")
    plan = api_test_plan_service.get_api_test_plan(plan_id, source_id=source_id)
    if not plan:
        raise ValueError("API 测试用例不存在")
    if plan.get("status") == "confirmed":
        execution = api_execution_service.start_api_execution(plan_id)
    else:
        execution = api_execution_service.start_api_cases_debug(
            plan_id,
            data.get("case_ids") or data.get("caseIds") or [],
            spawn=True,
        )
    with _DB_LOCK, _db() as conn:
        _upsert_execution(conn, execution)
    return {"ok": True, "execution": execution, "state": api_lab_state(source_id, module_path, execution.get("execution_id"))}


def execution_detail(execution_id: str) -> Dict[str, Any]:
    execution = _run_detail(execution_id)
    source_id = str(execution.get("source_id") or "")
    return {
        "ok": True,
        "execution": execution,
        "state": api_lab_state(source_id, execution_id=str(execution.get("execution_id") or "")) if source_id else api_lab_state(),
    }


def sync_ui_yaml_index(root_dir: str = "") -> Dict[str, Any]:
    """Index UI automation YAML files into the same database for later reuse."""
    base = os.path.abspath(root_dir or TASK_DIR)
    if not os.path.isdir(base):
        return {"ok": True, "indexed": 0, "root": base}
    indexed = 0
    init_test_lab_db()
    with _DB_LOCK, _db() as conn:
        for current, _, names in os.walk(base):
            for name in names:
                if not name.endswith((".yaml", ".yml")) or name.startswith("."):
                    continue
                path = os.path.join(current, name)
                rel = os.path.relpath(path, base)
                content = read_text_file(path, "")
                yaml_id = clean_id(f"ui_yaml_{rel}", "ui_yaml")
                module_path = os.path.dirname(rel).replace(os.sep, "/")
                conn.execute(
                    """
                    INSERT INTO ui_yaml_cases (
                      yaml_id, app_name, module_path, file_path, file_name,
                      content_hash, content_text, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                      module_path=excluded.module_path,
                      file_name=excluded.file_name,
                      content_hash=excluded.content_hash,
                      content_text=excluded.content_text,
                      updated_at=excluded.updated_at
                    """,
                    (
                        yaml_id,
                        module_path.split("/")[0] if module_path else "",
                        module_path,
                        path,
                        name,
                        hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        content,
                        _now(),
                    ),
                )
                indexed += 1
    return {"ok": True, "indexed": indexed, "root": base}


__all__ = [
    "TEST_LAB_DB_PATH",
    "api_lab_state",
    "execution_detail",
    "generate_cases",
    "import_openapi_to_test_lab",
    "init_test_lab_db",
    "mirror_existing_api_data",
    "refresh_apifox_to_test_lab",
    "run_cases",
    "save_environment",
    "sync_ui_yaml_index",
]
