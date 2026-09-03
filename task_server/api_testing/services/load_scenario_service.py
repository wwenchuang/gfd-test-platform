"""Copy functional cases into load scenarios and enforce safety admission."""

import copy
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from ..contracts.load_testing import LoadScenarioPayloadError, parse_load_scenario_definition


@dataclass(frozen=True)
class LoadScenarioIssue:
    level: str
    code: str
    step_id: str
    message: str
    remedy: str


@dataclass(frozen=True)
class LoadScenarioAdmission:
    accepted: bool
    definition: Optional[dict]
    issues: Tuple[LoadScenarioIssue, ...]


_PROTECTED_ACTIONS = (
    ("hardware_action_blocked", ("真实打印", "printjob/create", "createprint", "startprint"), "真实打印不能进入压测场景", "改用无硬件副作用的打印任务查询；真实打印继续保留为功能回归。"),
    ("device_control_blocked", ("设备控制", "device/command", "device/control", "binddevice", "unbinddevice"), "设备控制不能进入压测场景", "改用设备状态查询；绑定、解绑、启停等动作继续保留为功能回归。"),
    ("payment_blocked", ("支付", "/pay", "payment"), "支付接口不能进入压测场景", "使用明确的免支付沙箱链路，或仅压测商品与订单查询。"),
    ("sms_blocked", ("短信", "/sms/", "sendcode", "verificationcode"), "短信接口不能进入压测场景", "使用固定测试令牌或内部桩服务，避免真实发送短信。"),
    ("high_cost_ai_blocked", ("高成本ai", "ai/image/generate", "optimizeandgenerate", "generateimage", "createtextmodel", "texttomodel"), "高成本 AI 接口不能进入压测场景", "压测任务状态查询等低成本接口，生成类接口单独做受控容量验证。"),
)


class LoadScenarioService:
    @classmethod
    def validate_definition(cls, payload):
        try:
            definition = parse_load_scenario_definition(payload)
        except LoadScenarioPayloadError as exc:
            return LoadScenarioAdmission(
                False,
                None,
                (LoadScenarioIssue("error", "invalid_definition", "", str(exc), "按提示修正场景字段后重新校验。"),),
            )
        issues = list(cls._protected_action_issues(definition))
        issues.extend(cls._lifecycle_issues(definition))
        return LoadScenarioAdmission(not issues, definition, tuple(issues))

    @classmethod
    def copy_from_case_versions(cls, *, name, description, cases):
        snapshots = [cls._case_snapshot(item) for item in cases]
        steps = []
        for case_index, case in enumerate(snapshots):
            processing = case.get("processing") if isinstance(case.get("processing"), Mapping) else {}
            for index, item in enumerate(processing.get("setup_steps") or []):
                steps.append(cls._inline_step(item, f"case-{case_index + 1}-setup-{index + 1}", "agent_setup"))
            step_id = cls._unique_step_id(case.get("id"), case_index, steps)
            side_effect = cls._infer_side_effect(case)
            main_step = {
                "id": step_id,
                "name": str(case.get("name") or f"步骤 {case_index + 1}"),
                "scope": "iteration",
                "action": "http_request",
                "request": copy.deepcopy(case.get("request") or {}),
                "assertions": cls._plain_items(case.get("assertions") or []),
                "extractions": cls._plain_items(case.get("extractions") or []),
                "sleep_ms": 0,
                "side_effect": side_effect,
            }
            steps.append(main_step)
            cleanup_steps = processing.get("cleanup_steps") or []
            if cleanup_steps and side_effect in {"creates_owned_resource", "mutates_owned_resource"}:
                cleanup_id = f"{step_id}-cleanup-1"
                main_step["cleanup_step_id"] = cleanup_id
            for index, item in enumerate(cleanup_steps):
                identifier = f"{step_id}-cleanup-{index + 1}"
                steps.append(cls._inline_step(item, identifier, "cleanup_once", cleanup=True))
        payload = {
            "name": name,
            "description": description,
            "mode": "single_interface" if len(steps) == 1 else "workflow",
            "steps": steps,
            "dataset_contract": {"dataset_id": None, "usage_mode": "cycle", "variables": []},
            "risk": {"level": "low", "ownership_variable": None, "notes": "从功能用例复制，保存前需完成预检。"},
            "source_snapshot": {
                "type": "case_version",
                "version_ids": [str(item.get("id") or "") for item in snapshots],
                "items": snapshots,
            },
        }
        return cls.validate_definition(payload)

    @classmethod
    def _protected_action_issues(cls, definition):
        issues = []
        snapshots = definition["source_snapshot"]["items"]
        for step in definition["steps"]:
            path = str(step["request"].get("path") or "")
            matching_sources = [
                item
                for item in snapshots
                if isinstance(item, dict)
                and (
                    str((item.get("request") or {}).get("path") or "") == path
                    or str(item.get("name") or "") == step["name"]
                )
            ]
            tags = [
                tag
                for source in matching_sources
                for tag in (source.get("tags", []) or [])
            ]
            haystack = " ".join([path, step["name"], *(str(item) for item in tags)]).lower().replace(" ", "")
            for code, markers, message, remedy in _PROTECTED_ACTIONS:
                if any(marker.lower().replace(" ", "") in haystack for marker in markers):
                    issues.append(LoadScenarioIssue("error", code, step["id"], message, remedy))
                    break
        return tuple(issues)

    @staticmethod
    def _lifecycle_issues(definition):
        issues = []
        step_by_id = {item["id"]: item for item in definition["steps"]}
        ownership_variable = definition["risk"].get("ownership_variable")
        for index, step in enumerate(definition["steps"]):
            if step["side_effect"] not in {"creates_owned_resource", "mutates_owned_resource"}:
                continue
            if not ownership_variable:
                issues.append(
                    LoadScenarioIssue(
                        "error", "ownership_required", step["id"],
                        "写操作没有声明本轮资源归属变量",
                        "从创建响应中提取唯一资源 ID，并在风险配置中选择该归属变量。",
                    )
                )
            cleanup_id = step.get("cleanup_step_id")
            cleanup = step_by_id.get(cleanup_id) if cleanup_id else None
            cleanup_index = definition["steps"].index(cleanup) if cleanup else -1
            if cleanup is None or cleanup["scope"] != "cleanup_once" or cleanup_index <= index:
                issues.append(
                    LoadScenarioIssue(
                        "error", "cleanup_required", step["id"],
                        "写操作没有可验证的后置清理步骤",
                        "增加仅删除本轮资源的 cleanup_once 步骤，并在写步骤中关联该步骤。",
                    )
                )
                continue
            extracted_targets = {item["target"] for item in step["extractions"]}
            if (
                ownership_variable not in extracted_targets
                or not LoadScenarioService._contains_variable_reference(
                    cleanup["request"], ownership_variable
                )
            ):
                issues.append(
                    LoadScenarioIssue(
                        "error", "cleanup_ownership_mismatch", step["id"],
                        "清理步骤没有使用写操作提取的归属变量",
                        "在写操作响应中提取本轮资源 ID，并让清理请求通过模板或 $extract 引用同一变量。",
                    )
                )
        return tuple(issues)

    @staticmethod
    def _contains_variable_reference(value, variable):
        if not variable:
            return False
        if isinstance(value, dict):
            if value == {"$extract": variable}:
                return True
            return any(
                LoadScenarioService._contains_variable_reference(item, variable)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(
                LoadScenarioService._contains_variable_reference(item, variable)
                for item in value
            )
        return isinstance(value, str) and f"{{{{{variable}}}}}" in value

    @staticmethod
    def _case_snapshot(case):
        if isinstance(case, Mapping):
            return copy.deepcopy(dict(case))
        result = {}
        for field in ("id", "name", "version", "request", "assertions", "extractions", "processing", "tags"):
            if hasattr(case, field):
                value = getattr(case, field)
                if field in {"assertions", "extractions"}:
                    value = LoadScenarioService._plain_items(value)
                elif isinstance(value, Mapping):
                    value = copy.deepcopy(dict(value))
                result[field] = copy.deepcopy(value)
        return result

    @staticmethod
    def _plain_items(items):
        result = []
        for item in items:
            if isinstance(item, Mapping):
                result.append(copy.deepcopy(dict(item)))
            else:
                result.append({key: copy.deepcopy(value) for key, value in vars(item).items() if key != "sequence"})
        return result

    @staticmethod
    def _unique_step_id(value, index, existing):
        raw = "".join(character.lower() if character.isalnum() else "-" for character in str(value or ""))
        raw = raw.strip("-")[:50]
        if not raw or not raw[0].isalpha() or not raw.isascii():
            raw = f"case-{index + 1}"
        used = {item["id"] for item in existing}
        candidate = raw
        suffix = 2
        while candidate in used:
            candidate = f"{raw}-{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _infer_side_effect(case):
        explicit = case.get("side_effect")
        if explicit:
            return str(explicit)
        request = case.get("request") or {}
        method = str(request.get("method") or "GET").upper()
        path = str(request.get("path") or "").lower()
        tags = " ".join(str(item) for item in case.get("tags", [])).lower()
        if method in {"GET", "HEAD", "OPTIONS"} or "只读" in tags:
            return "readonly"
        if any(token in path for token in ("search", "query", "page", "list", "detail", "login")):
            return "readonly"
        return "creates_owned_resource"

    @staticmethod
    def _inline_step(item, identifier, scope, cleanup=False):
        item = dict(item)
        request = copy.deepcopy(item.get("request") or {})
        return {
            "id": identifier,
            "name": str(item.get("name") or identifier),
            "scope": scope,
            "action": "http_request",
            "request": request,
            "assertions": LoadScenarioService._plain_items(item.get("assertions") or []),
            "extractions": LoadScenarioService._plain_items(item.get("extractions") or []),
            "sleep_ms": int(item.get("sleep_ms") or 0),
            "side_effect": (
                "cleanup_owned_resource"
                if cleanup
                else LoadScenarioService._infer_side_effect({"request": request, "tags": []})
            ),
        }
