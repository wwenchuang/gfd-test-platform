"""Safe HTTP operation snapshots and bearer-scoped history."""

import hashlib
import logging
import math
import re
import threading
import time
import urllib.parse
import uuid

from . import auth


_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_STORES = {}
_LAST_REPLAY = {}
_AUDIT = {"storage": "unknown", "stored": True, "last_replay": 0.0, "last_failure": 0.0}
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_RESOURCE_KEYS = ("id", "job_id", "run_id", "session_id", "recording_id", "version_id", "file_id", "project_id", "environment_id")
_ALIASES = {"auth": "account", "api-testing": "api", "device-recordings": "recording", "test-reports": "report", "reports": "report", "assets": "asset", "cases": "case", "jobs": "job", "runner": "runner", "sonic": "sonic", "agent-runs": "agent_run", "file": "file", "files": "file", "modules": "module", "apps": "app", "tasks": "task", "knowledge": "knowledge", "repair-drafts": "repair_draft", "runners": "runner", "ui": "ui", "figma": "figma", "yaml": "yaml", "model-config": "model_config", "preflight": "preflight", "run-request": "job", "test-runs": "test_run"}
_ROUTE_VERBS = {"cancel", "retry", "run", "execute", "start", "stop", "upload", "download", "preview", "finish", "replay", "generate", "approve", "reject", "apply", "refresh", "submit", "save", "delete", "reset", "rename"}
_FIXED_ACTIONS = {
    ("POST", "/api/ui/generate-yaml"): "yaml.generate",
    ("POST", "/api/figma/parse-async"): "figma.parse_async",
    ("POST", "/api/figma/parse"): "figma.parse",
    ("POST", "/api/run-request"): "job.submit",
    ("POST", "/api/model-config"): "model_config.update",
    ("GET", "/api/model-config"): "model_config.view",
}


def _store():
    from .services.operation_attribution import OperationStore, default_db_path
    path = str(default_db_path())
    with _LOCK:
        if path not in _STORES:
            _STORES[path] = OperationStore(path)
        return _STORES[path]


def _safe_id(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else ""


def safe_resource_ref(value):
    """Stable audit reference for a validated resource name; never stores raw Unicode."""
    if not isinstance(value, str) or not value or len(value) > 512:
        return ""
    return _safe_id(value) or "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _actor_for_request(handler):
    from .services.operation_attribution import resolve_request_actor
    path = urllib.parse.urlsplit(getattr(handler, "path", "")).path
    from .access_control import MACHINE_ROUTES, MACHINE_READ_ROUTES
    method = getattr(handler, "command", "")
    machine_only = (method, path) in MACHINE_ROUTES or method == "POST" and path.startswith("/api/runner/jobs/")
    machine_read = method in {"GET", "HEAD"} and path in MACHINE_READ_ROUTES
    if not machine_only and not machine_read:
        bearer = {"Authorization": handler.headers.get("Authorization", "")}
        human = resolve_request_actor(bearer)
        if human["kind"] == "user" or path.startswith("/api/auth/") or path == "/api/operations":
            return human
    return resolve_request_actor(handler.headers)


def start_request(handler):
    handler.__dict__.pop("path", None)
    handler.__dict__.pop("command", None)
    handler.__dict__.pop("_parsed_body", None)
    handler.__dict__.pop("_main_access", None)
    handler._operation_id = uuid.uuid4().hex
    handler._operation_started = time.monotonic()
    handler._operation_status = 0
    handler._operation_headers_sent = False
    handler._operation_payload = None
    handler._operation_interrupted = False
    handler._operation_actor = None
    handler._operation_context = None
    handler._operation_initiator = ""
    handler._operation_resource_id = ""
    handler._operation_resource_type = ""


def parsed_request(handler):
    from .services.operation_attribution import actor_context
    try:
        handler._operation_actor = _actor_for_request(handler)
    except Exception:
        _LOG.warning("operation actor resolution unavailable request_id=%s", handler._operation_id)
        handler._operation_actor = {"kind": "anonymous"}
    handler._operation_context = actor_context(handler._operation_actor, handler._operation_id)
    handler._operation_context.__enter__()


def mark_authenticated_actor(handler, actor, *, initiator_username="", resource_id=""):
    """Bind a subject only after a route's own credential check succeeds."""
    if not hasattr(handler, "_operation_id"):
        return
    from .services.operation_attribution import actor_context, actor_from_trusted_username
    prior = getattr(handler, "_operation_context", None)
    if prior is not None:
        prior.__exit__(None, None, None)
    handler._operation_actor = actor
    handler._operation_context = actor_context(actor, handler._operation_id)
    handler._operation_context.__enter__()
    if initiator_username:
        try:
            trusted = actor_from_trusted_username(initiator_username)
            handler._operation_initiator = trusted["user_id"] if trusted["kind"] == "user" else ""
        except Exception:
            _LOG.warning("operation initiator lookup unavailable request_id=%s", handler._operation_id)
    handler._operation_resource_id = safe_resource_ref(str(resource_id))


def mark_authenticated_username(handler, username):
    if hasattr(handler, "_operation_id"):
        from .services.operation_attribution import actor_from_trusted_username
        try:
            mark_authenticated_actor(handler, actor_from_trusted_username(username))
        except Exception:
            _LOG.warning("operation actor lookup unavailable request_id=%s", handler._operation_id)


def mark_resource(handler, resource_type, resource_id):
    """Bind a resource after a route validates it, without changing the actor."""
    if hasattr(handler, "_operation_id"):
        handler._operation_resource_type = _safe_id(resource_type)
        handler._operation_resource_id = safe_resource_ref(resource_id)


def observed_json(handler, payload):
    if not isinstance(payload, dict):
        return
    handler._operation_payload = payload
    path = urllib.parse.urlsplit(getattr(handler, "path", "")).path
    if path == "/api/auth/login" and payload.get("ok") is True:
        profile = payload.get("profile")
        if isinstance(profile, dict) and profile.get("user_id"):
            handler._operation_actor = {"kind": "user", "user_id": profile["user_id"],
                                        "username": profile.get("username", ""),
                                        "display_name": profile.get("display_name", "")}


def _resource(path, method, payload, qs, status, parsed_body=None):
    parts = [part for part in path.split("/") if part]
    if not parts:
        return "", "", ""
    if parts[0] == "report":
        kind, tail = "report", []
    elif parts[0] == "api":
        kind = _ALIASES.get(parts[1], "api") if len(parts) > 1 else "api"
        tail = parts[2:]
    else:
        return "", "", ""
    kind = _safe_id(kind) or "api"
    resource_id = ""
    if kind == "api":
        api_nouns = {"projects": "project", "environments": "environment", "collections": "collection",
                     "executions": "execution", "requests": "request", "load-runs": "load_run",
                     "load-scenarios": "load_scenario", "load-agents": "load_agent", "datasets": "dataset"}
        for index, part in enumerate(tail):
            if part in api_nouns:
                kind = "api_" + api_nouns[part]
                resource_id = ""
                if status < 400 and index + 1 < len(tail):
                    candidate = safe_resource_ref(urllib.parse.unquote(tail[index + 1]))
                    if candidate and tail[index + 1] not in api_nouns and tail[index + 1] not in _ROUTE_VERBS:
                        resource_id = candidate
    # Only the known API-testing noun positions carry path IDs. Other routes
    # can bind a validated ID with mark_resource rather than assuming a suffix.
    if status < 400:
        if isinstance(payload, dict):
            if kind.startswith("api_") and method == "POST" and not resource_id:
                data = payload.get("data")
                if isinstance(data, dict):
                    resource_id = safe_resource_ref(data.get("id"))
            if kind in {"job", "agent_run", "test_run", "api_execution", "api_load_run"}:
                for nested_key in ("job", "run", "execution"):
                    nested = payload.get(nested_key)
                    if isinstance(nested, dict):
                        resource_id = resource_id or safe_resource_ref(nested.get("id") or nested.get("job_id") or nested.get("run_id"))
        if kind == "file" and method == "GET" and path in {"/api/file", "/api/file/history", "/api/file/version"}:
            resource_id = safe_resource_ref(qs.get("file"))
        elif path == "/api/file" and method == "POST" and isinstance(parsed_body, dict):
            resource_id = safe_resource_ref(parsed_body.get("file"))
        elif path == "/api/file/op" and method == "POST" and isinstance(payload, dict):
            resource_id = safe_resource_ref(payload.get("file"))
    if kind == "account" and tail:
        verb = "login" if tail[0] == "login" else "logout" if tail[0] == "logout" else "manage"
    else:
        verb = {"GET": "view", "HEAD": "inspect", "POST": "create", "PUT": "update", "DELETE": "delete", "PATCH": "update"}.get(method, "request")
        for part in reversed(tail):
            if part in _ROUTE_VERBS:
                verb = part
                break
    if kind == "file" and method == "POST" and isinstance(parsed_body, dict) and parsed_body.get("op") in {"copy", "move", "rename"}:
        verb = parsed_body["op"]
    return kind, resource_id, _FIXED_ACTIONS.get((method, path), f"{kind}.{verb}")


def _result(status, payload, interrupted):
    if interrupted:
        return "interrupted"
    if status in (401, 403):
        return "denied"
    if status == 207:
        return "partial"
    if status >= 400 or status == 0 or isinstance(payload, dict) and payload.get("ok") is False:
        return "failed"
    state = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    nested_states = [state.get(key, {}).get("status") for key in ("job", "run", "execution")
                     if isinstance(state, dict) and isinstance(state.get(key), dict)]
    if status == 202 or isinstance(state, dict) and state.get("status") in {"queued", "accepted", "pending"} or any(value in {"queued", "accepted", "pending", "running"} for value in nested_states):
        return "accepted"
    return "success"


def finish_request(handler):
    context = getattr(handler, "_operation_context", None)
    try:
        path = urllib.parse.urlsplit(getattr(handler, "path", "")).path
        method = getattr(handler, "command", "")
        if not path.startswith("/api/") and path != "/report" or path == "/api/health" or method == "OPTIONS":
            return
        payload = getattr(handler, "_operation_payload", None)
        status = getattr(handler, "_operation_status", 0)
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(handler.path).query, keep_blank_values=False))
        kind, resource_id, action = _resource(path, method, payload, qs, status,
                                               getattr(handler, "_parsed_body", None))
        kind = getattr(handler, "_operation_resource_type", "") or kind
        resource_id = getattr(handler, "_operation_resource_id", "") or resource_id
        if not kind:
            return
        result = _result(status, payload, getattr(handler, "_operation_interrupted", False))
        items = []
        body = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
        raw_items = body.get("results") if isinstance(body, dict) else None
        raw_errors = body.get("errors") if isinstance(body, dict) else None
        if isinstance(raw_items, list) or isinstance(raw_errors, list):
            entries = [(item, False) for item in (raw_items if isinstance(raw_items, list) else [])]
            entries += [(item, True) for item in (raw_errors if isinstance(raw_errors, list) else [])]
            for item, is_error in entries[:26]:
                if isinstance(item, dict):
                    code = _safe_id(item.get("code") or item.get("reason_code")) or ("operation_failed" if is_error else "")
                    item_result = "failed" if is_error or item.get("ok") is False else "success"
                    items.append({"resource_id": next((safe_resource_ref(item.get(key)) for key in (*_RESOURCE_KEYS, "file", "targetFile") if safe_resource_ref(item.get(key))), ""),
                                  "result": item_result, "reason_code": code})
            if result == "success" and any(item["result"] == "failed" for item in items):
                result = "partial"
        event = {"request_id": handler._operation_id, "actor": handler._operation_actor or {"kind": "anonymous"},
                 "action": action, "method": method, "resource_type": kind, "resource_id": resource_id, "result": result,
                 "status_code": status, "duration_ms": int((time.monotonic() - handler._operation_started) * 1000),
                 "item_outcomes": items, "truncated": len(entries) > 25 if isinstance(raw_items, list) or isinstance(raw_errors, list) else False,
                 "initiator_user_id": getattr(handler, "_operation_initiator", "")}
        store = _store()
        stored = store.append(event)
        now = time.monotonic()
        with _LOCK:
            _AUDIT.update(storage=stored["storage"], stored=stored["stored"])
            if not stored["stored"]:
                _AUDIT["last_failure"] = time.time()
            replay_due = stored["storage"] == "sqlite" and now - _LAST_REPLAY.get(str(store.path), 0) >= 60
            if replay_due:
                _LAST_REPLAY[str(store.path)] = now
                _AUDIT["last_replay"] = time.time()
        if not stored["stored"]:
            _LOG.error("operation audit persistence unavailable request_id=%s", handler._operation_id)
        elif stored["storage"] == "spool":
            _LOG.warning("operation audit spooled request_id=%s", handler._operation_id)
        if replay_due:
            store.replay_spool(limit=10)
    except Exception:
        _LOG.exception("operation audit finalization failed")
        with _LOCK:
            _AUDIT.update(storage="failed", stored=False, last_failure=time.time())
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def handle_operations(handler):
    from . import identity
    path = urllib.parse.urlsplit(handler.path).path
    if path != "/api/operations":
        return False
    if handler.command != "GET":
        handler._json({"ok": False, "code": "method_not_allowed"}, 405)
        return True
    try:
        session = auth.verify_session_token(auth.bearer_token(handler.headers))
        profile = identity.get_access_profile((session or {}).get("user", "")) if session else None
    except Exception:
        handler._json({"ok": False, "code": "audit_unavailable"}, 503)
        return True
    if not profile or profile.get("user_id") != (session or {}).get("user_id"):
        handler._json({"ok": False, "code": "unauthorized"}, 401)
        return True
    qs = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(handler.path).query))
    try:
        limit = int(qs.get("limit", 50))
        cursor = int(qs["cursor"]) if qs.get("cursor") else None
        if limit < 1 or limit > 100 or cursor is not None and cursor < 1:
            raise ValueError
        all_actors = qs.get("scope") == "all" and bool(profile.get("is_superuser"))
        filters = {key: value for key, value in qs.items() if key in {"method", "action", "result", "resource_type", "resource_id", "actor_id"}}
        if "method" in filters and filters["method"] not in {"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"}:
            raise ValueError
        for key in ("from_ts", "to_ts"):
            if key in qs:
                stamp = float(qs[key])
                if not math.isfinite(stamp) or stamp < 0:
                    raise ValueError
                filters[key] = stamp
        if filters.get("from_ts", 0) > filters.get("to_ts", float("inf")):
            raise ValueError
        page = _store().list_events(profile, limit=limit, cursor=cursor, filters=filters, all_actors=all_actors)
    except (ValueError, TypeError):
        handler._json({"ok": False, "code": "invalid_filter"}, 400)
        return True
    except PermissionError:
        handler._json({"ok": False, "code": "unauthorized"}, 401)
        return True
    except Exception:
        handler._json({"ok": False, "code": "audit_unavailable"}, 503)
        return True
    with _LOCK:
        audit = dict(_AUDIT)
    handler._json({"ok": True, **page, "audit": audit})
    return True
