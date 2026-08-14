"""Mindmap case test report service.

This service builds formal test-report artifacts from generated case-set
summaries without changing the existing Midscene Runner report index.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..config import CASE_DIR as _CONFIG_CASE_DIR, LEARNING_DIR as _CONFIG_LEARNING_DIR
from ..storage import clean_asset_filename, clean_id, read_json_file, unique_millis_id, write_json_file, write_text_file

CASE_DIR = _CONFIG_CASE_DIR
LEARNING_DIR = _CONFIG_LEARNING_DIR
TEST_REPORT_INDEX_FILE = os.path.join(LEARNING_DIR, "test-report-index.json")
TEST_REPORT_TEMPLATE_DIR = os.path.join(LEARNING_DIR, "test-report-templates")

REPORT_STATUS_TEXT = {
    "passed": "通过",
    "failed": "失败",
    "blocked": "阻塞",
    "not_executed": "未执行",
}

TEMPLATE_PLACEHOLDERS = (
    "report_title",
    "title",
    "test_start",
    "test_end",
    "tester",
    "client_side",
    "version",
    "environment",
    "requirement_link",
    "case_link",
    "test_goal",
    "test_scope",
    "test_points",
    "mindmap_list",
    "source_count",
    "conclusion_summary",
    "summary_table",
    "defect_table",
    "case_table",
    "failure_table",
    "manual_case_table",
    "quality_assessment",
    "release_suggestion",
    "generated_at",
)


class TestReportError(ValueError):
    """User-facing report generation error."""


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _case_set_dir(case_set_id: str) -> str:
    return os.path.join(CASE_DIR, clean_id(case_set_id, "case_set"))


def _summary_path(case_set_id: str) -> str:
    return os.path.join(_case_set_dir(case_set_id), "summary.json")


def _report_dir(case_set_id: str, report_id: str) -> str:
    return os.path.join(_case_set_dir(case_set_id), "test-reports", clean_id(report_id, "report"))


def _merged_report_dir(report_id: str) -> str:
    return os.path.join(CASE_DIR, "merged-test-reports", clean_id(report_id, "report"))


def _template_index_file() -> str:
    return os.path.join(_template_dir(), "index.json")


def _template_dir() -> str:
    configured = str(TEST_REPORT_TEMPLATE_DIR or "").strip()
    default_dir = os.path.join(_CONFIG_LEARNING_DIR, "test-report-templates")
    if configured and configured != default_dir:
        return configured
    return os.path.join(LEARNING_DIR, "test-report-templates")


def _load_summary(case_set_id: str) -> Dict[str, Any]:
    case_set_id = str(case_set_id or "").strip()
    if not case_set_id:
        raise TestReportError("case_set_id 不能为空")
    summary = read_json_file(_summary_path(case_set_id), default=None)
    if not isinstance(summary, dict):
        raise TestReportError("脑图记录不存在或已删除")
    return summary


def _case_set_ids(value: Any) -> List[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,，\s]+", str(value or ""))
    rows: List[str] = []
    seen = set()
    for item in raw:
        case_set_id = str(item or "").strip()
        if not case_set_id or case_set_id in seen:
            continue
        rows.append(case_set_id)
        seen.add(case_set_id)
    return rows


def _payload_case_set_ids(payload: Dict[str, Any]) -> List[str]:
    ids = _case_set_ids(payload.get("case_set_ids") or payload.get("caseSetIds"))
    if ids:
        return ids
    return _case_set_ids(payload.get("case_set_id") or payload.get("caseSetId") or payload.get("id"))


def _text(value: Any, default: str = "") -> str:
    value = "" if value is None else str(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or default


def _text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        rows: List[str] = []
        for item in value:
            if isinstance(item, dict):
                text = _text(item.get("step") or item.get("action") or item.get("description") or item.get("name"))
            else:
                text = _text(item)
            if text:
                rows.append(text)
        return rows
    text = _text(value)
    if not text:
        return []
    return [line.strip() for line in re.split(r"[\n；;]+", text) if line.strip()]


def _priority(row: Dict[str, Any]) -> str:
    return _text(row.get("priority") or row.get("level"), "P2").upper()


def _is_smoke(row: Dict[str, Any]) -> bool:
    if row.get("smoke") is True or row.get("is_smoke") is True or row.get("isSmoke") is True:
        return True
    values: List[str] = []
    for key in ("flag", "flags", "tags"):
        value = row.get(key)
        if isinstance(value, list):
            values.extend(_text(item) for item in value)
        else:
            values.append(_text(value))
    return "冒烟" in " ".join(values)


def _case_id(row: Dict[str, Any], prefix: str, index: int) -> str:
    value = _text(row.get("case_id") or row.get("caseId") or row.get("id"))
    return value or f"{prefix}-{index:03d}"


def _normalize_case(row: Dict[str, Any], index: int, *, source_type: str, summary: Dict[str, Any]) -> Dict[str, Any]:
    case_id = _case_id(row, "MT" if source_type == "manual" else "TC", index)
    priority = _priority(row)
    smoke = _is_smoke(row)
    feature = _text(row.get("feature") or row.get("module") or row.get("business_feature") or summary.get("module"), "未分组功能")
    scenario = _text(row.get("scenario") or row.get("scene") or row.get("name"), "未分组场景")
    title = _text(row.get("title") or row.get("case_name") or row.get("name"), "未命名用例")
    assertions = _text_list(row.get("assertions") or row.get("expects") or row.get("expect"))
    expected = _text(row.get("expected_result") or row.get("expectedResult") or row.get("expected"))
    if expected and expected not in assertions:
        assertions.insert(0, expected)
    default_selected = source_type != "manual" and (priority in {"P0", "P1"} or smoke)
    return {
        "case_id": case_id,
        "title": title,
        "priority": priority,
        "smoke": smoke,
        "feature": feature,
        "scenario": scenario,
        "source_type": source_type,
        "default_selected": default_selected,
        "steps": _text_list(row.get("steps") or row.get("flow")),
        "assertions": assertions,
        "expected_result": expected,
        "risk": _text(row.get("risk") or row.get("reason") or row.get("business_risk")),
        "data_requirements": _text(row.get("data_requirements") or row.get("dataRequirements") or row.get("test_data")),
    }


def _summary_cases(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, row in enumerate(summary.get("cases") or [], start=1):
        if isinstance(row, dict):
            rows.append(_normalize_case(row, index, source_type="automation", summary=summary))
    manual_start = 1
    for index, row in enumerate(summary.get("manual_cases") or [], start=manual_start):
        if isinstance(row, dict):
            rows.append(_normalize_case(row, index, source_type="manual", summary=summary))
    refs = _summary_yaml_refs(summary)
    for row in rows:
        ref = refs.get(str(row.get("case_id") or ""))
        if ref:
            row["yaml_file"] = ref.get("file") or ""
            row["target_task_name"] = ref.get("target_task_name") or ""
    return rows


def _source_title(summary: Dict[str, Any], case_set_id: str) -> str:
    return _text(summary.get("title"), case_set_id)


def _source_row(case_set_id: str, summary: Dict[str, Any], cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "case_set_id": case_set_id,
        "title": _source_title(summary, case_set_id),
        "module": _text(summary.get("module")),
        "generated_at": _text(summary.get("generated_at")),
        "case_count": len(cases),
    }


def _selection_id(case_set_id: str, case_id: str) -> str:
    return f"{case_set_id}::{case_id}"


def _decorate_source_cases(case_set_id: str, summary: Dict[str, Any], cases: List[Dict[str, Any]], *, multi: bool) -> List[Dict[str, Any]]:
    title = _source_title(summary, case_set_id)
    module = _text(summary.get("module"))
    rows: List[Dict[str, Any]] = []
    for case in cases:
        row = dict(case)
        case_id = _text(row.get("case_id"))
        row["case_set_id"] = case_set_id
        row["source_title"] = title
        row["source_module"] = module
        row["selection_id"] = _selection_id(case_set_id, case_id)
        row["display_id"] = f"{title} / {case_id}" if multi else case_id
        rows.append(row)
    return rows


def _load_sources(case_set_ids: List[str]) -> List[Dict[str, Any]]:
    if not case_set_ids:
        raise TestReportError("case_set_id 不能为空")
    raw_sources: List[Dict[str, Any]] = []
    for case_set_id in case_set_ids:
        summary = _load_summary(case_set_id)
        cases = _summary_cases(summary)
        raw_sources.append({"case_set_id": case_set_id, "summary": summary, "cases": cases})
    multi = len(raw_sources) > 1
    sources: List[Dict[str, Any]] = []
    for source in raw_sources:
        decorated = _decorate_source_cases(source["case_set_id"], source["summary"], source["cases"], multi=multi)
        sources.append({
            **source,
            "cases": decorated,
            "meta": _source_row(source["case_set_id"], source["summary"], decorated),
        })
    return sources


def _summary_yaml_refs(summary: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    refs: Dict[str, Dict[str, str]] = {}
    groups = summary.get("generatedCaseGroups") or summary.get("generated_case_groups") or {}
    if isinstance(groups, dict):
        buckets = [
            groups.get("executable_cases"),
            groups.get("needs_review_cases"),
            groups.get("draft_cases"),
            groups.get("manual_cases"),
        ]
        for bucket in buckets:
            for row in bucket or []:
                if not isinstance(row, dict):
                    continue
                case_id = _text(row.get("case_id") or row.get("caseId") or row.get("id"))
                file_name = _text(row.get("file") or row.get("yaml_file") or row.get("yamlFile"))
                if case_id and file_name and case_id not in refs:
                    refs[case_id] = {
                        "file": file_name,
                        "target_task_name": _text(row.get("target_task_name") or row.get("targetTaskName")),
                    }
    return refs


def _group_cases(cases: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    group_map: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        feature = case.get("feature") or "未分组功能"
        scenario = case.get("scenario") or "未分组场景"
        group = group_map.get(feature)
        if not group:
            group = {"feature": feature, "scenarios": [], "case_count": 0}
            group_map[feature] = group
            groups.append(group)
        scenario_row = next((item for item in group["scenarios"] if item.get("scenario") == scenario), None)
        if not scenario_row:
            scenario_row = {"scenario": scenario, "cases": [], "case_count": 0}
            group["scenarios"].append(scenario_row)
        scenario_row["cases"].append(case)
        scenario_row["case_count"] += 1
        group["case_count"] += 1
    return groups


def load_reportable_cases(case_set_id: str = "", case_set_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    ids = _case_set_ids(case_set_ids) if case_set_ids is not None else _case_set_ids(case_set_id)
    sources = _load_sources(ids)
    cases = [case for source in sources for case in source.get("cases") or []]
    automation_count = len([case for case in cases if case.get("source_type") == "automation"])
    manual_count = len([case for case in cases if case.get("source_type") == "manual"])
    primary = sources[0]
    source_metas = [source["meta"] for source in sources]
    return {
        "ok": True,
        "case_set_id": primary["case_set_id"],
        "case_set_ids": ids,
        "source_count": len(sources),
        "sources": source_metas,
        "title": primary["meta"]["title"] if len(sources) == 1 else f"{primary['meta']['title']} 等 {len(sources)} 个脑图",
        "module": primary["meta"]["module"] if len(sources) == 1 else "多脑图合并",
        "generated_at": primary["meta"]["generated_at"],
        "counts": {
            "total_case_count": len(cases),
            "automation_case_count": automation_count,
            "manual_case_count": manual_count,
            "default_selected_count": len([case for case in cases if case.get("default_selected")]),
        },
        "groups": _group_cases(cases),
        "cases": cases,
        "templates": list_test_report_templates(),
        "reports": list_test_reports(case_set_id=primary["case_set_id"], limit=20),
    }


def _meta(sources: List[Dict[str, Any]], payload: Dict[str, Any]) -> Dict[str, str]:
    raw = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    source_titles = [source["meta"]["title"] for source in sources]
    title = source_titles[0] if len(source_titles) == 1 else f"{source_titles[0]} 等 {len(source_titles)} 个脑图"
    mindmap_summary = source_titles[0] if len(source_titles) == 1 else "、".join(source_titles[:3]) + (f" 等 {len(source_titles)} 个" if len(source_titles) > 3 else "")
    return {
        "report_title": _text(raw.get("report_title") or raw.get("title"), f"{title}-测试报告"),
        "title": title,
        "mindmap_summary": mindmap_summary,
        "test_start": _text(raw.get("test_start") or raw.get("testStart")),
        "test_end": _text(raw.get("test_end") or raw.get("testEnd")),
        "tester": _text(raw.get("tester")),
        "client_side": _text(raw.get("client_side") or raw.get("clientSide")),
        "version": _text(raw.get("version")),
        "environment": _text(raw.get("environment")),
        "requirement_link": _text(raw.get("requirement_link") or raw.get("requirementLink")),
        "case_link": _text(raw.get("case_link") or raw.get("caseLink")),
        "test_goal": _text(raw.get("test_goal") or raw.get("testGoal")),
        "remark": _text(raw.get("remark") or raw.get("notes")),
    }


def _selected_cases(cases: List[Dict[str, Any]], payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    selected_ids = [str(item).strip() for item in (payload.get("selected_case_ids") or payload.get("selectedCaseIds") or []) if str(item).strip()]
    if selected_ids:
        selected_set = set(selected_ids)
        selected = [
            case for case in cases
            if case.get("selection_id") in selected_set or case.get("case_id") in selected_set
        ]
    else:
        selected = [case for case in cases if case.get("default_selected")]
    if not selected:
        raise TestReportError("请至少选择一条用例")
    return selected


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"success", "succeeded", "completed", "passed", "pass", "ok"}:
        return "passed"
    if text in {"failed", "failure", "error", "timeout"}:
        return "failed"
    if text in {"blocked", "cancelled", "canceled", "skipped"}:
        return "blocked"
    return "not_executed"


def _execution_map(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = payload.get("execution_results") or payload.get("executionResults") or {}
    if isinstance(raw, list):
        return {
            str(item.get("selection_id") or item.get("selectionId") or item.get("case_id") or item.get("caseId") or "").strip(): item
            for item in raw
            if isinstance(item, dict) and str(item.get("selection_id") or item.get("selectionId") or item.get("case_id") or item.get("caseId") or "").strip()
        }
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
    return {}


def _indexed_execution_map(cases: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    report_index = read_json_file(os.path.join(LEARNING_DIR, "report-index.json"), default={})
    reports = report_index.get("reports") if isinstance(report_index, dict) else []
    if not isinstance(reports, list):
        return {}
    rows = sorted(
        [item for item in reports if isinstance(item, dict)],
        key=lambda item: str(item.get("createdAt") or item.get("created_at") or ""),
        reverse=True,
    )
    matched: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        case_id = _text(case.get("case_id"))
        selection_id = _text(case.get("selection_id") or case_id)
        yaml_file = _text(case.get("yaml_file"))
        module = _text(case.get("source_module"))
        if not case_id or not yaml_file:
            continue
        for report in rows:
            report_module = _text(report.get("module"))
            report_file = _text(report.get("file"))
            if report_module and module and report_module != module:
                continue
            if report_file != yaml_file:
                continue
            matched[selection_id] = {
                "status": report.get("status"),
                "report_url": report.get("reportUrl") or report.get("report_url") or report.get("sonic_report_url"),
                "failure_reason": report.get("summary") or report.get("failure_reason") or report.get("error"),
                "source": "midscene_report_index",
                "job_id": report.get("jobId") or report.get("job_id"),
                "report_id": report.get("reportId") or report.get("report_id"),
            }
            break
    return matched


def _case_with_status(case: Dict[str, Any], execution: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    result = dict(case)
    evidence = execution.get(str(case.get("selection_id") or "")) or execution.get(str(case.get("case_id") or "")) or {}
    result["status"] = "passed"
    result["report_url"] = _text(evidence.get("report_url") or evidence.get("reportUrl") or evidence.get("sonic_report_url"))
    result["failure_reason"] = _text(evidence.get("failure_reason") or evidence.get("failureReason") or evidence.get("error"))
    return result


def _defect_statistics(payload: Dict[str, Any]) -> Dict[str, int]:
    raw = payload.get("defects") if isinstance(payload.get("defects"), dict) else {}

    def count(*keys: str) -> int:
        for key in keys:
            if key in raw:
                try:
                    return max(0, int(raw.get(key) or 0))
                except Exception:
                    return 0
        return 0

    stats = {
        "fatal": count("fatal", "critical", "致命"),
        "serious": count("serious", "major", "严重"),
        "normal": count("normal", "general", "一般"),
        "minor": count("minor", "trivial", "轻微"),
    }
    stats["total"] = sum(stats.values())
    return stats


def _statistics(cases: List[Dict[str, Any]], defects: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    total = len(cases)
    defects = defects or {"total": 0}
    return {
        "total": total,
        "passed": total,
        "failed": 0,
        "blocked": 0,
        "not_executed": 0,
        "pass_rate": 100 if total else 0,
        "defect_total": int(defects.get("total") or 0),
    }


def _quality(statistics: Dict[str, Any], cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "result": "通过",
        "text": "本轮测试已覆盖核心测试范围内的自动化用例，全部用例执行完成且结果通过；关键业务流程、主要功能点及回归风险点验证符合预期，测试结果满足发布准入要求。",
    }


def _release(quality: Dict[str, Any]) -> Dict[str, str]:
    return {
        "suggestion": "建议发布",
        "text": "本轮测试结论为通过，未发现影响发布的阻断问题，版本质量满足发布要求；建议按既定发布流程推进上线，并在发布后持续关注核心业务指标、异常告警及用户反馈。",
    }


def _case_id_label(cases: List[Dict[str, Any]]) -> str:
    if len({case.get("case_set_id") for case in cases if case.get("case_set_id")}) > 1:
        ids = [str(case.get("display_id") or case.get("case_id") or "").strip() for case in cases if str(case.get("case_id") or "").strip()]
        return "、".join(ids[:3]) + (f" 等 {len(ids)} 条" if len(ids) > 3 else "")
    ids = [str(case.get("case_id") or "").strip() for case in cases if str(case.get("case_id") or "").strip()]
    if not ids:
        return ""
    parsed = []
    for item in ids:
        match = re.match(r"^([A-Za-z]+)-(\d+)$", item)
        if not match:
            return "、".join(ids[:3]) + (f" 等 {len(ids)} 条" if len(ids) > 3 else "")
        parsed.append((match.group(1), int(match.group(2)), len(match.group(2))))
    prefixes = {item[0] for item in parsed}
    if len(prefixes) == 1 and len(parsed) > 1:
        nums = [item[1] for item in parsed]
        width = parsed[0][2]
        if max(nums) - min(nums) + 1 == len(set(nums)):
            prefix = parsed[0][0]
            return f"{prefix}-{min(nums):0{width}d} ~ {prefix}-{max(nums):0{width}d}"
    return "、".join(ids[:3]) + (f" 等 {len(ids)} 条" if len(ids) > 3 else "")


def _scenario_goal(scenario: str, cases: List[Dict[str, Any]]) -> str:
    for case in cases:
        for value in (case.get("expected_result"), case.get("assertions", [""])[0] if case.get("assertions") else "", case.get("title")):
            text = _text(value)
            if text:
                return text.rstrip("。") + "。"
    return f"验证{scenario}符合需求。"


def _scope_markdown(cases: List[Dict[str, Any]], *, max_features: int = 8, max_scenarios_per_feature: int = 3) -> str:
    source_ids = []
    for case in cases:
        case_set_id = case.get("case_set_id")
        if case_set_id and case_set_id not in source_ids:
            source_ids.append(case_set_id)
    if len(source_ids) > 1:
        lines: List[str] = []
        scenario_total = 0
        for source_index, case_set_id in enumerate(source_ids, start=1):
            source_cases = [case for case in cases if case.get("case_set_id") == case_set_id]
            if not source_cases or scenario_total >= max_features:
                break
            source_title = source_cases[0].get("source_title") or case_set_id
            lines.append(f"{source_index}. {source_title}")
            source_rows = 0
            for group in _group_cases(source_cases):
                if scenario_total >= max_features or source_rows >= max_scenarios_per_feature:
                    break
                for scenario in group.get("scenarios") or []:
                    if scenario_total >= max_features or source_rows >= max_scenarios_per_feature:
                        break
                    scenario_cases = scenario.get("cases") or []
                    goal = _scenario_goal(scenario.get("scenario") or "", scenario_cases)
                    lines.append(f"   - {group.get('feature') or '未分组功能'} / {scenario.get('scenario') or '未分组场景'}：{goal}")
                    scenario_total += 1
                    source_rows += 1
            if source_index < len(source_ids) and scenario_total < max_features:
                lines.append("")
        return "\n".join(lines).strip()

    lines: List[str] = []
    group_rows = _group_cases(cases)
    scenario_total = 0
    for feature_index, group in enumerate(group_rows[:max_features], start=1):
        if scenario_total >= max_features:
            break
        lines.append(f"{feature_index}. {group['feature']}")
        scenario_rows = group.get("scenarios") or []
        for scenario in scenario_rows[:max_scenarios_per_feature]:
            if scenario_total >= max_features:
                break
            scenario_cases = scenario.get("cases") or []
            goal = _scenario_goal(scenario.get("scenario") or "", scenario_cases)
            lines.append(f"   - {scenario.get('scenario') or '未分组场景'}：{goal}")
            scenario_total += 1
        if feature_index < len(group_rows[:max_features]) and scenario_total < max_features:
            lines.append("")
    return "\n".join(lines).strip()


def _test_points_markdown(cases: List[Dict[str, Any]], *, limit: int = 8) -> str:
    points: List[str] = []
    seen = set()
    for group in _group_cases(cases):
        feature = _text(group.get("feature"), "未分组功能")
        for scenario in group.get("scenarios") or []:
            scenario_name = _text(scenario.get("scenario"), "未分组场景")
            scenario_cases = scenario.get("cases") or []
            goal = _scenario_goal(scenario_name, scenario_cases)
            text = f"{feature} / {scenario_name}：{goal}"
            key = re.sub(r"\s+", "", text)
            if key in seen:
                continue
            seen.add(key)
            points.append(text)
            if len(points) >= limit:
                break
        if len(points) >= limit:
            break
    if not points:
        return "-"
    return "\n".join(f"{index}. {point}" for index, point in enumerate(points, start=1))


def _markdown_table(headers: List[str], rows: List[List[Any]]) -> str:
    safe_headers = [_text(item) for item in headers]
    lines = [
        "| " + " | ".join(safe_headers) + " |",
        "| " + " | ".join(["---"] * len(safe_headers)) + " |",
    ]
    for row in rows:
        cells = [_text(item).replace("|", "\\|") or "-" for item in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _summary_table(statistics: Dict[str, Any]) -> str:
    return _markdown_table(
        ["总计", "通过", "失败", "阻塞", "未执行", "通过率", "缺陷总数"],
        [[
            statistics.get("total", 0),
            statistics.get("passed", 0),
            statistics.get("failed", 0),
            statistics.get("blocked", 0),
            statistics.get("not_executed", 0),
            f"{statistics.get('pass_rate', 0)}%",
            statistics.get("defect_total", 0),
        ]],
    )


def _defect_table(defects: Dict[str, int]) -> str:
    return _markdown_table(
        ["致命", "严重", "一般", "轻微", "总计"],
        [[
            defects.get("fatal", 0),
            defects.get("serious", 0),
            defects.get("normal", 0),
            defects.get("minor", 0),
            defects.get("total", 0),
        ]],
    )


def _conclusion_summary(data: Dict[str, Any]) -> str:
    statistics = data.get("statistics") or {}
    defects = data.get("defects") or {}
    quality = data.get("quality") or {}
    release = data.get("release") or {}
    return _markdown_table(
        ["结论项", "说明"],
        [
            ["准入结论", f"{quality.get('result') or '通过'}，{release.get('suggestion') or '建议发布'}"],
            [
                "执行情况",
                f"自动化用例共 {statistics.get('total', 0)} 条，已执行 {statistics.get('passed', 0)} 条且全部通过，通过率 {statistics.get('pass_rate', 0)}%。",
            ],
            [
                "缺陷情况",
                f"致命 {defects.get('fatal', 0)} 个，严重 {defects.get('serious', 0)} 个，一般 {defects.get('normal', 0)} 个，轻微 {defects.get('minor', 0)} 个，缺陷总数 {defects.get('total', 0)} 个。",
            ],
            ["发布意见", release.get("text") or "-"],
        ],
    )


def _case_table(cases: List[Dict[str, Any]]) -> str:
    return _markdown_table(
        ["用例编号", "优先级", "类型", "场景", "用例名称", "状态"],
        [
            [
                case.get("display_id") or case.get("case_id"),
                case.get("priority"),
                "人工" if case.get("source_type") == "manual" else "自动化",
                case.get("scenario"),
                case.get("title"),
                REPORT_STATUS_TEXT.get(case.get("status"), "未执行"),
            ]
            for case in cases
        ],
    )


def _failure_table(cases: List[Dict[str, Any]]) -> str:
    failed = [case for case in cases if case.get("status") in {"failed", "blocked"}]
    if not failed:
        return "无失败或阻塞用例。"
    return _markdown_table(
        ["用例编号", "状态", "失败原因", "报告链接"],
        [
            [
                case.get("display_id") or case.get("case_id"),
                REPORT_STATUS_TEXT.get(case.get("status"), "失败"),
                case.get("failure_reason") or "见执行报告",
                case.get("report_url") or "-",
            ]
            for case in failed
        ],
    )


def _manual_case_table(cases: List[Dict[str, Any]]) -> str:
    manual = [case for case in cases if case.get("source_type") == "manual"]
    if not manual:
        return "未选择人工用例。"
    return _markdown_table(
        ["用例编号", "场景", "用例名称", "人工原因/风险"],
        [[case.get("display_id") or case.get("case_id"), case.get("scenario"), case.get("title"), case.get("risk") or "需要人工确认"] for case in manual],
    )


def _mindmap_list_markdown(sources: List[Dict[str, Any]]) -> str:
    rows = []
    for source in sources:
        meta = source.get("meta") or {}
        title = meta.get("title") or source.get("case_set_id") or "-"
        module = meta.get("module") or "-"
        rows.append(f"- {title}（{module}，{meta.get('case_count', 0)} 条）")
    return "\n".join(rows) or "-"


def _basic_info(meta: Dict[str, str]) -> str:
    period = "至".join([item for item in [meta.get("test_start"), meta.get("test_end")] if item]) or "-"
    return "\n".join([
        f"测试周期： {period}",
        f"测试人员： {meta.get('tester') or '-'}",
        f"涉及端侧： {meta.get('client_side') or '-'}",
        f"测试版本： {meta.get('version') or '-'}",
        f"测试环境： {meta.get('environment') or '-'}",
    ])


def _overview(meta: Dict[str, str], scope: str) -> str:
    return "\n".join([
        f"需求链接： {meta.get('requirement_link') or '-'}",
        f"测试用例链接： {meta.get('case_link') or '-'}",
        f"脑图文件： {meta.get('mindmap_summary') or '-'}",
        f"测试目标： {meta.get('test_goal') or '验证需求核心流程是否符合预期，并确保核心业务流程不受影响。'}",
        "测试范围：",
        "",
        scope or "-",
    ])


def _default_markdown(data: Dict[str, Any]) -> str:
    meta = data["meta"]
    return "\n\n".join([
        f"# {meta['report_title']}",
        f"## 1. 报告结论\n\n{data['conclusion_summary']}",
        f"## 2. 基本信息\n\n{_basic_info(meta)}",
        f"## 3. 测试概要\n\n{_overview(meta, data['scope_markdown'])}",
        f"## 4. 主要测试点\n\n{data['test_points_markdown']}",
        f"## 5. 测试数据\n\n用例统计：\n\n{data['summary_table']}\n\n缺陷统计：\n\n{data['defect_table']}",
        f"## 6. 质量评估\n\n测试结果： {data['quality']['result']}\n\n{data['quality']['text']}",
        f"## 7. 发布建议\n\n{data['release']['suggestion']}：{data['release']['text']}",
    ]) + "\n"


def _template_body(template_id: str) -> str:
    if not template_id:
        return ""
    templates = {item.get("template_id"): item for item in list_test_report_templates()}
    meta = templates.get(template_id)
    if not meta:
        raise TestReportError("模板不存在，请切回默认模板")
    path = meta.get("path") or ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as exc:
        raise TestReportError(f"模板读取失败：{exc}") from exc


def _render_template(template: str, data: Dict[str, Any]) -> str:
    values = {
        **data.get("meta", {}),
        "test_scope": data.get("scope_markdown") or "",
        "test_points": data.get("test_points_markdown") or "",
        "mindmap_list": data.get("mindmap_list") or "",
        "source_count": data.get("source_count") or "",
        "conclusion_summary": data.get("conclusion_summary") or "",
        "summary_table": data.get("summary_table") or "",
        "defect_table": data.get("defect_table") or "",
        "case_table": data.get("case_table") or "",
        "failure_table": data.get("failure_table") or "",
        "manual_case_table": data.get("manual_case_table") or "",
        "quality_assessment": f"{data['quality']['result']}：{data['quality']['text']}",
        "release_suggestion": f"{data['release']['suggestion']}：{data['release']['text']}",
        "generated_at": data.get("generated_at") or "",
    }
    rendered = template
    for key in TEMPLATE_PLACEHOLDERS:
        rendered = rendered.replace("{{" + key + "}}", str(values.get(key) or ""))
    fallback_sections = []
    required_markers = {
        "## 1. 报告结论": "## 1. 报告结论\n\n" + data["conclusion_summary"],
        "## 2. 基本信息": "## 2. 基本信息\n\n" + _basic_info(data["meta"]),
        "## 3. 测试概要": "## 3. 测试概要\n\n" + _overview(data["meta"], data["scope_markdown"]),
        "## 4. 主要测试点": "## 4. 主要测试点\n\n" + data["test_points_markdown"],
        "## 5. 测试数据": "## 5. 测试数据\n\n用例统计：\n\n" + data["summary_table"] + "\n\n缺陷统计：\n\n" + data["defect_table"],
        "## 6. 质量评估": "## 6. 质量评估\n\n" + values["quality_assessment"],
        "## 7. 发布建议": "## 7. 发布建议\n\n" + values["release_suggestion"],
    }
    for marker, content in required_markers.items():
        if marker not in rendered:
            fallback_sections.append(content)
    if fallback_sections:
        rendered = rendered.rstrip() + "\n\n" + "\n\n".join(fallback_sections) + "\n"
    return rendered


def _markdown_to_html(markdown: str, title: str) -> str:
    lines = []
    in_table = False
    in_points = False
    section_open = False

    def close_table() -> None:
        nonlocal in_table
        if in_table:
            lines.append("</table>")
            in_table = False

    def close_points() -> None:
        nonlocal in_points
        if in_points:
            lines.append("</ol>")
            in_points = False

    def close_section() -> None:
        nonlocal section_open
        close_table()
        close_points()
        if section_open:
            lines.append("</section>")
            section_open = False

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("| ") and line.endswith(" |"):
            close_points()
            cells = [html.escape(cell.strip()) for cell in line.strip("|").split("|")]
            if set(cell.replace("-", "").strip() for cell in cells) == {""}:
                continue
            tag = "th" if not in_table else "td"
            if not in_table:
                lines.append("<table>")
                in_table = True
            lines.append("<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>")
            continue
        close_table()
        escaped = html.escape(line)
        if line.startswith("# "):
            close_section()
            lines.append(f"<header class=\"report-cover\"><div><span>测试报告</span><h1>{html.escape(line[2:].strip())}</h1></div></header>")
        elif line.startswith("## "):
            close_section()
            title_text = line[3:].strip()
            section_class = "report-section report-summary" if "报告结论" in title_text else "report-section"
            lines.append(f"<section class=\"{section_class}\">")
            section_open = True
            lines.append(f"<h2>{html.escape(title_text)}</h2>")
        elif line.startswith("### "):
            close_points()
            lines.append(f"<h3>{html.escape(line[4:].strip())}</h3>")
        elif line.startswith("   - "):
            close_points()
            lines.append(f"<p class=\"scope-item\">{html.escape(line.strip())}</p>")
        elif re.match(r"^\d+\.\s+", line):
            if not in_points:
                lines.append("<ol class=\"test-points\">")
                in_points = True
            point = re.sub(r"^\d+\.\s+", "", line)
            lines.append(f"<li>{html.escape(point)}</li>")
        elif line:
            close_points()
            lines.append(f"<p>{escaped}</p>")
        else:
            close_points()
            lines.append("")
    close_section()
    body = "\n".join(lines)
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{safe_title}</title>
  <style>
    @page {{ margin: 20mm 18mm; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f4f6f9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color: #1f2937; line-height: 1.68; }}
    .report-document {{ max-width: 980px; margin: 34px auto; padding: 0 24px 34px; }}
    .report-cover {{ border-radius: 18px; background: linear-gradient(135deg, #12233f, #0f766e); color: #fff; padding: 34px 38px; box-shadow: 0 18px 42px rgba(15, 35, 64, .18); }}
    .report-cover span {{ display: inline-block; margin-bottom: 22px; padding: 4px 10px; border: 1px solid rgba(255,255,255,.36); border-radius: 999px; font-size: 12px; letter-spacing: .08em; }}
    .report-cover h1 {{ margin: 0; font-size: 34px; line-height: 1.25; font-weight: 800; }}
    .report-section {{ margin-top: 18px; border: 1px solid #dfe5ee; border-radius: 14px; background: #fff; padding: 22px 24px; box-shadow: 0 10px 28px rgba(31, 41, 55, .07); }}
    .report-summary {{ border-color: #b7d7f4; background: linear-gradient(180deg, #ffffff 0%, #f7fbff 100%); }}
    .report-summary table {{ margin-bottom: 4px; }}
    .report-summary th:first-child, .report-summary td:first-child {{ width: 128px; font-weight: 700; color: #0f3d5e; }}
    h2 {{ margin: 0 0 16px; color: #0f172a; font-size: 22px; line-height: 1.35; padding-bottom: 10px; border-bottom: 2px solid #e7edf5; }}
    h3 {{ margin: 18px 0 10px; color: #1f2937; font-size: 16px; }}
    p {{ margin: 7px 0; }}
    table {{ width: 100%; border-collapse: collapse; margin: 12px 0 18px; font-size: 14px; border-radius: 10px; overflow: hidden; }}
    th, td {{ border: 1px solid #d9dee8; padding: 9px 11px; text-align: left; vertical-align: top; }}
    th {{ background: #eef4fb; color: #172033; font-weight: 700; }}
    tr:nth-child(even) td {{ background: #fafcff; }}
    .scope-item {{ margin-left: 4px; padding: 7px 10px; border-left: 3px solid #14b8a6; background: #f0fdfa; border-radius: 6px; }}
    .test-points {{ margin: 6px 0 0; padding-left: 0; counter-reset: point; list-style: none; }}
    .test-points li {{ counter-increment: point; position: relative; margin: 10px 0; padding: 12px 14px 12px 48px; border: 1px solid #dbeafe; border-radius: 10px; background: #f8fbff; }}
    .test-points li::before {{ content: counter(point); position: absolute; left: 14px; top: 12px; width: 24px; height: 24px; border-radius: 50%; background: #0f766e; color: #fff; text-align: center; line-height: 24px; font-weight: 700; }}
    @media print {{ body {{ background: #fff; }} .report-document {{ max-width: none; margin: 0; padding: 0; }} .report-cover, .report-section {{ box-shadow: none; }} }}
  </style>
</head>
<body>
<main class="report-document">
{body}
</main>
</body>
</html>
"""


def _build_report_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    case_set_ids = _payload_case_set_ids(payload)
    sources = _load_sources(case_set_ids)
    all_cases = [case for source in sources for case in source.get("cases") or []]
    selected = _selected_cases(all_cases, payload)
    report_cases = [case for case in all_cases if case.get("source_type") == "automation"]
    execution = {**_indexed_execution_map(report_cases), **_execution_map(payload)}
    cases = [_case_with_status(case, execution) for case in selected]
    report_cases_with_status = [_case_with_status(case, execution) for case in report_cases]
    defects = _defect_statistics(payload)
    statistics = _statistics(report_cases_with_status, defects)
    quality = _quality(statistics, report_cases_with_status)
    release = _release(quality)
    meta = _meta(sources, payload)
    scope = _scope_markdown(cases)
    source_metas = [source["meta"] for source in sources]
    data = {
        "case_set_id": case_set_ids[0],
        "case_set_ids": case_set_ids,
        "source_count": len(sources),
        "sources": source_metas,
        "title": meta["title"],
        "module": source_metas[0]["module"] if len(source_metas) == 1 else "多脑图合并",
        "meta": meta,
        "cases": cases,
        "report_cases": report_cases_with_status,
        "groups": _group_cases(cases),
        "scope_markdown": scope,
        "test_points_markdown": _test_points_markdown(cases),
        "mindmap_list": _mindmap_list_markdown(sources),
        "statistics": statistics,
        "defects": defects,
        "quality": quality,
        "release": release,
        "generated_at": _now_text(),
    }
    data["summary_table"] = _summary_table(statistics)
    data["defect_table"] = _defect_table(defects)
    data["conclusion_summary"] = _conclusion_summary(data)
    data["case_table"] = _case_table(cases)
    data["failure_table"] = _failure_table(cases)
    data["manual_case_table"] = _manual_case_table(cases)
    template = _template_body(_text(payload.get("template_id") or payload.get("templateId")))
    data["markdown"] = _render_template(template, data) if template else _default_markdown(data)
    data["html"] = _markdown_to_html(data["markdown"], meta["report_title"])
    return data


def preview_test_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    data = _build_report_data(payload)
    return {"ok": True, **data}


def _load_index() -> Dict[str, Any]:
    data = read_json_file(TEST_REPORT_INDEX_FILE, default={"reports": [], "updatedAt": ""})
    if not isinstance(data, dict):
        data = {"reports": [], "updatedAt": ""}
    if not isinstance(data.get("reports"), list):
        data["reports"] = []
    return data


def _save_index(data: Dict[str, Any]) -> None:
    data["updatedAt"] = _now_text()
    write_json_file(TEST_REPORT_INDEX_FILE, data)


def create_test_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _build_report_data(payload if isinstance(payload, dict) else {})
    report_id = unique_millis_id("tpr")
    report_dir = _report_dir(data["case_set_id"], report_id) if len(data.get("case_set_ids") or []) <= 1 else _merged_report_dir(report_id)
    os.makedirs(report_dir, exist_ok=True)
    md_path = os.path.join(report_dir, "report.md")
    html_path = os.path.join(report_dir, "report.html")
    word_path = os.path.join(report_dir, "report.doc")
    json_path = os.path.join(report_dir, "report.json")
    write_text_file(md_path, data["markdown"])
    write_text_file(html_path, data["html"])
    write_text_file(word_path, data["html"])
    record = {
        "ok": True,
        "report_id": report_id,
        "case_set_id": data["case_set_id"],
        "case_set_ids": data.get("case_set_ids") or [data["case_set_id"]],
        "source_count": data.get("source_count") or 1,
        "sources": data.get("sources") or [],
        "title": data["meta"]["report_title"],
        "module": data.get("module") or "",
        "created_at": data["generated_at"],
        "statistics": data["statistics"],
        "quality": data["quality"],
        "release": data["release"],
        "files": {"markdown": md_path, "html": html_path, "word": word_path, "json": json_path},
        "download": {
            "markdown": f"/api/test-reports/download?report_id={report_id}&format=md",
            "html": f"/api/test-reports/download?report_id={report_id}&format=html",
            "word": f"/api/test-reports/download?report_id={report_id}&format=doc",
        },
    }
    write_json_file(json_path, {**data, **record})
    index = _load_index()
    reports = [item for item in index.get("reports", []) if item.get("report_id") != report_id]
    reports.insert(0, record)
    index["reports"] = reports[:1000]
    _save_index(index)
    return record


def list_test_reports(case_set_id: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    reports = list(_load_index().get("reports") or [])
    if case_set_id:
        reports = [
            item for item in reports
            if item.get("case_set_id") == case_set_id or case_set_id in (item.get("case_set_ids") or [])
        ]
    reports.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return reports[: max(1, min(500, int(limit or 100)))]


def read_test_report(report_id: str) -> Optional[Dict[str, Any]]:
    report_id = str(report_id or "").strip()
    if not report_id:
        return None
    for item in _load_index().get("reports") or []:
        if item.get("report_id") != report_id:
            continue
        path = ((item.get("files") or {}).get("json") or "")
        data = read_json_file(path, default=None) if path else None
        return data if isinstance(data, dict) else item
    return None


def render_test_report(data: Dict[str, Any], template_id: str = "", output_format: str = "html") -> str:
    if not isinstance(data, dict):
        raise TestReportError("报告数据不能为空")
    markdown = data.get("markdown")
    if template_id:
        markdown = _render_template(_template_body(template_id), data)
    if output_format == "md":
        return markdown or _default_markdown(data)
    return data.get("html") or _markdown_to_html(markdown or _default_markdown(data), (data.get("meta") or {}).get("report_title") or "测试报告")


def list_test_report_templates() -> List[Dict[str, Any]]:
    index = read_json_file(_template_index_file(), default={"templates": []})
    if not isinstance(index, dict) or not isinstance(index.get("templates"), list):
        return []
    templates = []
    for item in index.get("templates") or []:
        if not isinstance(item, dict):
            continue
        path = item.get("path") or ""
        row = dict(item)
        row["exists"] = bool(path and os.path.exists(path))
        templates.append(row)
    return templates


def save_test_report_template(template: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(template, dict):
        raise TestReportError("模板不能为空")
    content = str(template.get("content") or "")
    if not content.strip():
        raise TestReportError("模板内容不能为空")
    filename = clean_asset_filename(template.get("filename") or template.get("name") or "test-report-template.md", default="test-report-template.md")
    ext = ".html" if filename.lower().endswith(".html") else ".md"
    template_id = unique_millis_id("tpl")
    template_dir = _template_dir()
    os.makedirs(template_dir, exist_ok=True)
    path = os.path.join(template_dir, f"{template_id}{ext}")
    write_text_file(path, content)
    record = {
        "template_id": template_id,
        "name": _text(template.get("name"), filename),
        "filename": filename,
        "format": "html" if ext == ".html" else "md",
        "path": path,
        "created_at": _now_text(),
    }
    index = read_json_file(_template_index_file(), default={"templates": []})
    if not isinstance(index, dict):
        index = {"templates": []}
    templates = [item for item in index.get("templates", []) if isinstance(item, dict) and item.get("template_id") != template_id]
    templates.insert(0, record)
    index["templates"] = templates[:100]
    index["updatedAt"] = _now_text()
    write_json_file(_template_index_file(), index)
    return record
