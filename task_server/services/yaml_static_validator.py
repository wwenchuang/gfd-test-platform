"""Static executable validation for generated Midscene YAML."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Set

try:
    import yaml as _pyyaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _pyyaml = None  # type: ignore

from task_server.schemas import FLOW_CHILD_KEYS, MIDSCENE_FLOW_ACTIONS, TASK_LEVEL_ALLOWED_KEYS

CONTRACT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config_data", "yaml_actions.json")
)

COMMON_STEP_ATTRS = {
    "optional",
    "retry",
    "retries",
    "interval",
    "comment",
    "description",
    "note",
    "id",
    "if",
    "then",
    "else",
    "params",
    "args",
    "enabled",
}
STEP_ATTR_KEYS: Set[str] = set(FLOW_CHILD_KEYS) | COMMON_STEP_ATTRS


def load_yaml_action_contract(path: str = CONTRACT_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    allowed = set(str(item).strip() for item in data.get("allowed_actions") or [] if str(item).strip())
    if not allowed:
        allowed = set(MIDSCENE_FLOW_ACTIONS)
    allowed &= set(MIDSCENE_FLOW_ACTIONS)
    data["allowed_actions"] = sorted(allowed)
    data.setdefault("required_for_flow", ["aiWaitFor", "aiAssert"])
    data.setdefault("transition_actions", ["aiTap", "aiInput", "aiAction", "aiAct", "ai", "launch", "runAdbShell"])
    data.setdefault("assertion_actions", ["aiAssert", "aiBoolean", "aiQuery", "aiAsk"])
    data.setdefault("wait_actions", ["aiWaitFor", "sleep"])
    data.setdefault("forbidden_actions", ["check", "verify", "observe", "ensure", "检查", "确认", "观察", "验证"])
    data.setdefault("forbidden_phrases", ["检查是否正常", "确认是否正常", "观察页面", "验证功能正常"])
    return data


def _extract_midscene_tasks(parsed: Any) -> tuple[str, List[Any]]:
    if not isinstance(parsed, dict):
        return "", []
    if isinstance(parsed.get("tasks"), list):
        return "root", parsed.get("tasks") or []
    for platform in ("android", "ios"):
        node = parsed.get(platform)
        if isinstance(node, dict) and isinstance(node.get("tasks"), list):
            return platform, node.get("tasks") or []
    return "", []


def _action_keys(step: dict, allowed: Set[str]) -> List[str]:
    return [str(key) for key in step.keys() if key in allowed]


def _value_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _step_text(step: dict) -> str:
    parts = []
    for value in step.values():
        if isinstance(value, (str, int, float)):
            parts.append(str(value))
        elif isinstance(value, (list, dict)):
            parts.append(json.dumps(value, ensure_ascii=False)[:500])
    return " ".join(parts)


def _declared_variables(parsed: Any, tasks: List[Any]) -> Set[str]:
    declared: Set[str] = set()
    if isinstance(parsed, dict):
        for key in ("env", "variables", "data"):
            value = parsed.get(key)
            if isinstance(value, dict):
                declared.update(str(item) for item in value.keys())
    for task in tasks:
        if not isinstance(task, dict):
            continue
        for key in ("env", "variables", "data"):
            value = task.get(key)
            if isinstance(value, dict):
                declared.update(str(item) for item in value.keys())
    return declared


def validate_yaml_static_executable(yaml_text: str, *, strict: bool = False) -> dict:
    """Validate generated YAML against the executable action contract.

    ``ok`` means no blocking errors. Warnings still mark the YAML as
    ``needs_review`` so the UI can explain what may be flaky.
    """
    contract = load_yaml_action_contract()
    allowed = set(contract.get("allowed_actions") or [])
    forbidden = set(str(item).strip() for item in contract.get("forbidden_actions") or [] if str(item).strip())
    forbidden_phrases = [str(item) for item in contract.get("forbidden_phrases") or [] if str(item).strip()]
    result = {
        "ok": False,
        "executionLevel": "draft",
        "platform": "",
        "taskCount": 0,
        "errors": [],
        "warnings": [],
        "blockedActions": [],
        "unknownActions": [],
        "actionSummary": [],
        "assertCount": 0,
        "waitCount": 0,
        "launchGuard": False,
        "rule": "YAML 必须只使用平台动作白名单，并仿写已成功基线步骤；静态错误不会进入 Runner 自动执行。",
    }
    text = str(yaml_text or "")
    if not text.strip():
        result["errors"].append("YAML 内容为空")
        return result
    if _pyyaml is None:
        result["errors"].append("服务端未安装 PyYAML，无法做 YAML 静态可执行校验")
        return result
    try:
        parsed = _pyyaml.safe_load(text)
    except Exception as exc:
        result["errors"].append(f"YAML 解析失败：{exc}")
        return result
    if not isinstance(parsed, dict):
        result["errors"].append("YAML 根节点必须是对象")
        return result

    platform, tasks = _extract_midscene_tasks(parsed)
    result["platform"] = platform
    result["taskCount"] = len(tasks or [])
    if not platform:
        result["errors"].append("必须包含 root.tasks、android.tasks 或 ios.tasks")
        return result
    if not tasks:
        result["errors"].append(f"{platform}.tasks 不能为空")
        return result

    action_counter: Dict[str, int] = {}
    declared_variables = _declared_variables(parsed, tasks)
    used_variables = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text))
    unresolved = sorted(item for item in used_variables if item not in declared_variables and item not in os.environ)
    if unresolved:
        result["warnings"].append(f"存在未声明变量：{', '.join(unresolved[:8])}")

    for task_index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            result["errors"].append(f"tasks[{task_index}] 必须是对象")
            continue
        if not str(task.get("name") or "").strip():
            result["warnings"].append(f"tasks[{task_index}] 缺少 name，报告无法清楚定位用例")
        misplaced = [
            key for key in task.keys()
            if key not in TASK_LEVEL_ALLOWED_KEYS and key not in ("name", "flow")
        ]
        if misplaced:
            result["warnings"].append(f"tasks[{task_index}] 存在不建议放在 task 顶层的字段：{misplaced[:8]}")
        flow = task.get("flow")
        if not isinstance(flow, list) or not flow:
            result["errors"].append(f"tasks[{task_index}].flow 不能为空")
            continue
        task_has_assert = False
        task_has_wait = False
        for step_index, step in enumerate(flow, start=1):
            if not isinstance(step, dict) or not step:
                result["errors"].append(f"tasks[{task_index}].flow[{step_index}] 必须是非空对象")
                continue
            actions = _action_keys(step, allowed)
            unsupported_keys = [
                str(key) for key in step.keys()
                if key not in allowed and key not in STEP_ATTR_KEYS
            ]
            forbidden_keys = [key for key in step.keys() if str(key) in forbidden]
            if forbidden_keys:
                result["blockedActions"].extend(forbidden_keys)
                result["errors"].append(
                    f"tasks[{task_index}].flow[{step_index}] 使用了不可执行伪动作：{forbidden_keys}"
                )
            if not actions:
                result["unknownActions"].extend(unsupported_keys or [str(key) for key in step.keys()])
                result["errors"].append(
                    f"tasks[{task_index}].flow[{step_index}] 没有平台支持的 action：{sorted(step.keys())}"
                )
                continue
            if len(actions) > 1:
                result["errors"].append(f"tasks[{task_index}].flow[{step_index}] 同时声明多个 action：{actions}")
            if unsupported_keys:
                result["warnings"].append(
                    f"tasks[{task_index}].flow[{step_index}] 存在非标准字段：{unsupported_keys[:8]}"
                )
            for action in actions:
                action_counter[action] = action_counter.get(action, 0) + 1
                value = step.get(action)
                if action in ("ai", "aiAct", "aiAction", "aiTap", "aiAssert", "aiWaitFor") and _value_blank(value):
                    result["errors"].append(f"tasks[{task_index}].flow[{step_index}] {action} 内容不能为空")
                if action == "aiInput" and _value_blank(value) and _value_blank(step.get("value")):
                    result["errors"].append(f"tasks[{task_index}].flow[{step_index}] aiInput 必须包含输入目标或 value")
                if action in contract.get("assertion_actions", []):
                    task_has_assert = True
                    result["assertCount"] += 1
                if action in contract.get("wait_actions", []):
                    task_has_wait = True
                    result["waitCount"] += 1
                if action == "launch":
                    result["launchGuard"] = True
                if action in ("aiTap", "aiInput", "aiAction", "aiAct", "ai", "aiScroll", "launch"):
                    if not any(
                        isinstance(next_step, dict) and any(key in next_step for key in ("aiWaitFor", "sleep", "aiAssert"))
                        for next_step in flow[step_index: step_index + 3]
                    ):
                        result["warnings"].append(
                            f"tasks[{task_index}].flow[{step_index}] {action} 后缺少就近等待或断言，慢设备上容易失败"
                        )
                step_text = _step_text(step)
                for phrase in forbidden_phrases:
                    if phrase and phrase in step_text:
                        result["warnings"].append(
                            f"tasks[{task_index}].flow[{step_index}] 包含过泛描述“{phrase}”，建议改成具体页面状态或业务结果"
                        )
        if not task_has_assert:
            message = f"tasks[{task_index}] 缺少最终业务断言 aiAssert/aiBoolean/aiQuery"
            if strict:
                result["errors"].append(message)
            else:
                result["warnings"].append(message)
        if not task_has_wait:
            result["warnings"].append(f"tasks[{task_index}] 缺少 aiWaitFor/sleep 等待，执行稳定性偏低")

    result["blockedActions"] = sorted(set(result["blockedActions"]))
    result["unknownActions"] = sorted(set(result["unknownActions"]))
    result["actionSummary"] = [
        {"action": action, "count": count}
        for action, count in sorted(action_counter.items(), key=lambda item: (-item[1], item[0]))
    ]
    result["ok"] = not bool(result["errors"])
    if result["errors"]:
        result["executionLevel"] = "draft"
    elif result["warnings"]:
        result["executionLevel"] = "needs_review"
    else:
        result["executionLevel"] = "executable"
    return result
