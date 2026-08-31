"""Case统一关联服务 - 将YAML/Sonic/Job/Report/AgentRun关联到统一Case。

从 midscene-upload.py 迁移用例资产管理、用例生成、覆盖度改善等功能。
依赖 task_server.config、task_server.storage、yaml_service，以及通过
lazy import 引入 AI skill / sonic / platform 相关函数。
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

from ..config import (
    AI_COVERAGE_MODEL_WHEN_LOCAL_OK,
    BASELINE_REFS_FILE,
    DEFAULT_APP_PACKAGE,
    ENABLE_AUTOMATIC_BASELINE_REPAIR,
    TASK_DIR,
    safe_bool,
    safe_int,
)
from ..storage import (
    clean_filename,
    clean_id,
    read_json_file,
    read_text_file,
    safe_join,
    write_json_file,
    write_text_file,
)
from .yaml_service import (
    audit_case_coverage,
    case_value,
    clean_filename as _clean_filename,
    extract_baseline_meta_from_block,
    find_yaml_task_block,
    first_non_empty,
    list_yaml_task_blocks,
    normalize_cases_payload,
    normalize_text_list,
    resolve_app_package,
    save_file_version,
    stable_case_id,
    yaml_with_single_task,
)
from .job_service import app_package_for_module
from .business_line_service import business_line_id, configured_test_application

__all__ = [
    "list_task_case_assets",
    "find_task_case_asset",
    "update_task_case_business",
    "task_case_info",
    "task_case_yaml",
    "ensure_yaml_case_ids",
    "build_cases_payload_from_skills",
    "improve_case_coverage",
    "generation_volume_targets",
    "list_file_versions",
    "read_file_version",
    "build_suite",
]

# ---------------------------------------------------------------------------
# 已有 Case 索引管理
# ---------------------------------------------------------------------------

CASE_INDEX_PATH = os.getenv(
    "CASE_INDEX_PATH",
    os.path.join(TASK_DIR, "case-index.json"),
)


def build_suite(cases, suite_id="auto", suite_type="regression"):
    """Build a lightweight suite wrapper without changing existing case logic."""
    return {
        "suite_id": suite_id or "auto",
        "suite_type": suite_type or "regression",
        "cases": cases if isinstance(cases, list) else [],
        "case_count": len(cases) if isinstance(cases, list) else 0,
    }


def _load_index():
    """加载Case索引文件"""
    if os.path.exists(CASE_INDEX_PATH):
        with open(CASE_INDEX_PATH, 'r') as f:
            return json.load(f)
    return {'cases': [], 'updatedAt': None}


def _save_index(index):
    """保存Case索引文件"""
    index['updatedAt'] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(CASE_INDEX_PATH), exist_ok=True)
    with open(CASE_INDEX_PATH, 'w') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def get_or_create_case(yaml_path, app_name='智小白3D APP', module='', task_name=''):
    """根据yamlPath查找或创建Case"""
    index = _load_index()
    for case in index['cases']:
        if case.get('yamlPath') == yaml_path:
            return case
    case = {
        'caseId': _unique_id(),
        'appName': app_name,
        'module': module,
        'file': os.path.basename(yaml_path) if yaml_path else '',
        'taskName': task_name or (os.path.splitext(os.path.basename(yaml_path))[0] if yaml_path else ''),
        'requirement': '',
        'yamlPath': yaml_path,
        'sonicCaseId': None,
        'sonicSuiteId': None,
        'latestJobId': None,
        'latestReportId': None,
        'latestAgentRunId': None,
        'latestRepairDraftId': None,
        'tags': [],
        'riskLevel': 'LOW',
        'updatedAt': time.strftime("%Y-%m-%d %H:%M:%S")
    }
    index['cases'].append(case)
    _save_index(index)
    return case


def _unique_id():
    """生成唯一ID"""
    import uuid
    return str(uuid.uuid4())[:12]


def update_case_link(case_id, **kwargs):
    """更新Case关联: sonicCaseId, latestJobId, latestReportId等"""
    index = _load_index()
    for case in index['cases']:
        if case.get('caseId') == case_id:
            for k, v in kwargs.items():
                if k in case:
                    case[k] = v
            case['updatedAt'] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_index(index)
            return case
    return None


def list_cases_by_module(module):
    """按模块列出Case"""
    index = _load_index()
    if not module:
        return index['cases']
    return [c for c in index['cases'] if c.get('module') == module]


def get_case_by_yaml(yaml_path):
    """根据YAML路径查找Case"""
    index = _load_index()
    for case in index['cases']:
        if case.get('yamlPath') == yaml_path:
            return case
    return None


def get_case(case_id):
    """根据caseId查找"""
    index = _load_index()
    for case in index['cases']:
        if case.get('caseId') == case_id:
            return case
    return None


def list_all_cases():
    """列出所有Case"""
    index = _load_index()
    return index['cases']


# ---------------------------------------------------------------------------
# Lazy import helpers — 避免循环导入
# ---------------------------------------------------------------------------

def _get_task_app_map():
    """Lazy import task_app_map from sonic_service."""
    from .sonic_service import _task_app_map_by_package
    return _task_app_map_by_package()


def _get_load_sonic_sync():
    """Lazy import load_sonic_sync_state from sonic_service."""
    from .sonic_service import load_sonic_sync_state
    return load_sonic_sync_state()


def _get_load_task_meta():
    """Lazy import load_task_meta from job_service."""
    from .job_service import load_task_meta
    return load_task_meta()


def _get_update_task_meta(module, file, patch):
    """Lazy import update_task_meta from job_service."""
    from .job_service import update_task_meta
    return update_task_meta(module, file, patch)


def _get_task_key():
    """Import task_key from job_service."""
    from .job_service import task_key
    return task_key


# ---------------------------------------------------------------------------
# 用例资产管理（迁移自 midscene-upload.py）
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _candidate_task_roots():
    """Return task roots used by runtime and packaged baseline assets.

    Sonic bridge only passes a stable case_id.  In production TASK_DIR may be
    /opt/midscene-tasks, while packaged baseline YAMLs live beside the service
    under server-tasks/server-tasks-all.  Scanning all known roots keeps Sonic
    execution independent from one manual sync step.
    """
    roots = [
        TASK_DIR,
        os.path.join(_PROJECT_ROOT, "server-tasks"),
        os.path.join(_PROJECT_ROOT, "server-tasks-all"),
        "/opt/midscene-task-platform/server-tasks",
        "/opt/midscene-task-platform/server-tasks-all",
    ]
    seen = set()
    result = []
    for root in roots:
        root_abs = os.path.abspath(str(root or ""))
        if root_abs and root_abs not in seen and os.path.isdir(root_abs):
            seen.add(root_abs)
            result.append(root_abs)
    return result


def _safe_join_task_root(root, *parts):
    root_abs = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root_abs, *[str(p or "") for p in parts]))
    if path != root_abs and not path.startswith(root_abs + os.sep):
        raise ValueError("非法路径")
    return path

def task_case_info(module, file, yaml_text, task_info, app_package=None):
    """提取单条用例的元信息。"""
    meta = extract_baseline_meta_from_block(task_info.get("block", ""))
    resolved_app = resolve_app_package(module, file, yaml_text, explicit=app_package or "", allow_default=False)
    case_id = meta.get("case_id") or meta.get("caseId") or stable_case_id(resolved_app, module, file, task_info.get("name"))
    try:
        app_map = _get_task_app_map()
        app_info = app_map.get(resolved_app) or {}
        app_name = app_info.get("name") or resolved_app
    except Exception:
        app_name = resolved_app
    status = "draft"
    business = ""
    try:
        task_key_fn = _get_task_key()
        task_meta = _get_load_task_meta()
        file_meta = task_meta.get(task_key_fn(module, file), {}) or {}
        status = file_meta.get("status", "draft")
        case_businesses = file_meta.get("case_businesses") or {}
        if isinstance(case_businesses, dict):
            business = case_businesses.get(case_id)
    except Exception:
        pass
    business = normalize_task_case_business(
        meta.get("business") or meta.get("business_type") or business,
        app_package=resolved_app,
    )
    return {
        "case_id": case_id,
        "module": module,
        "file": clean_filename(file),
        "task_name": task_info.get("name") or "",
        "line": safe_int(task_info.get("start"), 0) + 1,
        "app_package": resolved_app,
        "app_name": app_name,
        "platform": "android" if "android:" in (yaml_text or "") else ("ios" if "ios:" in (yaml_text or "") else "android"),
        "goal": meta.get("goal") or "",
        "path": meta.get("path") or "",
        "expected": meta.get("expected") or "",
        "status": status,
        "business": business,
    }


def list_task_case_assets(module_filter="", file_filter=""):
    """扫描运行目录和随包基线目录，返回所有用例资产列表。"""
    rows = []
    seen_cases = set()
    for root in _candidate_task_roots():
        for module in sorted(os.listdir(root)):
            if module_filter and module != module_filter:
                continue
            module_dir = _safe_join_task_root(root, module)
            if not os.path.isdir(module_dir):
                continue
            for file in sorted(os.listdir(module_dir)):
                if not file.endswith((".yaml", ".yml")):
                    continue
                if file_filter and file != clean_filename(file_filter):
                    continue
                try:
                    yaml_text_value = read_text_file(_safe_join_task_root(root, module, file))
                    app_package = resolve_app_package(module, file, yaml_text_value, allow_default=False)
                    for task_info in list_yaml_task_blocks(yaml_text_value):
                        row = task_case_info(module, file, yaml_text_value, task_info, app_package=app_package)
                        row["_root"] = root
                        dedupe_key = row.get("case_id") or f"{root}|{module}|{file}|{row.get('task_name')}"
                        if dedupe_key in seen_cases:
                            continue
                        seen_cases.add(dedupe_key)
                        rows.append(row)
                except Exception as e:
                    rows.append({
                        "case_id": "",
                        "module": module,
                        "file": file,
                        "task_name": "",
                        "error": str(e),
                        "_root": root
                    })
    try:
        sync = _get_load_sonic_sync()
        sync_cases = sync.get("cases", {})
    except Exception:
        sync_cases = {}
    for row in rows:
        row["sonic"] = sync_cases.get(row.get("case_id"), {})
    return rows


def find_task_case_asset(case_id):
    """按 case_id 查找用例资产。"""
    case_id = (case_id or "").strip()
    if not case_id:
        raise ValueError("case_id 不能为空")
    for row in list_task_case_assets():
        if row.get("case_id") == case_id:
            return row
    raise FileNotFoundError(f"未找到 case_id：{case_id}")


def normalize_task_case_business(value, app_package=""):
    return business_line_id(value, app_package=app_package)


def update_task_case_business(case_id, business):
    """Persist one UI case's business without adding unsupported YAML fields."""
    case = find_task_case_asset(case_id)
    app_package = str(case.get("app_package") or "").strip()
    if not app_package:
        raise ValueError("YAML 缺少有效的应用启动步骤；请在 flow 中配置 launch 为平台已配置的应用包名，保存后重试")
    application = configured_test_application(app_package, include_disabled=True)
    if not application:
        raise ValueError("YAML 的应用包名未在平台配置；请核对启动步骤和应用配置后重试")
    if (
        application.get("enabled") is False
        or application.get("historical_only") is True
    ):
        raise ValueError("当前应用已停用或仅用于历史展示，不能修改所属业务")
    normalized = business_line_id(
        business,
        app_package=app_package,
        require_active=True,
    )
    if not normalized:
        raise ValueError("请选择所属业务")
    module = case.get("module") or ""
    file = case.get("file") or ""
    task_key_fn = _get_task_key()
    file_meta = (_get_load_task_meta().get(task_key_fn(module, file), {}) or {})
    case_businesses = dict(file_meta.get("case_businesses") or {})
    case_businesses[case_id] = normalized
    _get_update_task_meta(module, file, {"case_businesses": case_businesses})
    return {**case, "business": normalized}


def task_case_yaml(case_info):
    """获取单条用例的 YAML。"""
    root = case_info.get("_root") or TASK_DIR
    yaml_path = _safe_join_task_root(root, case_info["module"], case_info["file"])
    if not os.path.exists(yaml_path) and root != TASK_DIR:
        yaml_path = safe_join(TASK_DIR, case_info["module"], case_info["file"])
    yaml_text_value = read_text_file(yaml_path)
    app_package = resolve_app_package(case_info["module"], case_info["file"], yaml_text_value, allow_default=False)
    return yaml_with_single_task(yaml_text_value, case_info["task_name"], app_package=app_package)


def ensure_yaml_case_ids(module, file):
    """确保 YAML 文件中每条 task 都有 case_id。"""
    file = clean_filename(file)
    yaml_path = safe_join(TASK_DIR, module, file)
    if not os.path.exists(yaml_path):
        raise FileNotFoundError("YAML 文件不存在")
    yaml_text_value = read_text_file(yaml_path)
    app_package = resolve_app_package(module, file, yaml_text_value, allow_default=False)
    tasks = list_yaml_task_blocks(yaml_text_value)
    if not tasks:
        return yaml_text_value, []
    lines = yaml_text_value.splitlines()
    changes = []
    for task in reversed(tasks):
        meta = extract_baseline_meta_from_block(task.get("block", ""))
        if meta.get("case_id") or meta.get("caseId"):
            continue
        cid = stable_case_id(app_package, module, file, task.get("name"))
        insert_at = safe_int(task.get("start"), 0) + 1
        indent = task.get("indent", "") + "  "
        lines.insert(insert_at, f"{indent}# baseline.case_id: {cid}")
        changes.append({"task_name": task.get("name"), "case_id": cid})
    if changes:
        save_file_version(module, file, reason="before_sonic_case_id")
        yaml_text_value = "\n".join(lines).rstrip() + "\n"
        write_text_file(yaml_path, yaml_text_value)
    return yaml_text_value, list(reversed(changes))


# ---------------------------------------------------------------------------
# 版本管理（迁移自 midscene-upload.py）
# ---------------------------------------------------------------------------

def list_file_versions(module, file, limit=30):
    """列出文件版本列表。"""
    from .yaml_service import version_dir_for
    try:
        vdir = version_dir_for(module, clean_filename(file))
    except ValueError:
        return []
    if not os.path.exists(vdir):
        return []
    result = []
    for name in os.listdir(vdir):
        if not name.endswith(".json"):
            continue
        meta = read_json_file(safe_join(vdir, name), default=None)
        if meta:
            result.append(meta)
    result.sort(key=lambda item: item.get("id", ""), reverse=True)
    return result[:limit]


def read_file_version(module, file, version_id):
    """读取指定版本内容。"""
    from .yaml_service import version_dir_for
    version_id = clean_id(version_id, "version")
    vdir = version_dir_for(module, clean_filename(file))
    meta = read_json_file(safe_join(vdir, f"{version_id}.json"), default=None)
    if not meta:
        raise FileNotFoundError("版本不存在")
    yaml_path = safe_join(vdir, meta.get("yaml") or f"{version_id}.yaml")
    with open(yaml_path, encoding="utf-8") as f:
        content = f.read()
    return meta, content


# ---------------------------------------------------------------------------
# 用例生成 — generation_volume_targets（迁移自 midscene-upload.py）
# ---------------------------------------------------------------------------

def generation_volume_targets(analysis, mode="full"):
    """Derive a soft case-plan size from independently verifiable requirements."""
    mode = str(mode or "full").strip().lower()
    analysis = analysis if isinstance(analysis, dict) else {}
    points = normalize_text_list(analysis.get("requirement_points"))
    acceptance_checks = [
        item for item in (analysis.get("requirement_acceptance_checks") or analysis.get("requirementAcceptanceChecks") or [])
        if isinstance(item, dict) or str(item or "").strip()
    ]
    contract = analysis.get("requirement_contract") if isinstance(analysis.get("requirement_contract"), dict) else {}
    def unit_key(value):
        return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())

    point_keys = {unit_key(item) for item in points if unit_key(item)}
    branch_values = []
    acceptance_units = set()
    mergeable_positive_kinds = {
        "visibility", "visible", "display", "relation", "layout", "position",
        "order", "copy", "text", "icon", "reachability", "navigation", "click",
        "可见", "展示", "关系", "布局", "位置", "顺序", "文案", "图标", "可达", "跳转", "点击",
    }
    for item in acceptance_checks:
        if isinstance(item, dict):
            branch = first_non_empty(
                item.get("branch"),
                item.get("businessBranch"),
                item.get("business_branch"),
                item.get("requirementId"),
                item.get("requirement_id"),
                item.get("feature"),
            )
            if branch:
                branch_values.append(branch)
            condition = first_non_empty(
                item.get("condition"),
                item.get("precondition"),
                item.get("state"),
                item.get("dataState"),
                item.get("data_state"),
                item.get("role"),
            )
            kind = first_non_empty(item.get("kind"), item.get("type"), item.get("category"))
            kind_key = unit_key(kind)
            dimension = "core_positive" if not condition and kind_key in mergeable_positive_kinds else first_non_empty(
                condition,
                kind,
                item.get("text"),
                item.get("title"),
            )
            acceptance_units.add((unit_key(branch) or "global", unit_key(dimension) or "core_positive"))
        else:
            key = unit_key(item)
            if key:
                acceptance_units.add(("global", key))
    for item in (contract.get("businessFlows") or contract.get("business_flows") or []):
        if isinstance(item, dict):
            branch = str(item.get("branch") or item.get("name") or "").strip()
            if branch:
                branch_values.append(branch)
    branch_count = len({unit_key(item) for item in branch_values if unit_key(item)})
    acceptance_count = len(acceptance_checks)
    risks = normalize_text_list((analysis or {}).get("risks"))
    blockers = normalize_text_list((analysis or {}).get("blockers"))
    missing = normalize_text_list((analysis or {}).get("missing_inputs"))
    point_count = len(point_keys)
    acceptance_unit_count = len(acceptance_units)
    requirement_unit_count = max(1, point_count, branch_count, acceptance_unit_count)
    target_plan_cases = requirement_unit_count
    plan_review_slack = max(2, (target_plan_cases + 1) // 2)
    max_plan_cases = target_plan_cases + plan_review_slack
    min_plan_cases = target_plan_cases

    if target_plan_cases <= 3:
        size = "small"
        automation_ceiling = 3
    elif target_plan_cases <= 8:
        size = "medium"
        automation_ceiling = 5
    else:
        size = "large"
        automation_ceiling = 8
    target_cases = max(1, min(target_plan_cases, automation_ceiling))
    min_cases = max(1, min(target_cases, min_plan_cases))
    max_cases = automation_ceiling
    min_scenarios = min_plan_cases
    target_scenarios = target_plan_cases
    max_scenarios = max_plan_cases
    smoke_cases = min(3, target_cases)
    return {
        "mode": mode,
        "size": size,
        "requirement_point_count": point_count,
        "acceptance_check_count": acceptance_count,
        "acceptance_unit_count": acceptance_unit_count,
        "business_branch_count": branch_count,
        "requirement_unit_count": requirement_unit_count,
        "effective_requirement_point_count": requirement_unit_count,
        "min_plan_cases": min_plan_cases,
        "target_plan_cases": target_plan_cases,
        "max_plan_cases": max_plan_cases,
        "min_automation_cases": min_cases,
        "target_automation_cases": target_cases,
        "max_automation_cases": max_cases,
        "max_cases": max_cases,
        "smoke_cases": smoke_cases,
        "smoke_max_cases": 3,
        "continue_threshold": 0.5,
        "min_scenarios": min_scenarios,
        "target_scenarios": target_scenarios,
        "max_scenarios": max_scenarios,
        "manual_cases_not_counted": True,
        "guidance": (
            "测试计划数量按需求文档中的独立业务分支、前置状态、业务规则和可观察结果动态计算；"
            "同一路径上的可见性、文案、位置和可达性应合并验证，不要机械拆成多条。"
            "3/5/8 只作为 Runner 自动化候选容量，不是测试设计硬上限，也不能用于补齐不存在的场景。"
            "冒烟候选可多于首批，但 Runner 首批自动下发最多 3 条。"
            "首批冒烟用于证明 YAML 能下发、能执行、能产生日志；真实通过率不低于 50% 时继续扩展，"
            "单条脚本/定位/产品断言失败会记录为测试结果并进入修复，不等同于 YAML 不可执行。"
            "无法稳定自动化的场景进入 manual_cases，但不计入自动化 cases 数。"
        ),
        "missing_inputs": missing,
        "blockers": blockers
    }


# ---------------------------------------------------------------------------
# 用例生成 — build_cases_payload_from_skills（delegated to ai_skill_service）
# ---------------------------------------------------------------------------

def build_cases_payload_from_skills(title, module, text_assets, mode="full", model_config=None, app_package="", app_name=""):
    """通过 AI skills pipeline 生成用例 payload。

    Source: midscene-upload.py 行 15852-15880。
    Delegated to ``ai_skill_service.build_cases_payload_from_skills``。
    """
    from task_server.services.ai_skill_service import build_cases_payload_from_skills as _build
    return _build(
        title,
        module,
        text_assets,
        mode=mode,
        model_config=model_config,
        app_package=app_package,
        app_name=app_name,
    )


# ---------------------------------------------------------------------------
# 用例覆盖度改善 — improve_case_coverage（delegated to ai_skill_service）
# ---------------------------------------------------------------------------

def improve_case_coverage(title, module, payload, max_rounds=1):
    """改善用例覆盖度。

    Source: midscene-upload.py 行 16348-16379。
    Delegated to ``ai_skill_service.improve_case_coverage``。
    """
    from task_server.services.ai_skill_service import improve_case_coverage as _improve
    return _improve(title, module, payload, max_rounds=max_rounds)




# ---------------------------------------------------------------------------
# Migrated from midscene-upload.py
# ---------------------------------------------------------------------------

def baseline_ref_key(app_package, module, file, task_name=""):
    return "::".join([
        app_package or app_package_for_module(module) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE),
        module or "",
        clean_filename(file or ""),
        task_name or ""
    ])



def get_baseline_ref_page_ids(app_package, module, file, task_name=""):
    refs = load_baseline_refs()
    app_package = app_package or app_package_for_module(module) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    page_ids = []
    for key in (
        baseline_ref_key(app_package, module, file, ""),
        baseline_ref_key(app_package, module, file, task_name)
    ):
        row = refs.get(key) or {}
        for page_id in row.get("page_ids") or []:
            if page_id and page_id not in page_ids:
                page_ids.append(page_id)
    return page_ids



def load_baseline_refs():
    data = read_json_file(BASELINE_REFS_FILE, default={})
    return data if isinstance(data, dict) else {}



def set_baseline_ref_page_ids(app_package, module, file, task_name, page_ids):
    refs = load_baseline_refs()
    app_package = app_package or app_package_for_module(module) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    key = baseline_ref_key(app_package, module, file, task_name)
    refs[key] = {
        "app_package": app_package or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE),
        "module": module,
        "file": clean_filename(file),
        "task_name": task_name or "",
        "page_ids": [str(item) for item in (page_ids or []) if str(item).strip()],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_baseline_refs(refs)
    return refs[key]



def automatic_baseline_repair_enabled(requested):
    return bool(ENABLE_AUTOMATIC_BASELINE_REPAIR and safe_bool(requested))



def save_baseline_refs(data):
    write_json_file(BASELINE_REFS_FILE, data)
