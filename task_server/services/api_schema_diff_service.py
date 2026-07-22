"""Deterministic API revision diffs and affected-plan analysis."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List

from task_server.storage import clean_id, read_json_file, safe_join, write_json_file
from task_server.services import api_asset_service


DIFF_FIELDS = (
    "method",
    "path",
    "parameters",
    "request_body_required",
    "request_schema",
    "responses",
    "response_schema",
    "security",
    "deprecated",
    "module",
    "name",
    "description",
    "tags",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _endpoint_map(revision: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for endpoint in revision.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        key = str(endpoint.get("endpoint_key") or "").strip()
        if key:
            result[key] = endpoint
    return result


def _endpoint_ref(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "endpoint_key": endpoint.get("endpoint_key"),
        "endpoint_id": endpoint.get("endpoint_id"),
        "method": endpoint.get("method"),
        "path": endpoint.get("path"),
        "name": endpoint.get("name"),
        "schema_hash": endpoint.get("schema_hash"),
        "endpoint_revision_id": endpoint.get("endpoint_revision_id"),
    }


def _changed_fields(old_endpoint: Dict[str, Any], new_endpoint: Dict[str, Any]) -> List[str]:
    return [
        field
        for field in DIFF_FIELDS
        if _canonical(old_endpoint.get(field)) != _canonical(new_endpoint.get(field))
    ]


def compare_api_revisions(old_revision: Dict[str, Any], new_revision: Dict[str, Any]) -> Dict[str, Any]:
    old_map = _endpoint_map(old_revision or {})
    new_map = _endpoint_map(new_revision or {})
    added: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    unchanged: List[Dict[str, Any]] = []
    for endpoint_key in sorted(set(old_map) | set(new_map)):
        old_endpoint = old_map.get(endpoint_key)
        new_endpoint = new_map.get(endpoint_key)
        if old_endpoint is None and new_endpoint is not None:
            added.append({"endpoint_key": endpoint_key, "new": _endpoint_ref(new_endpoint)})
            continue
        if new_endpoint is None and old_endpoint is not None:
            removed.append({"endpoint_key": endpoint_key, "old": _endpoint_ref(old_endpoint)})
            continue
        old_hash = str((old_endpoint or {}).get("schema_hash") or "")
        new_hash = str((new_endpoint or {}).get("schema_hash") or "")
        fields = _changed_fields(old_endpoint or {}, new_endpoint or {})
        if old_hash == new_hash and not fields:
            unchanged.append({
                "endpoint_key": endpoint_key,
                "schema_hash": new_hash,
                "endpoint": _endpoint_ref(new_endpoint or {}),
            })
            continue
        if not fields:
            fields = ["schema_hash"]
        changed.append({
            "endpoint_key": endpoint_key,
            "old_schema_hash": old_hash,
            "new_schema_hash": new_hash,
            "fields": fields,
            "old": _endpoint_ref(old_endpoint or {}),
            "new": _endpoint_ref(new_endpoint or {}),
        })
    return {
        "from_revision_id": str((old_revision or {}).get("revision_id") or ""),
        "to_revision_id": str((new_revision or {}).get("revision_id") or ""),
        "summary": {
            "added": len(added),
            "changed": len(changed),
            "removed": len(removed),
            "unchanged": len(unchanged),
        },
        "added": added,
        "changed": changed,
        "removed": removed,
        "unchanged": unchanged,
    }


def _iter_plans(plans: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for plan in plans or []:
        if isinstance(plan, dict):
            yield plan


def analyze_api_plan_impact(diff: Dict[str, Any], plans: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    impacted_keys = {
        str(item.get("endpoint_key") or "").strip()
        for group in ("changed", "removed")
        for item in (diff.get(group) or [])
        if isinstance(item, dict) and str(item.get("endpoint_key") or "").strip()
    }
    affected_plan_ids: List[str] = []
    affected_case_ids: List[str] = []
    affected: List[Dict[str, Any]] = []
    unresolved_legacy_plan_ids: List[str] = []
    for plan in _iter_plans(plans):
        plan_id = str(plan.get("plan_id") or "").strip()
        aliases = {
            str(endpoint.get("endpoint_id") or "").strip(): str(endpoint.get("endpoint_key") or "").strip()
            for endpoint in (plan.get("endpoints") or [])
            if isinstance(endpoint, dict) and endpoint.get("endpoint_id") and endpoint.get("endpoint_key")
        }
        plan_case_ids: List[str] = []
        unresolved = False
        for case in plan.get("cases") or []:
            if not isinstance(case, dict):
                continue
            endpoint_key = str(case.get("endpoint_key") or "").strip()
            endpoint_id = str(case.get("endpoint_id") or "").strip()
            if not endpoint_key and endpoint_id:
                endpoint_key = aliases.get(endpoint_id, "")
                if not endpoint_key:
                    unresolved = True
            if endpoint_key in impacted_keys:
                case_id = str(case.get("case_id") or case.get("plan_case_key") or "").strip()
                if case_id:
                    plan_case_ids.append(case_id)
        if plan_case_ids:
            affected_plan_ids.append(plan_id)
            affected_case_ids.extend(plan_case_ids)
            affected.append({"plan_id": plan_id, "case_ids": sorted(set(plan_case_ids))})
        if unresolved and plan_id:
            unresolved_legacy_plan_ids.append(plan_id)
    return {
        "affected_plans": len(set(affected_plan_ids)),
        "affected_plan_ids": sorted(set(affected_plan_ids)),
        "affected_case_ids": sorted(set(affected_case_ids)),
        "affected": sorted(affected, key=lambda item: item.get("plan_id") or ""),
        "unresolved_legacy_plan_ids": sorted(set(unresolved_legacy_plan_ids)),
    }


def _diff_id(asset_id: str, from_revision_id: str, to_revision_id: str) -> str:
    raw = f"{asset_id}\n{from_revision_id}\n{to_revision_id}"
    return f"api_diff_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _diff_path(diff_id: str) -> str:
    return safe_join(api_asset_service.API_TESTING_DIR, "diffs", f"{clean_id(diff_id, 'api_diff')}.json")


def save_api_diff(asset_id: str, diff: Dict[str, Any], impact: Dict[str, Any] | None = None) -> Dict[str, Any]:
    result = dict(diff or {})
    result["asset_id"] = str(asset_id or "")
    result["diff_id"] = _diff_id(
        result["asset_id"],
        str(result.get("from_revision_id") or ""),
        str(result.get("to_revision_id") or ""),
    )
    result["impact"] = dict(impact or {})
    write_json_file(_diff_path(result["diff_id"]), result)
    return result


def get_api_diff(diff_id: str) -> Dict[str, Any]:
    value = read_json_file(_diff_path(diff_id), default={}) or {}
    return value if isinstance(value, dict) else {}


def get_asset_revision_diff(
    asset_id: str,
    from_revision_id: str = "",
    to_revision_id: str = "",
) -> Dict[str, Any]:
    asset = api_asset_service.get_api_asset(asset_id)
    if not asset:
        raise ValueError("API asset 不存在")
    revisions = api_asset_service.list_api_revisions(asset_id, limit=200)
    target_to = str(to_revision_id or asset.get("active_revision_id") or "").strip()
    if not target_to:
        raise ValueError("API asset 尚无活动版本")
    target_from = str(from_revision_id or "").strip()
    if not target_from:
        revision_ids = [str(item.get("revision_id") or "") for item in revisions]
        if target_to in revision_ids:
            position = revision_ids.index(target_to)
            if position + 1 < len(revision_ids):
                target_from = revision_ids[position + 1]
    new_revision = api_asset_service.get_api_revision(target_to)
    old_revision = api_asset_service.get_api_revision(target_from) if target_from else {}
    if not new_revision or new_revision.get("asset_id") != asset_id:
        raise ValueError("目标 API revision 不存在")
    if target_from and (not old_revision or old_revision.get("asset_id") != asset_id):
        raise ValueError("起始 API revision 不存在")
    diff = compare_api_revisions(old_revision, new_revision)
    from task_server.services import api_test_plan_service

    impact = analyze_api_plan_impact(diff, api_test_plan_service.list_full_api_test_plans(limit=1000))
    diff["asset_id"] = asset_id
    diff["impact"] = impact
    return diff


__all__ = [
    "analyze_api_plan_impact",
    "compare_api_revisions",
    "get_asset_revision_diff",
    "get_api_diff",
    "save_api_diff",
]
