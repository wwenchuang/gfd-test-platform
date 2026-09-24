"""Convert recorded Sonic actions and UI evidence into gated Midscene YAML."""

from __future__ import annotations

from typing import Any, Dict, List

import yaml

from .yaml_executable_scorer import score_midscene_yaml_executable
from .yaml_service import validate_midscene_yaml
from .device_recording_service import confirmed_recording_description


def _node_description(step: Dict[str, Any]) -> str:
    return confirmed_recording_description(step)


def normalize_recorded_step(step: Dict[str, Any]) -> Dict[str, Any]:
    action_type = str(step.get("type") or "").lower()
    result = {"flow": [], "requires_confirmation": False, "issue": ""}
    if action_type == "launch":
        package = str(step.get("package") or "").strip()
        if package:
            result["flow"] = [{"launch": package}]
        else:
            result.update(requires_confirmation=True, issue="启动步骤缺少应用包名")
    elif action_type == "tap":
        target = _node_description(step)
        if target:
            result["flow"] = [{"aiTap": target}]
            if step.get("screen_change_status") == "unchanged":
                result.update(requires_confirmation=True, issue="点击后手机画面未变化，请核对触摸是否生效并重录或删除该步")
        else:
            result.update(requires_confirmation=True, issue="点击控件尚未通过截图识别或人工确认，请重新识别或手动标记")
    elif action_type == "text":
        target = _node_description(step)
        if target:
            result["flow"] = [{"aiInput": target, "value": str(step.get("text") or "")}]
        else:
            result.update(requires_confirmation=True, issue="输入步骤缺少输入框语义")
    elif action_type == "swipe":
        start = step.get("start") if isinstance(step.get("start"), dict) else {}
        end = step.get("end") if isinstance(step.get("end"), dict) else {}
        dx = int(end.get("x") or 0) - int(start.get("x") or 0)
        dy = int(end.get("y") or 0) - int(start.get("y") or 0)
        if abs(dy) >= abs(dx):
            direction = "向上" if dy < 0 else "向下"
        else:
            direction = "向左" if dx < 0 else "向右"
        result["flow"] = [{"aiScroll": f"在当前页面{direction}滚动"}]
    elif action_type == "key":
        keys = {"BACK": "4", "HOME": "3", "ENTER": "66"}
        key = str(step.get("key") or "").upper()
        if key in keys:
            result["flow"] = [{"runAdbShell": f"input keyevent {keys[key]}"}]
        else:
            result.update(requires_confirmation=True, issue="按键步骤无法识别")
    elif action_type == "checkpoint":
        description = str(step.get("description") or "").strip()
        if description:
            action = "aiAssert" if step.get("checkpoint_kind") == "assert" else "aiWaitFor"
            result["flow"] = [{action: description}]
        else:
            result.update(requires_confirmation=True, issue="检查点缺少预期说明")
    else:
        result.update(requires_confirmation=True, issue=f"不支持的录制步骤：{action_type or '未知'}")
    return result


def generate_recording_yaml(session: Dict[str, Any], task_name: str = "录制生成用例") -> Dict[str, Any]:
    if str(session.get("status") or "") != "finished":
        raise ValueError("请先结束录制，再生成 YAML")
    if not any(step.get("type") != "checkpoint" for step in session.get("steps") or []):
        raise ValueError("没有记录到手机操作，不能生成空 YAML")
    flow: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    for index, step in enumerate(session.get("steps") or [], 1):
        normalized = normalize_recorded_step(step)
        flow.extend(normalized["flow"])
        if normalized["requires_confirmation"]:
            issues.append({"step": index, "message": normalized["issue"]})
    package = str(session.get("app_package") or "").strip()
    if package:
        flow = [{"launch": package}] + [item for item in flow if "launch" not in item]
    ready_flow: List[Dict[str, Any]] = []
    for item in flow:
        target = str(item.get("aiTap") or "").strip()
        ready_condition = f"页面中已出现可点击的“{target}”入口" if target else ""
        if target and not (
            ready_flow
            and "aiWaitFor" in ready_flow[-1]
            and ready_condition == str(ready_flow[-1]["aiWaitFor"])
        ):
            ready_flow.append({
                "aiWaitFor": ready_condition,
                "timeout": 12000,
            })
        ready_flow.append(item)
    flow = ready_flow
    normalized_task_name = str(task_name or "录制生成用例").strip()
    document = {"android": {}, "tasks": [{"name": normalized_task_name, "flow": flow}]}
    yaml_text = yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=120)
    validation = validate_midscene_yaml(yaml_text)
    score = score_midscene_yaml_executable(yaml_text, generated=True)
    for warning in validation.get("warnings") or []:
        issues.append({"step": 0, "message": str(warning)})
    return {
        "yaml": yaml_text,
        "task_name": normalized_task_name,
        "issues": issues,
        "requires_confirmation": any(item.get("step") for item in issues),
        "validation": validation,
        "score": score,
        "can_debug": not issues and bool(validation.get("ok")),
    }
