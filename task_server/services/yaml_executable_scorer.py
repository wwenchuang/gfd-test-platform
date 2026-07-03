"""Executable quality gate for generated Midscene YAML."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

try:
    import yaml as _pyyaml  # type: ignore
except Exception:  # pragma: no cover
    _pyyaml = None  # type: ignore

from task_server.schemas import MIDSCENE_FLOW_ACTIONS


TRANSITION_ACTIONS = {"aiTap", "aiInput", "ai", "aiAction", "aiAct", "aiScroll"}
WAIT_ACTIONS = {"aiWaitFor", "sleep"}
START_GUARD_WORDS = ("首页", "入口", "已加载", "加载完成", "底部导航", "AI建模", "模型库", "课程", "我的")
SMOKE_WORDS = ("冒烟", "P0", "P1", "入口", "主流程", "核心", "基础", "跳转", "展示")
SMOKE_EXCLUDE_WORDS = (
    "未安装", "拒绝授权", "授权失败", "非会员", "权限", "边界", "异常", "降级",
    "防抖", "重复", "历史", "返回", "缓存", "弱网", "超时", "宽屏", "多设备",
    "一致性", "弹窗", "拦截", "失败", "错误", "不可用", "无数据", "空状态", "极限",
)
SMOKE_STRONG_WORDS = ("冒烟", "P0", "P1", "主流程", "主链", "核心", "基础")
SMOKE_NAME_WORDS = ("入口", "展示")
MAIN_CHAIN_WORDS = ("主流程", "主链", "核心", "入口", "开始创作", "AI建模", "图片建模", "文字建模", "语音创作", "生成模型")
VAGUE_PHRASES = (
    "检查是否正常", "确认是否正常", "页面正常", "功能正常", "验证功能正常",
    "验证页面正确", "检查页面正确", "页面正确", "结果正常", "状态正常",
)
NON_TAP_INTENT_WORDS = (
    "检查", "验证", "确认是否", "是否展示", "是否显示", "是否存在",
    "页面展示", "页面显示", "页面稳定展示", "文案清晰", "可见", "存在",
)
TAP_ACTION_WORDS = (
    "点击", "点按", "轻触", "选择", "进入", "打开", "切换", "返回", "关闭",
    "上传", "提交", "下一步", "上一步", "确认打印", "确认按钮", "确定按钮",
    "开始", "重试", "刷新", "搜索", "滑动", "滚动", "长按", "勾选", "取消",
    "保存", "下载",
)
ASSERTION_CONTEXT_WORDS = (
    "是否", "页面", "展示", "显示", "存在", "可见", "加载", "稳定",
    "正确", "一致", "结果", "文案", "状态",
)
GENERIC_QUERY_WORDS = ("页面", "按钮", "元素", "内容", "状态", "结果", "区域", "入口")
ACTION_PREFIX_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)$", re.S)
MANUAL_HINT_WORDS = (
    "人工", "手工", "manual", "肉眼", "视觉还原", "设计稿一致", "UI一致",
    "真实支付", "真实扣费", "后台造数", "线下确认", "外部人工",
)
# Generated baseline metadata comments are trace data, not proof that the case
# matched a successful baseline. Treat only explicit template/reference wording
# as baseline execution evidence.
BASELINE_HINT_RE = re.compile(r"(命中基线|相似基线|基线模板|matched\s*baseline|from\s*baseline|参考样例)", re.I)
PRIORITY_RE = re.compile(r"\b(P[0-3])\b", re.I)


def _extract_tasks(parsed: Any) -> Tuple[str, List[Any]]:
    if not isinstance(parsed, dict):
        return "", []
    root_tasks = parsed.get("ta" + "sks")
    if isinstance(root_tasks, list):
        return "root", root_tasks or []
    for platform in ("android", "ios"):
        node = parsed.get(platform)
        if isinstance(node, dict) and isinstance(node.get("tasks"), list):
            return platform, node.get("tasks") or []
    return "", []


def _step_text(step: Any) -> str:
    if not isinstance(step, dict):
        return ""
    parts = []
    for value in step.values():
        if isinstance(value, (str, int, float)):
            parts.append(str(value))
    return " ".join(parts)


def _step_actions(step: Any) -> List[str]:
    if not isinstance(step, dict):
        return []
    return [str(key) for key in step.keys() if key in MIDSCENE_FLOW_ACTIONS]


def _has_start_guard(flow: List[Any]) -> bool:
    for step in flow[:6]:
        if not isinstance(step, dict):
            continue
        text = _step_text(step)
        if "launch" in step:
            return True
        shell = str(step.get("runAdbShell") or "")
        if "am force-stop" in shell or "monkey" in shell:
            return True
        if "aiWaitFor" in step and any(word in text for word in START_GUARD_WORDS):
            return True
    return False


def _has_previous_wait(flow: List[Any], index: int) -> bool:
    for prev in flow[max(0, index - 4):index]:
        if isinstance(prev, dict) and any(action in prev for action in WAIT_ACTIONS):
            return True
    return False


def _has_followup_wait_or_terminal(flow: List[Any], index: int) -> bool:
    for nxt in flow[index + 1:index + 4]:
        if isinstance(nxt, dict) and any(action in nxt for action in ("aiWaitFor", "sleep", "aiAssert", "ai", "aiAction")):
            return True
    return False


def _task_smoke_candidate(task: Dict[str, Any]) -> bool:
    name_text = " ".join([
        str(task.get("name") or ""),
        str(task.get("priority") or ""),
        str(task.get("tags") or ""),
    ])
    if _has_smoke_exclusion(name_text):
        return False
    if any(word in name_text for word in SMOKE_STRONG_WORDS):
        return True
    return any(word in name_text for word in SMOKE_NAME_WORDS)


def _has_smoke_exclusion(text: str) -> bool:
    return any(word in str(text or "") for word in SMOKE_EXCLUDE_WORDS)


def _ref_smoke_excluded(item: dict, score: dict, task_scores: List[dict]) -> bool:
    label = " ".join([
        str(item.get("file") or ""),
        str(item.get("module") or ""),
        str(score.get("reason") or ""),
        " ".join(str(task.get("name") or "") for task in task_scores),
        " ".join(" ".join(str(reason) for reason in (task.get("reasons") or [])) for task in task_scores),
    ])
    return _has_smoke_exclusion(label)


def _ref_has_smoke_priority(item: dict, task_scores: List[dict]) -> bool:
    label = " ".join([
        str(item.get("file") or ""),
        str(item.get("module") or ""),
        str(item.get("priority") or ""),
        " ".join(str(task.get("priority") or "") for task in task_scores),
    ])
    match = PRIORITY_RE.search(label)
    return bool(match and match.group(1).upper() in ("P0", "P1"))


def _task_priority(task: Dict[str, Any], fallback_text: str = "") -> str:
    blob = " ".join([
        str(task.get("priority") or ""),
        str(task.get("name") or ""),
        str(task.get("tags") or ""),
        fallback_text,
    ])
    match = PRIORITY_RE.search(blob)
    return match.group(1).upper() if match else ""


def _has_main_chain_signal(task: Dict[str, Any]) -> bool:
    text = str(task.get("name") or "") + " " + " ".join(_step_text(step) for step in task.get("flow") or [])
    return any(word in text for word in MAIN_CHAIN_WORDS)


def _manual_hint(task: Dict[str, Any]) -> bool:
    text = " ".join([
        str(task.get("executionLevel") or ""),
        str(task.get("level") or ""),
        str(task.get("type") or ""),
        str(task.get("name") or ""),
        str(task.get("reason") or ""),
        " ".join(_step_text(step) for step in task.get("flow") or []),
    ]).lower()
    return any(word.lower() in text for word in MANUAL_HINT_WORDS)


def _ai_query_too_generic(text: str) -> bool:
    query = re.sub(r"\s+", "", str(text or ""))
    if len(query) < 6:
        return True
    if any(phrase in query for phrase in VAGUE_PHRASES):
        return True
    compact = re.sub(r"[，。！？、,.!?;；:：\"'“”‘’（）()【】\[\]\s]", "", query)
    return compact in GENERIC_QUERY_WORDS


def _tap_prompt_looks_assertion(text: str) -> bool:
    prompt = str(text or "").strip()
    compact = re.sub(r"\s+", "", prompt)
    if not compact:
        return False
    if any(word in compact for word in TAP_ACTION_WORDS):
        return False
    if compact.startswith(("检查", "验证")):
        return True
    if compact.startswith(("确认", "等待")) and any(word in compact for word in ASSERTION_CONTEXT_WORDS):
        return True
    return any(word in compact for word in NON_TAP_INTENT_WORDS)


def tap_prompt_looks_assertion(text: str) -> bool:
    """Public helper shared by Agent-side local YAML repair."""
    return _tap_prompt_looks_assertion(text)


def score_midscene_yaml_executable(yaml_text: str, *, generated: bool = True) -> dict:
    """Score whether YAML is safe enough to auto-send to Runner.

    Static parsers answer "can this YAML load"; this gate answers "should a
    newly generated case be auto-executed now". Existing hand-maintained
    baselines can still run through their normal path.
    """
    text = str(yaml_text or "")
    result = {
        "ok": False,
        "score": 0,
        "executionLevel": "draft",
        "level": "draft",
        "platform": "",
        "taskCount": 0,
        "errors": [],
        "warnings": [],
        "reasons": [],
        "taskScores": [],
        "smokeCandidate": False,
        "baselineEvidence": bool(BASELINE_HINT_RE.search(text)),
        "rule": "Agent 自动生成 YAML 只有达到 executable 才会下发 Runner；needs_review/draft 留作人工确认或后续扩展。",
    }
    if not text.strip():
        result["errors"].append("YAML 内容为空")
        return result
    if _pyyaml is None:
        result["errors"].append("服务端未安装 PyYAML，无法评分")
        return result
    try:
        parsed = _pyyaml.safe_load(text)
    except Exception as exc:
        result["errors"].append(f"YAML 解析失败：{exc}")
        return result
    platform, tasks = _extract_tasks(parsed)
    result["platform"] = platform
    result["taskCount"] = len(tasks or [])
    if not platform:
        result["errors"].append("必须包含 root.tasks、android.tasks 或 ios.tasks")
        return result
    if not tasks:
        result["errors"].append(f"{platform}.tasks 不能为空")
        return result

    total_score = 0
    task_scores = []
    for idx, task in enumerate(tasks, start=1):
        task_name = str(task.get("name") or f"tasks[{idx}]").strip() if isinstance(task, dict) else f"tasks[{idx}]"
        score = 100
        errors: List[str] = []
        warnings: List[str] = []
        task_text = ""
        baseline_evidence = bool(result["baselineEvidence"])
        if not isinstance(task, dict):
            errors.append("任务不是对象")
            score = 0
            flow = []
        else:
            flow = task.get("flow") if isinstance(task.get("flow"), list) else []
            task_text = str(task.get("name") or "") + " " + " ".join(_step_text(step) for step in flow)
            baseline_evidence = baseline_evidence or bool(BASELINE_HINT_RE.search(task_text))
            if not task_name:
                warnings.append("缺少任务名，报告中无法定位用例")
                score -= 5
            if not flow:
                errors.append("flow 为空")
                score = 0

        action_count = 0
        wait_count = 0
        assert_count = 0
        transition_count = 0
        unguarded_taps = 0
        missing_followups = 0
        vague_steps = 0
        generic_queries = 0
        non_tap_intents = 0
        nested_action_prefixes = 0
        manual_hint = _manual_hint(task) if isinstance(task, dict) else False
        start_guard = _has_start_guard(flow)
        if flow and not start_guard:
            warnings.append("缺少稳定起点/启动守卫，可能依赖上一个页面状态")
            score -= 25 if generated else 10
        for step_index, step in enumerate(flow):
            actions = _step_actions(step)
            if not actions:
                errors.append(f"flow[{step_index + 1}] 没有平台支持的动作")
                score -= 30
                continue
            action_count += len(actions)
            if any(action in WAIT_ACTIONS for action in actions):
                wait_count += 1
            if "aiAssert" in actions:
                assert_count += 1
            if any(action in TRANSITION_ACTIONS for action in actions):
                transition_count += 1
                if "aiTap" in actions and not (baseline_evidence or _has_previous_wait(flow, step_index)):
                    unguarded_taps += 1
                if "aiTap" in actions and _tap_prompt_looks_assertion(_step_text(step)):
                    non_tap_intents += 1
                if not _has_followup_wait_or_terminal(flow, step_index):
                    missing_followups += 1
            step_text = _step_text(step)
            for action in actions:
                action_value = step.get(action) if isinstance(step, dict) else None
                if action == "runAdbShell" and isinstance(action_value, str) and re.search(r"\$\{[^}]+\}", action_value):
                    errors.append(
                        f"flow[{step_index + 1}] runAdbShell 包含 `${{...}}` shell 参数展开，Midscene 会按环境变量插值解析"
                    )
                    score -= 55
                if isinstance(action_value, str):
                    prefix_match = ACTION_PREFIX_RE.match(action_value)
                    if prefix_match and prefix_match.group(1) in MIDSCENE_FLOW_ACTIONS:
                        nested_action_prefixes += 1
                        prefix = prefix_match.group(1)
                        if prefix == action:
                            warnings.append(f"flow[{step_index + 1}] {action} 内容重复包含动作前缀 `{prefix}:`")
                            score -= 12
                        else:
                            errors.append(f"flow[{step_index + 1}] 声明为 {action}，但内容前缀是 `{prefix}:`")
                            score -= 40
            if any(phrase in step_text for phrase in VAGUE_PHRASES):
                vague_steps += 1
            if "aiQuery" in actions and _ai_query_too_generic(step_text):
                generic_queries += 1
        if action_count < 3:
            warnings.append("步骤过少，无法形成稳定的冒烟路径")
            score -= 20
        if wait_count == 0:
            warnings.append("缺少 aiWaitFor/sleep 等待，页面加载慢时容易失败")
            score -= 25
        if assert_count == 0:
            warnings.append("缺少 aiAssert 明确业务结果，Runner 只能判断流程是否走完")
            score -= 12
        if unguarded_taps:
            warnings.append(f"{unguarded_taps} 个 aiTap 前缺少就近 aiWaitFor/sleep 或成功基线依据")
            score -= min(35, 12 * unguarded_taps)
        if non_tap_intents:
            warnings.append(f"{non_tap_intents} 个 aiTap 描述像检查/断言，不应点击；应改为 aiWaitFor 或 aiAssert")
            score -= min(45, 30 * non_tap_intents)
        if missing_followups:
            warnings.append(f"{missing_followups} 个交互动作后缺少等待或终态判断")
            score -= min(25, 6 * missing_followups)
        if assert_count > 3:
            warnings.append(f"aiAssert 数量 {assert_count} 偏多，容易把 UI 差异放大成失败")
            score -= min(20, 4 * (assert_count - 3))
        if transition_count > 8:
            warnings.append(f"交互动作 {transition_count} 个偏多，建议拆成更短的自动化链路")
            score -= min(20, 3 * (transition_count - 8))
        if vague_steps:
            warnings.append(f"{vague_steps} 个步骤描述过泛")
            score -= min(15, 5 * vague_steps)
        if generic_queries:
            warnings.append(f"{generic_queries} 个 aiQuery 过短或过泛，容易定位不到真实元素")
            score -= min(20, 10 * generic_queries)
        if nested_action_prefixes:
            warnings.append(f"{nested_action_prefixes} 个步骤内容嵌套了动作名前缀，需要先规范化再执行")
        if len(flow) > 36:
            warnings.append("单条用例步骤过长，建议拆分后执行")
            score -= 15

        score = max(0, min(100, score))
        level = "draft" if errors or score < 55 else ("needs_review" if score < 78 or warnings else "executable")
        if manual_hint and not errors and level != "executable":
            level = "manual"
        reasons = errors + warnings
        task_score = {
            "name": task_name,
            "score": score,
            "executionLevel": level,
            "level": level,
            "reasons": reasons,
            "errors": errors,
            "warnings": warnings,
            "actionCount": action_count,
            "transitionCount": transition_count,
            "waitCount": wait_count,
            "assertCount": assert_count,
            "startGuard": start_guard,
            "baselineEvidence": baseline_evidence,
            "priority": _task_priority(task, task_text) if isinstance(task, dict) else "",
            "mainBusinessChain": _has_main_chain_signal(task) if isinstance(task, dict) else False,
            "manualHint": manual_hint,
            "smokeCandidate": _task_smoke_candidate(task) if isinstance(task, dict) else False,
        }
        task_scores.append(task_score)
        total_score += score

    result["taskScores"] = task_scores
    result["score"] = int(round(total_score / max(1, len(task_scores))))
    result["errors"] = [f"{item['name']}: {err}" for item in task_scores for err in item.get("errors") or []]
    result["warnings"] = [f"{item['name']}: {warn}" for item in task_scores for warn in item.get("warnings") or []]
    result["reasons"] = result["errors"] + result["warnings"]
    result["smokeCandidate"] = any(item.get("smokeCandidate") for item in task_scores)
    if result["errors"]:
        result["executionLevel"] = "draft"
    elif all(item.get("executionLevel") == "executable" for item in task_scores):
        result["executionLevel"] = "executable"
    elif any(item.get("executionLevel") == "draft" for item in task_scores):
        result["executionLevel"] = "draft"
    elif all(item.get("executionLevel") == "manual" for item in task_scores):
        result["executionLevel"] = "manual"
    else:
        result["executionLevel"] = "needs_review"
    result["level"] = result["executionLevel"]
    result["ok"] = result["executionLevel"] == "executable"
    return result


def rank_executable_yaml_refs(scored_refs: List[dict], *, limit: int = 3) -> Tuple[List[dict], List[dict]]:
    """Return executable refs for first Runner batch and blocked refs."""
    eligible = []
    candidates = []
    blocked = []
    for item in scored_refs:
        score = item.get("executableScore") if isinstance(item.get("executableScore"), dict) else {}
        if score.get("executionLevel") != "executable":
            blocked.append({**item, "gateReason": "执行等级不是 executable"})
        else:
            task_scores = [task for task in (score.get("taskScores") or []) if isinstance(task, dict)]
            smoke_candidate = bool(
                item.get("smoke") is True
                or item.get("is_smoke") is True
                or item.get("isSmoke") is True
                or item.get("smokeCandidate") is True
                or item.get("runnerCandidate") is True
                or score.get("smokeCandidate") is True
                or _ref_has_smoke_priority(item, task_scores)
                or any(task.get("smokeCandidate") for task in task_scores)
            )
            smoke_excluded = _ref_smoke_excluded(item, score, task_scores)
            if smoke_excluded:
                smoke_candidate = False
            row = {**item, "smokeCandidate": smoke_candidate, "runnerCandidate": smoke_candidate, "smokeExcluded": smoke_excluded}
            eligible.append(row)
            if smoke_candidate:
                candidates.append(row)

    if candidates:
        executable = candidates
        candidate_ids = {id(item) for item in candidates}
        for item in eligible:
            if id(item) not in candidate_ids:
                reason = "异常/边界/权限类用例不进入首批冒烟，待首批通过后再扩展执行" if item.get("smokeExcluded") else "非首批冒烟候选，待首批通过后再扩展执行"
                blocked.append({**item, "gateReason": reason})
    else:
        fallback_pool = [item for item in eligible if not item.get("smokeExcluded")]
        if fallback_pool:
            executable = [{**item, "fallbackSmokeSelection": True} for item in fallback_pool]
            fallback_ids = {id(item) for item in fallback_pool}
            for item in eligible:
                if id(item) not in fallback_ids:
                    blocked.append({**item, "gateReason": "异常/边界/权限类用例不进入首批冒烟，待首批通过后再扩展执行"})
        else:
            executable = [{**item, "fallbackSmokeSelection": True} for item in eligible]

    def sort_key(item: dict):
        score = item.get("executableScore") if isinstance(item.get("executableScore"), dict) else {}
        task_scores = [task for task in (score.get("taskScores") or []) if isinstance(task, dict)]
        label = f"{item.get('file') or ''} {item.get('module') or ''} " + " ".join(str(task.get("name") or "") for task in task_scores)
        priority_text = " ".join([label] + [str(task.get("priority") or "") for task in task_scores])
        priority_match = PRIORITY_RE.search(priority_text)
        priority = priority_match.group(1).upper() if priority_match else ""
        priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(priority, 4)
        main_chain = bool(any(task.get("mainBusinessChain") for task in task_scores) or any(word in label for word in MAIN_CHAIN_WORDS))
        baseline = bool(score.get("baselineEvidence") or any(task.get("baselineEvidence") for task in task_scores))
        smoke_excluded = bool(item.get("smokeExcluded") or _has_smoke_exclusion(label))
        return (
            1 if smoke_excluded else 0,
            priority_rank,
            0 if main_chain else 1,
            0 if baseline else 1,
            -int(score.get("score") or 0),
            str(item.get("file") or ""),
        )

    ranked = sorted(executable, key=sort_key)
    limit = max(1, int(limit or 3))
    selected = ranked[:limit]
    overflow = ranked[limit:]
    blocked.extend({**item, "gateReason": f"超过自动冒烟首批上限 {limit}，待首批通过后再扩展执行"} for item in overflow)
    return selected, blocked
