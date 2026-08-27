"""Assertion audit and evidence-gated review drafts for adopted API baselines."""

import copy
import json
from collections.abc import Mapping

from ..contracts.case import parse_case_payload
from ..repositories.case_repository import CaseRepository
from .case_service import CaseNotFoundError, CaseService
from .workflow_policy import (
    classify_endpoint_workflow,
    is_print_cancel_step,
    print_task_extraction_targets,
)


BUSINESS_SUCCESS_VALUES = {"$.code": (0, 200), "$.success": (True,)}


class BaselineAssertionUpgradeError(ValueError):
    pass


class BaselineAssertionAuditService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def list(self, project_id, actor_id):
        with self.session_factory() as session:
            repository = CaseRepository(session)
            rows = repository.list_active_baselines(
                project_id,
                actor_id,
                current_only=True,
            )
            version_ids = [version.id for _baseline, _case, version, _endpoint in rows]
            evidence_ids = [
                baseline.debug_execution_case_id
                for baseline, _case, _version, _endpoint in rows
            ]
            assertions_by_version = repository.get_assertions_for_versions(version_ids)
            extractions_by_version = repository.get_extractions_for_versions(version_ids)
            evidence_by_id = repository.get_execution_cases(evidence_ids)
            attempts_by_evidence = repository.latest_execution_attempts(evidence_ids)

            items = []
            for baseline, case, version, endpoint in rows:
                evidence = evidence_by_id.get(baseline.debug_execution_case_id)
                attempt = attempts_by_evidence.get(baseline.debug_execution_case_id)
                response = _evidence_response(evidence, attempt)
                assertions = [
                    _stored_assertion(item)
                    for item in assertions_by_version.get(version.id, ())
                ]
                extractions = [
                    _stored_extraction(item)
                    for item in extractions_by_version.get(version.id, ())
                ]
                workflow = classify_endpoint_workflow(endpoint)
                analysis = analyze_baseline_assertions(
                    assertions,
                    response,
                    workflow,
                    version.processing_spec or {},
                    extractions,
                )
                if _is_one_time(case.name, baseline.group_name, endpoint.tags):
                    analysis["execution"] = {
                        "level": "manual",
                        "label": "一次性人工复核",
                        "selectable": False,
                        "reason": "一次性基线不得进入批量连续执行",
                    }
                items.append({
                    "baseline_id": baseline.id,
                    "case_id": case.id,
                    "case_version_id": version.id,
                    "endpoint_id": endpoint.id,
                    "case_name": case.name,
                    "method": endpoint.method,
                    "path": endpoint.path,
                    "group_name": baseline.group_name or "未分组",
                    "environment_revision_id": baseline.environment_revision_id,
                    "evidence_execution_case_id": baseline.debug_execution_case_id,
                    "evidence_captured_at": _captured_at(evidence, attempt),
                    "upgrade_draft_case_version_id": (
                        case.active_version_id
                        if case.active_version_id != version.id
                        else None
                    ),
                    **analysis,
                })

        counts = {
            status: sum(item["status"] == status for item in items)
            for status in (
                "verified",
                "upgrade_available",
                "http_failure",
                "business_failure",
                "domain_assertion_required",
                "evidence_missing",
            )
        }
        return {
            "summary": {
                "total": len(items),
                **counts,
                "needs_review": len(items) - counts["verified"],
                "safe_review": sum(
                    item["status"] != "verified"
                    and item["execution"]["selectable"]
                    for item in items
                ),
            },
            "items": items,
        }

    def create_upgrade_draft(self, baseline_id, actor_id):
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            baseline = repository.get_baseline_for_update(baseline_id)
            if (
                baseline is None
                or baseline.owner_id != actor_id
                or baseline.status != "active"
            ):
                raise CaseNotFoundError("API baseline was not found")

            version = repository.get_version(baseline.case_version_id)
            case = repository.get_case_for_update(baseline.case_id)
            endpoint = repository.get_endpoint(version.endpoint_id) if version else None
            if version is None or case is None or endpoint is None:
                raise CaseNotFoundError("API baseline case was not found")
            if case.active_version_id != version.id:
                raise BaselineAssertionUpgradeError(
                    "该用例已有较新的用例版本，请继续复核现有版本，避免覆盖人工修改"
                )

            evidence = repository.get_execution_case(
                baseline.debug_execution_case_id
            )
            attempt = repository.latest_execution_attempts(
                [baseline.debug_execution_case_id]
            ).get(baseline.debug_execution_case_id)
            assertions = [
                _stored_assertion(item)
                for item in repository.get_assertions(version.id)
            ]
            extractions = [
                _stored_extraction(item)
                for item in repository.get_extractions(version.id)
            ]
            analysis = analyze_baseline_assertions(
                assertions,
                _evidence_response(evidence, attempt),
                classify_endpoint_workflow(endpoint),
                version.processing_spec or {},
                extractions,
            )
            suggestions = analysis["suggested_assertions"]
            if analysis["status"] != "upgrade_available" or not suggestions:
                raise BaselineAssertionUpgradeError(
                    f"当前审计结果为“{analysis['status_label']}”，不能生成待复核版本"
                )
            for suggestion in suggestions:
                if any(
                    item.get("enabled", True) is not False
                    and item.get("type") == suggestion.get("type")
                    and item.get("path") == suggestion.get("path")
                    for item in assertions
                ):
                    raise BaselineAssertionUpgradeError(
                        "现有业务断言与实际响应冲突，请人工确认测试意图后修改"
                    )

            current_view = CaseService._version_view(repository, version, case)
            payload = parse_case_payload(
                _upgrade_payload(current_view, suggestions),
                allow_disabled_scope=True,
            )
            upgraded = CaseService._persist_version(
                repository,
                case,
                payload,
                repository.next_version_number(case.id),
                actor_id,
                version.group_name or "",
            )
            case.active_version_id = upgraded.id
            case.updated_by = actor_id
            repository.flush()
            return {
                "case_version": CaseService._version_view(
                    repository,
                    upgraded,
                    case,
                ),
                "source_baseline_id": baseline.id,
                "source_case_version_id": version.id,
                "suggestion_count": len(suggestions),
            }


def analyze_baseline_assertions(assertions, response, workflow, processing, extractions=()):
    normalized = [_assertion_dict(item) for item in assertions if _enabled(item)]
    actual_status, body = _response_evidence(response)
    execution = _execution_readiness(workflow, processing, extractions)
    if response is None or actual_status is None:
        return _result(
            "evidence_missing",
            "缺少可解析的历史调试响应，需要重新执行后判断",
            actual_status,
            "",
            None,
            [],
            execution,
        )

    if actual_status < 200 or actual_status >= 400:
        if _has_exact_status_assertion(normalized, actual_status):
            return _result(
                "verified",
                "精确 HTTP 状态断言与实际负向响应一致",
                actual_status,
                "",
                None,
                [],
                execution,
            )
        return _result(
            "http_failure",
            f"实际 HTTP 状态为 {actual_status}，且没有匹配的负向状态断言",
            actual_status,
            "",
            None,
            [],
            execution,
        )

    business_values = _business_values(body)
    if business_values:
        suggestions = []
        failures = []
        for business_path, business_value in business_values:
            if _has_exact_assertion(normalized, business_path, business_value):
                continue
            if _is_business_success(business_path, business_value):
                suggestions.append({
                    "type": "json_path",
                    "operator": "equals",
                    "expected": business_value,
                    "path": business_path,
                    "enabled": True,
                })
            else:
                failures.append((business_path, business_value))
        if failures:
            business_path, business_value = failures[0]
            return _result(
                "business_failure",
                f"实际业务值 {business_path}={business_value!r} 与成功结果不一致，且没有匹配的精确负向断言，不能自动采纳",
                actual_status,
                business_path,
                business_value,
                [],
                execution,
            )
        if suggestions:
            business_path = suggestions[0]["path"]
            business_value = suggestions[0]["expected"]
            return _result(
                "upgrade_available",
                "实际响应为业务成功，可在新版本补充精确业务断言后重新调试",
                actual_status,
                business_path,
                business_value,
                suggestions,
                execution,
            )
        business_path, business_value = business_values[0]
        return _result(
            "verified",
            "精确业务断言与实际响应一致",
            actual_status,
            business_path,
            business_value,
            [],
            execution,
        )

    if _has_domain_assertion(normalized):
        return _result(
            "verified",
            "响应没有统一业务码，现有领域断言已提供结果判定",
            actual_status,
            "",
            None,
            [],
            execution,
        )
    return _result(
        "domain_assertion_required",
        "响应没有统一业务码，当前只有 HTTP 或性能断言，需要补充结构或领域字段断言",
        actual_status,
        "",
        None,
        [],
        execution,
    )


def _result(status, reason, actual_http_status, business_path, business_value, suggestions, execution):
    return {
        "status": status,
        "status_label": {
            "verified": "断言已精确",
            "upgrade_available": "可补精确断言",
            "http_failure": "实际 HTTP 失败",
            "business_failure": "实际业务失败",
            "domain_assertion_required": "缺少领域断言",
            "evidence_missing": "证据不足",
        }[status],
        "reason": reason,
        "actual_http_status": actual_http_status,
        "business_path": business_path,
        "business_value": copy.deepcopy(business_value),
        "suggested_assertions": copy.deepcopy(suggestions),
        "execution": execution,
    }


def _upgrade_payload(version, suggestions):
    assertions = [
        _compact({
            "type": item.type,
            "operator": item.operator,
            "expected": _plain(item.expected),
            "path": item.path,
            "name": item.name,
            "timeout_ms": item.timeout_ms,
            "enabled": item.enabled,
        })
        for item in version.assertions
    ]
    assertions.extend(_plain(item) for item in suggestions)
    return {
        "name": version.name,
        "purpose": version.purpose,
        "priority": version.priority,
        "app_package": version.app_package,
        "app_name": version.app_name,
        "business": version.business,
        "request": _plain(version.request),
        "data_rows": [
            {
                "name": item.name,
                "values": _plain(item.values),
                "enabled": item.enabled,
            }
            for item in version.data_rows
        ],
        "assertions": assertions,
        "extractions": [
            _compact({
                "target": item.target,
                "type": item.type,
                "path": item.path,
                "name": item.name,
                "required": item.required,
                "default": _plain(item.default),
            })
            for item in version.extractions
        ],
        "dependencies": [_plain(item) for item in version.dependencies],
        "processing": _plain(version.processing),
    }


def _compact(value):
    return {key: item for key, item in value.items() if item is not None}


def _plain(value):
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def _assertion_dict(item):
    if isinstance(item, Mapping):
        return dict(item)
    return {
        "type": getattr(item, "type", ""),
        "operator": getattr(item, "operator", ""),
        "expected": copy.deepcopy(getattr(item, "expected", None)),
        "path": getattr(item, "path", None),
        "enabled": getattr(item, "enabled", True),
    }


def _enabled(item):
    if isinstance(item, Mapping):
        return item.get("enabled", True) is not False
    return getattr(item, "enabled", True) is not False


def _response_evidence(response):
    if not isinstance(response, Mapping):
        return None, None
    status = response.get("status_code")
    if not isinstance(status, int) or isinstance(status, bool):
        status = None
    body = response.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            body = None
    return status, body


def _business_values(body):
    if not isinstance(body, Mapping):
        return []
    values = []
    if "code" in body and not isinstance(body["code"], (dict, list)):
        values.append(("$.code", body["code"]))
    if "success" in body and isinstance(body["success"], bool):
        values.append(("$.success", body["success"]))
    return values


def _is_business_success(path, value):
    if path == "$.code":
        return type(value) is int and value in BUSINESS_SUCCESS_VALUES[path]
    if path == "$.success":
        return value is True
    return False


def _has_exact_assertion(assertions, path, actual):
    for item in assertions:
        if item.get("type") != "json_path" or item.get("path") != path:
            continue
        if item.get("operator") == "equals" and _json_value_equal(
            item.get("expected"), actual
        ):
            return True
        expected = item.get("expected")
        if item.get("operator") == "in" and isinstance(expected, list):
            if expected and all(_json_value_equal(value, actual) for value in expected):
                return True
    return False


def _has_exact_status_assertion(assertions, actual):
    for item in assertions:
        if item.get("type") != "status_code":
            continue
        if item.get("operator") == "equals" and _json_value_equal(
            item.get("expected"), actual
        ):
            return True
        expected = item.get("expected")
        if item.get("operator") == "in" and isinstance(expected, list):
            if expected and all(_json_value_equal(value, actual) for value in expected):
                return True
    return False


def _json_value_equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_value_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    if isinstance(left, Mapping):
        return left.keys() == right.keys() and all(
            _json_value_equal(left[key], right[key]) for key in left
        )
    return left == right


def _has_domain_assertion(assertions):
    return any(
        item.get("type") in {"json_path", "schema"}
        and item.get("path") not in BUSINESS_SUCCESS_VALUES
        for item in assertions
    )


def _execution_readiness(workflow, processing, extractions):
    workflow = dict(workflow or {})
    processing = dict(processing or {})
    policy = str(workflow.get("baseline_policy") or "manual")
    if policy == "excluded":
        return {
            "level": "excluded",
            "label": "禁止自动执行",
            "selectable": False,
            "reason": str(workflow.get("reason") or "该操作不可安全回滚"),
        }
    if policy == "direct":
        return {
            "level": "direct",
            "label": "可直接复核",
            "selectable": True,
            "reason": str(workflow.get("reason") or "只读操作可直接执行"),
        }
    if policy == "guarded" and workflow.get("kind") == "print_lifecycle":
        setup_ready = _has_enabled_step(processing.get("setup_steps"))
        task_targets = print_task_extraction_targets(extractions)
        cleanup_steps = processing.get("cleanup_steps")
        cleanup_ready = isinstance(cleanup_steps, list) and any(
            is_print_cancel_step(step, task_targets) for step in cleanup_steps
        )
        if setup_ready and task_targets and cleanup_ready:
            return {
                "level": "guarded",
                "label": "具备打印上下游保护",
                "selectable": True,
                "reason": "已准备运行资源、提取本次打印任务标识并配置取消打印清理",
            }
        missing = []
        if not setup_ready:
            missing.append("启用的前置准备")
        if not task_targets:
            missing.append("本次打印任务标识提取")
        if not cleanup_ready:
            missing.append("引用该标识的取消打印清理")
        return {
            "level": "manual",
            "label": "需人工补全打印闭环",
            "selectable": False,
            "reason": f"缺少{'、'.join(missing)}",
        }
    return {
        "level": "manual",
        "label": "需人工复核",
        "selectable": False,
        "reason": "受控写操作的资源关系无法仅凭步骤存在性证明，需逐条确认准备数据、主体影响和清理结果",
    }


def _has_enabled_step(steps):
    return isinstance(steps, list) and any(
        isinstance(item, Mapping) and item.get("enabled", True) is not False
        for item in steps
    )


def _stored_assertion(record):
    definition = dict(record.definition or {})
    return {
        "type": record.assertion_type,
        "operator": definition.get("operator", ""),
        "expected": copy.deepcopy(definition.get("expected")),
        "path": definition.get("path"),
        "enabled": record.enabled,
    }


def _stored_extraction(record):
    definition = dict(record.definition or {})
    return {
        "target": record.target_name,
        "type": record.extraction_type,
        "path": definition.get("path"),
        "name": definition.get("name"),
        "required": definition.get("required", True),
    }


def _evidence_response(evidence, attempt):
    if evidence is None or evidence.status != "PASSED":
        return None
    if attempt is not None and isinstance(attempt.response, Mapping):
        return copy.deepcopy(dict(attempt.response))
    result = evidence.sanitized_result or {}
    response = result.get("response") if isinstance(result, Mapping) else None
    return copy.deepcopy(dict(response)) if isinstance(response, Mapping) else None


def _captured_at(evidence, attempt):
    value = getattr(attempt, "created_at", None) or getattr(evidence, "updated_at", None)
    return value.isoformat() if hasattr(value, "isoformat") else ""


def _is_one_time(case_name, group_name, tags):
    text = " ".join([str(case_name or ""), str(group_name or ""), *(tags or ())]).lower()
    return any(marker in text for marker in ("一次性", "one-time", "one time"))
