"""Extract executable YAML patterns from existing baseline examples.

The generator should not treat historical YAML as raw prompt filler. This
module condenses similar examples into action sequences and reusable writing
rules so the model is constrained to runner-supported structures.
"""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Dict, Iterable, List, Tuple

try:
    import yaml as _pyyaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _pyyaml = None  # type: ignore

from task_server.schemas import MIDSCENE_FLOW_ACTIONS


def _extract_tasks(parsed: Any) -> List[dict]:
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
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
        return _parse_task_block_by_regex(text)
    try:
        parsed = _pyyaml.safe_load(str(text or ""))
    except Exception:
        return _parse_task_block_by_regex(text)
    tasks = _extract_tasks(parsed)
    return tasks or _parse_task_block_by_regex(text)


def _clean_scalar(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if _pyyaml is not None:
        try:
            parsed = _pyyaml.safe_load(value)
            if isinstance(parsed, (str, int, float)):
                return str(parsed)
        except Exception:
            pass
    return value.strip().strip("\"'")


def _parse_task_block_by_regex(text: str) -> List[dict]:
    """Parse task snippets cut from a YAML file even when indentation changed."""
    raw = str(text or "")
    if not raw.strip():
        return []
    name_match = re.search(r"^\s*-\s+name\s*:\s*(.+?)\s*$", raw, flags=re.M)
    task_name = _clean_scalar(name_match.group(1)) if name_match else ""
    flow: List[dict] = []
    in_flow = False
    for line in raw.splitlines():
        if re.match(r"^\s*flow\s*:\s*$", line):
            in_flow = True
            continue
        if not in_flow:
            continue
        match = re.match(r"^\s*-\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not match:
            continue
        action = match.group(1)
        if action not in MIDSCENE_FLOW_ACTIONS:
            continue
        flow.append({action: _clean_scalar(match.group(2))})
    if not flow:
        return []
    return [{"name": task_name, "flow": flow}]


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
            actions = [
                str(item) for item in example.get("actions") or []
                if str(item).strip() in MIDSCENE_FLOW_ACTIONS
            ]
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
    pattern_items = [p for p in patterns or [] if isinstance(p, dict)]
    actions = Counter()
    wait_count = 0
    assertion_count = 0
    patterns_with_wait = 0
    patterns_with_assertion = 0
    for pattern in pattern_items:
        actions.update(pattern.get("actions") or [])
        waits = len(pattern.get("waits") or [])
        assertions = len(pattern.get("assertions") or [])
        wait_count += waits
        assertion_count += assertions
        if waits:
            patterns_with_wait += 1
        if assertions:
            patterns_with_assertion += 1
    return {
        "pattern_count": len(pattern_items),
        "top_actions": [{"action": action, "count": count} for action, count in actions.most_common(12)],
        "wait_count": wait_count,
        "assertion_count": assertion_count,
        "patterns_with_wait": patterns_with_wait,
        "patterns_with_assertion": patterns_with_assertion,
        "patterns_without_assertion": max(0, len(pattern_items) - patterns_with_assertion),
    }


def build_yaml_library_profile_text(patterns: Iterable[dict], profile: dict, total_examples: int = 0) -> str:
    patterns = [item for item in patterns or [] if isinstance(item, dict)]
    top_actions = ", ".join(
        f"{item.get('action')}({item.get('count')})"
        for item in (profile.get("top_actions") or [])[:10]
    ) or "-"
    lines = [
        "【全量基线库写法画像】",
        f"已扫描基线样本：{int(total_examples or len(patterns))} 条；提炼动作模式：{len(patterns)} 条。",
        f"高频动作：{top_actions}",
        f"包含等待/稳定停顿的模式：{profile.get('patterns_with_wait', 0)}；包含 aiAssert 的模式：{profile.get('patterns_with_assertion', 0)}；不含 aiAssert 的模式：{profile.get('patterns_without_assertion', 0)}。",
        "生成策略：优先学习全量基线里的启动清理、等待、点击、输入、外部跳转和终态判断写法；不要因为新用例缺少 aiAssert 就强行补断言。",
    ]
    if patterns:
        lines.append("全量基线代表模式：")
    for idx, pattern in enumerate(patterns[:8], start=1):
        actions = " -> ".join(pattern.get("actions") or []) or "-"
        lines.append(f"{idx}. {pattern.get('title') or pattern.get('file')}：{actions}")
    return "\n".join(lines).strip()


def build_yaml_pattern_contract_text(patterns: Iterable[dict], action_contract: dict) -> str:
    patterns = [item for item in patterns or [] if isinstance(item, dict)]
    allowed = [str(item) for item in action_contract.get("allowed_actions") or [] if str(item).strip()]
    forbidden = [str(item) for item in action_contract.get("forbidden_actions") or [] if str(item).strip()]
    lines = [
        "【YAML 生成契约：按可执行基线仿写】",
        "你是自动化执行 Agent，不是自由写测试设计文档。生成 YAML 时必须优先套用下面的基线动作模式，只替换业务对象、入口文案、等待条件和终态判断。",
        f"允许动作白名单：{', '.join(allowed)}",
        f"禁止动作/伪动作：{', '.join(forbidden)}",
        "硬性要求：",
        "1. 禁止生成白名单外 action，禁止把 check/verify/ensure/观察/检查 写成 flow 动作。",
        "2. 页面跳转、弹窗、上传、搜索、外部跳转后必须有 aiWaitFor 或明确状态等待。",
        "3. 每个 YAML 文件只覆盖一个可执行业务检查点；冒烟只是首批筛选标签，不等于自动化总量上限。",
        "4. 不确定或依赖人工判断的覆盖点进入 manual_cases/draft，不得标记为可执行 YAML。",
        "5. 生成完整可执行集时仍要按基线短路径组织：启动/到达入口/核心动作/等待终态/清理，不复制无关历史断言。",
    ]
    if patterns:
        lines.append("")
        lines.append("【相似基线动作模式 Top3】")
    for idx, pattern in enumerate(patterns[:3], start=1):
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
