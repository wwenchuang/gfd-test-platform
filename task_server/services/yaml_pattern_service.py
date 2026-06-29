"""Extract executable YAML patterns from existing baseline examples.

The generator should not treat historical YAML as raw prompt filler. This
module condenses similar examples into action sequences and reusable writing
rules so the model is constrained to runner-supported structures.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Tuple

try:
    import yaml as _pyyaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _pyyaml = None  # type: ignore

from task_server.schemas import MIDSCENE_FLOW_ACTIONS


def _extract_tasks(parsed: Any) -> List[dict]:
    if not isinstance(parsed, dict):
        return []
    if isinstance(parsed.get("tasks"), list):
        return [item for item in parsed.get("tasks") or [] if isinstance(item, dict)]
    for platform in ("android", "ios"):
        node = parsed.get(platform)
        if isinstance(node, dict) and isinstance(node.get("tasks"), list):
            return [item for item in node.get("tasks") or [] if isinstance(item, dict)]
    return []


def _parse_yaml_snippet(text: str) -> List[dict]:
    if _pyyaml is None or not str(text or "").strip():
        return []
    try:
        parsed = _pyyaml.safe_load(str(text or ""))
    except Exception:
        return []
    return _extract_tasks(parsed)


def _step_action(step: Any) -> str:
    if not isinstance(step, dict):
        return ""
    for key in step.keys():
        if key in MIDSCENE_FLOW_ACTIONS:
            return key
    return ""


def _step_label(step: Any) -> str:
    if not isinstance(step, dict):
        return ""
    action = _step_action(step)
    if not action:
        return ""
    value = step.get(action)
    if isinstance(value, str):
        return value.strip()[:80]
    locate = step.get("locate") or step.get("query") or step.get("prompt")
    return str(locate or "").strip()[:80]


def _pattern_from_task(task: dict, example: dict) -> dict:
    flow = task.get("flow") if isinstance(task.get("flow"), list) else []
    actions: List[str] = []
    waits: List[dict] = []
    assertions: List[dict] = []
    selectors: List[dict] = []
    structure: List[List[str]] = []
    labels: List[str] = []
    for step in flow:
        if not isinstance(step, dict):
            continue
        action = _step_action(step)
        if action:
            actions.append(action)
        structure.append([str(key) for key in step.keys()])
        label = _step_label(step)
        if label:
            labels.append(label)
        if action in ("aiWaitFor", "sleep"):
            waits.append({"action": action, "text": label})
        if action in ("aiAssert", "aiBoolean", "aiQuery", "aiAsk"):
            assertions.append({"action": action, "text": label})
        locate = step.get("locate") or step.get("query") or step.get("prompt")
        if locate or action in ("aiTap", "aiInput", "aiWaitFor", "aiAssert"):
            selectors.append({
                "action": action,
                "selector": str(locate or "").strip(),
                "aiQuery": label,
            })
    return {
        "title": task.get("name") or example.get("title") or example.get("file") or "未命名基线",
        "module": example.get("module") or "",
        "file": example.get("file") or "",
        "score": example.get("score") or 0,
        "matched_terms": example.get("matched_terms") or [],
        "actions": actions,
        "waits": waits[:5],
        "assertions": assertions[:5],
        "selectors": selectors[:8],
        "structure": structure[:20],
        "sample_labels": labels[:8],
        "baseline_path": example.get("baseline_path") or "",
    }


def extract_yaml_patterns_from_examples(examples: Iterable[dict], limit: int = 5) -> List[dict]:
    """Return Top-N executable writing patterns from retrieved YAML examples."""
    patterns: List[dict] = []
    seen: set[Tuple[str, ...]] = set()
    for example in list(examples or [])[: max(1, limit * 2)]:
        if not isinstance(example, dict):
            continue
        tasks = _parse_yaml_snippet(str(example.get("snippet") or ""))
        if not tasks:
            actions = [str(item) for item in example.get("actions") or [] if str(item).strip()]
            if not actions:
                continue
            pattern = {
                "title": example.get("title") or example.get("file") or "未命名基线",
                "module": example.get("module") or "",
                "file": example.get("file") or "",
                "score": example.get("score") or 0,
                "matched_terms": example.get("matched_terms") or [],
                "actions": actions,
                "waits": [],
                "assertions": [],
                "selectors": [],
                "structure": [],
                "sample_labels": [],
                "baseline_path": example.get("baseline_path") or "",
            }
            tasks_patterns = [pattern]
        else:
            tasks_patterns = [_pattern_from_task(task, example) for task in tasks]
        for pattern in tasks_patterns:
            actions = tuple(pattern.get("actions") or [])
            if not actions or actions in seen:
                continue
            seen.add(actions)
            patterns.append(pattern)
            if len(patterns) >= limit:
                return patterns
    return patterns


def summarize_yaml_patterns(patterns: Iterable[dict]) -> dict:
    actions = Counter()
    wait_count = 0
    assertion_count = 0
    for pattern in patterns or []:
        if not isinstance(pattern, dict):
            continue
        actions.update(pattern.get("actions") or [])
        wait_count += len(pattern.get("waits") or [])
        assertion_count += len(pattern.get("assertions") or [])
    return {
        "pattern_count": len([p for p in patterns or [] if isinstance(p, dict)]),
        "top_actions": [{"action": action, "count": count} for action, count in actions.most_common(12)],
        "wait_count": wait_count,
        "assertion_count": assertion_count,
    }


def build_yaml_pattern_contract_text(patterns: Iterable[dict], action_contract: dict) -> str:
    patterns = [item for item in patterns or [] if isinstance(item, dict)]
    allowed = [str(item) for item in action_contract.get("allowed_actions") or [] if str(item).strip()]
    forbidden = [str(item) for item in action_contract.get("forbidden_actions") or [] if str(item).strip()]
    lines = [
        "【YAML 生成契约：按可执行基线仿写】",
        "你是自动化执行 Agent，不是自由写测试设计文档。生成 YAML 时必须优先套用下面的基线动作模式，只替换业务对象、入口文案、等待条件和最终断言。",
        f"允许动作白名单：{', '.join(allowed)}",
        f"禁止动作/伪动作：{', '.join(forbidden)}",
        "硬性要求：",
        "1. 禁止生成白名单外 action，禁止把 check/verify/ensure/观察/检查 写成 flow 动作。",
        "2. 页面跳转、弹窗、上传、搜索、外部跳转后必须有 aiWaitFor 或明确状态等待。",
        "3. 每个 YAML 文件只覆盖一个可冒烟业务检查点，至少保留一个最终业务 aiAssert。",
        "4. 不确定或依赖人工判断的覆盖点进入 manual_cases/draft，不得标记为可执行 YAML。",
        "5. 不要复制无关历史断言；只学习动作组织方式和稳定等待写法。",
    ]
    if patterns:
        lines.append("")
        lines.append("【相似基线动作模式 Top5】")
    for idx, pattern in enumerate(patterns[:5], start=1):
        actions = " -> ".join(pattern.get("actions") or []) or "-"
        labels = "；".join(pattern.get("sample_labels") or [])[:260] or "-"
        matched = "、".join(pattern.get("matched_terms") or []) or "结构相近"
        lines.extend([
            f"{idx}. {pattern.get('title') or pattern.get('file')}",
            f"   来源：{pattern.get('file') or '-'}",
            f"   匹配：{matched}",
            f"   动作序列：{actions}",
            f"   关键文案/定位写法：{labels}",
        ])
    return "\n".join(lines).strip()
