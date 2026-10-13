"""Validate a trusted user selection, then freeze one UI execution batch."""

import hashlib
import json
import re

from . import business_line_service, case_service
from .operation_attribution import current_actor
from .ui_case_compiler import compile_ui_case
from .ui_execution_contract import AccessDenied, require_ui_actor, resolve_ui_target
from .ui_execution_store import BatchConflict, UiExecutionStore


_REVISION = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_REQUEST_KEYS = {"app_id", "cases", "targets", "suite_id", "suite_revision", "test_data_refs", "notifications"}


class BatchValidationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _identifier(value, code):
    if not isinstance(value, str) or not _REFERENCE.fullmatch(value):
        raise BatchValidationError(code)
    return value


def _normalize_request(request):
    if not isinstance(request, dict) or set(request) - _REQUEST_KEYS:
        raise BatchValidationError("request_invalid")
    app_id = _identifier(request.get("app_id"), "app_required")
    cases = request.get("cases")
    targets = request.get("targets")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200:
        raise BatchValidationError("cases_invalid")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 8 or len(cases) * len(targets) > 400:
        raise BatchValidationError("targets_invalid")
    normalized_cases, seen = [], set()
    for item in cases:
        if not isinstance(item, dict) or set(item) != {"case_id", "revision"}:
            raise BatchValidationError("case_invalid")
        case_id = _identifier(item["case_id"], "case_invalid")
        revision = item["revision"]
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision) or case_id in seen:
            raise BatchValidationError("case_invalid")
        seen.add(case_id)
        normalized_cases.append({"case_id": case_id, "revision": revision})
    normalized_targets, seen = [], set()
    for item in targets:
        if not isinstance(item, dict) or set(item) != {"runner_id", "device_id", "platform"}:
            raise BatchValidationError("target_invalid")
        target = {key: _identifier(item[key], "target_invalid") for key in ("runner_id", "device_id", "platform")}
        if target["platform"] not in {"android", "ios"}:
            raise BatchValidationError("target_invalid")
        key = tuple(target.values())
        if key in seen:
            raise BatchValidationError("target_duplicate")
        seen.add(key)
        normalized_targets.append(target)
    references = request.get("test_data_refs", [])
    if not isinstance(references, list) or len(references) > 32:
        raise BatchValidationError("test_data_refs_invalid")
    references = [_identifier(value, "test_data_refs_invalid") for value in references]
    if len(set(references)) != len(references):
        raise BatchValidationError("test_data_refs_invalid")
    notifications = request.get("notifications", {"enabled": False})
    if not isinstance(notifications, dict) or set(notifications) != {"enabled"} or type(notifications.get("enabled")) is not bool:
        raise BatchValidationError("notifications_invalid")
    suite_id = request.get("suite_id")
    suite_revision = request.get("suite_revision")
    if (suite_id is None) != (suite_revision is None):
        raise BatchValidationError("suite_invalid")
    if suite_id is not None:
        suite_id = _identifier(suite_id, "suite_invalid")
        suite_revision = _identifier(suite_revision, "suite_invalid")
    return {"app_id": app_id, "cases": normalized_cases, "targets": normalized_targets,
            "suite_id": suite_id, "suite_revision": suite_revision,
            "test_data_refs": references, "notifications": {"enabled": notifications["enabled"]}}


def _request_hash(request):
    data = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _store():
    return UiExecutionStore()


def create_ui_batch(actor, request, idempotency_key):
    """Freeze selected shared baselines and dispatch intents without creating jobs."""
    normalized = _normalize_request(request)
    profile = require_ui_actor(actor, app_id=normalized["app_id"])
    key = _identifier(idempotency_key, "idempotency_key_invalid")
    request_hash = _request_hash(normalized)
    store = _store()
    prior = store.find_idempotent(profile["user_id"], key)
    if prior:
        if prior["request_hash"] != request_hash:
            raise BatchConflict("idempotency_conflict")
        return store.get_batch(prior["ui_batch_id"])
    return store.create_batch(_prepare_ui_batch(actor, normalized, profile, key, request_hash))


def _prepare_ui_batch(actor, normalized, profile, key, request_hash):
    application = business_line_service.configured_test_application(normalized['app_id']) or {}
    resolved_targets = []
    for target in normalized["targets"]:
        resolved = resolve_ui_target(normalized["app_id"], target, actor)
        if not resolved["ok"]:
            raise BatchValidationError(resolved["block_code"])
        resolved = dict(resolved)
        mapping = {"app_id": normalized["app_id"], "platform_mappings": resolved["platform_mappings"],
                   "target_package": resolved["target_package"]}
        resolved["mapping_revision"] = _request_hash(mapping)
        resolved_targets.append(resolved)
    # One catalog fetch for the selected scope; never trust a supplied path or _root.
    selected = {case["case_id"]: case for case in normalized["cases"]}
    catalog = {row.get("case_id"): row for row in case_service.list_task_case_assets()
               if row.get("case_id") in selected and not row.get("error")}
    snapshots, attempts = [], []
    for case in normalized["cases"]:
        asset = catalog.get(case["case_id"])
        if not asset or asset.get("app_package") != normalized["app_id"]:
            raise BatchValidationError("case_unavailable")
        try:
            source = case_service.task_case_yaml(asset)
        except (OSError, ValueError) as error:
            raise BatchValidationError("case_unavailable") from error
        if not isinstance(source, str) or hashlib.sha256(source.encode("utf-8")).hexdigest() != case["revision"]:
            raise BatchValidationError("case_revision_changed")
        metadata = {key: asset.get(key) for key in ("module", "file", "task_name", "app_package", "app_name", "platform", "business", "status", "goal", "expected")}
        metadata['app_name'] = application.get('name') or asset.get('app_name') or '应用'
        snapshots.append({"case_id": case["case_id"], "case_revision": case["revision"],
                          "source_yaml": source, "source_hash": case["revision"], "metadata": metadata})
        for target, resolution in zip(normalized["targets"], resolved_targets):
            from .ui_attempt_progress import stable_step_ids
            compiled = compile_ui_case({"yaml_content": source,
                "step_ids": stable_step_ids(case["case_id"], case["revision"], source)}, resolution)
            if not compiled["ok"]:
                raise BatchValidationError(compiled["block_code"])
            attempts.append({"case_id": case["case_id"], "case_revision": case["revision"],
                             "target": target, "target_resolution": resolution, "compiled_case": compiled})
    trusted = current_actor()
    prepared = {"owner_user_id": profile["user_id"], "owner_username": profile["username"],
                "owner_display_name": trusted["display_name"], "app_id": normalized["app_id"],
                "suite_id": normalized["suite_id"], "suite_revision": normalized["suite_revision"],
                "idempotency_key": key, "request_hash": request_hash, "selection": normalized,
                "snapshots": snapshots, "attempts": attempts}
    return prepared


def get_ui_batch(actor, batch_id):
    profile = require_ui_actor(actor, permission="ui.view")
    if not isinstance(batch_id, str) or not re.fullmatch(r"uib_[0-9a-f]{32}", batch_id):
        return None
    batch = _store().get_batch(batch_id)
    if not batch:
        return None
    if not profile.get("is_superuser") and batch["owner_user_id"] != profile["user_id"]:
        return None
    require_ui_actor(actor, permission="ui.view", app_id=batch["app_id"])
    return batch


def list_ui_batches(actor, filters=None, cursor="", limit=20):
    profile = require_ui_actor(actor, permission="ui.view")
    filters = filters or {}
    if not isinstance(filters, dict) or set(filters) - {"app_id", "scope"}:
        raise BatchValidationError("filters_invalid")
    scope = filters.get("scope", "own")
    if scope not in {"own", "all"}:
        raise BatchValidationError("scope_invalid")
    if scope == "all" and not profile.get("is_superuser"):
        raise AccessDenied("team_scope_forbidden")
    app_id = filters.get("app_id")
    if app_id:
        app_id = _identifier(app_id, "app_invalid")
        require_ui_actor(actor, permission="ui.view", app_id=app_id)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise BatchValidationError("limit_invalid")
    if not isinstance(cursor, str):
        raise BatchValidationError("cursor_invalid")
    # Ownership is admin-sensitive; app scope applies to every actor.
    allowed = (profile.get("scope") or {}).get("ui_apps")
    app_ids = None if allowed == "*" else tuple(allowed or [])
    return _store().list_batches(None if scope == "all" else profile["user_id"],
                                 app_id, cursor, limit, app_ids)


def append_case_attempt(batch_id, case_id, case_revision, target, compiled_case):
    """Internal T4 retry hook; case, target, and compilation must match a frozen attempt."""
    return _store().append_case_attempt(batch_id, case_id, case_revision, target, compiled_case)


def preflight_ui_batch(actor, request):
    from .ui_batch_dispatch import preflight_ui_batch as preflight
    return preflight(actor, request)
