"""Repair-draft service.

从 midscene-upload.py 抽取的修复草稿业务逻辑，提供：

* 修复草稿的加载、保存、归一化与单条查询
* 上层路由调用的 ``upsert / reject / apply`` 操作
* 修复风险评估（基于高风险关键词）
* 应用前的版本备份

约束：
* 应用修复前必须 ``backup_before_repair`` 备份原始基线 YAML
* 默认 **禁止自动覆盖基线**——除非显式 ``confirm=True`` 或运行时
  ``ENABLE_AUTOMATIC_BASELINE_REPAIR=true``
* 不可修复的故障类型（PRODUCT_BUG/ENV_ISSUE/UNKNOWN）由
  :func:`can_generate_yaml_repair` 直接拒绝
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import yaml as pyyaml
except Exception:
    pyyaml = None

from task_server.config import (
    ENABLE_AUTOMATIC_BASELINE_REPAIR,
    JOB_LOCK,
    LEARNING_DIR,
    REPAIR_DRAFTS_FILE,
    TASK_DIR,
    VERSION_DIR,
    safe_bool,
    safe_int,
)
from task_server.schemas import HIGH_RISK_KEYWORDS, REPAIRABLE_FAILURE_TYPES
from task_server.storage import (
    clean_filename,
    clean_id,
    read_json_cached,
    read_json_file,
    read_text_file,
    safe_join,
    unique_millis_id,
    write_json_file,
    write_text_file,
)
from task_server.services.ai_skill_service import (
    dashscope_chat_content,
    dashscope_api_key,
    repair_knowledge_context,
    repair_strategy_guide,
    task_business_context,
    extract_failure_brief,
    normalize_model_json,
    execution_screenshot_context,
    report_image_context,
    build_failure_context,
    classify_failure_by_context,
    sanitize_failure_review_against_sources,
    detect_wait_strategy_issue,
    detect_horizontal_scroll_script_issue,
    run_ai_skill,
)
from task_server.services.yaml_service import (
    changed_line_count,
    detect_yaml_platform,
    generate_job_id,
    normalize_yaml_from_model,
    normalize_yaml_task_block_from_model,
    normalize_yaml_runtime_guards,
    normalize_task_block_runtime_guards,
    normalize_full_yaml_structure,
    replace_yaml_task_block,
    resolve_app_package,
    save_file_version,
    find_yaml_task_block,
    strip_yaml_quotes,
    update_generate_job,
    validate_midscene_yaml,
    yaml_diff_summary,
    yaml_task_names,
)


def _repair_prompt_center_prefix(
    module: str = "",
    file: str = "",
    task_name: str = "",
    yaml_text: str = "",
    task_block: str = "",
    knowledge_text: str = "",
    failure_brief: Dict[str, Any] | None = None,
    business_context: Dict[str, Any] | List[Dict[str, Any]] | None = None,
) -> str:
    """Render a business-first repair prompt prefix without changing legacy rules."""
    try:
        from task_server.prompts import get_prompt_center
    except Exception:
        return ""
    if isinstance(business_context, list):
        business_path = "；".join(
            str(item.get("business_path") or item.get("task") or "").strip()
            for item in business_context
            if isinstance(item, dict)
        )
    elif isinstance(business_context, dict):
        business_path = str(business_context.get("business_path") or business_context.get("goal") or "").strip()
    else:
        business_path = ""
    requirement_text = "\n\n".join(
        part for part in [
            f"文件：{module}/{file}".strip("/"),
            f"目标用例：{task_name}" if task_name else "",
            f"业务主链：{business_path}" if business_path else "",
            f"失败摘要：{json.dumps(failure_brief or {}, ensure_ascii=False)}",
            f"页面知识：{knowledge_text}" if knowledge_text else "",
            f"当前 task：{task_block}" if task_block else "",
            f"YAML 摘要：{yaml_text[-4000:]}" if yaml_text else "",
        ]
        if part
    )
    try:
        return get_prompt_center().get("repair", {
            "target": task_name or file or module,
            "module": module,
            "file": file,
            "businessPath": business_path,
            "requirementText": requirement_text,
            "failureAnalysis": failure_brief or {},
        })
    except Exception:
        return ""
from task_server.services.job_service import (
    create_pending_job,
    load_jobs,
    new_job_id,
    save_jobs,
    update_task_meta,
)

# ---------------------------------------------------------------------------
# Failure-type gating (合并自原 task_server/repair_service.py)
# ---------------------------------------------------------------------------

NON_REPAIRABLE_FAILURE_TYPES = {"PRODUCT_BUG", "ENV_ISSUE", "UNKNOWN"}

GENERIC_PROMPT_TEXTS = {
    "确认", "确定", "取消", "返回", "下一步", "完成", "提交", "保存", "关闭",
    "继续", "开始", "进入", "查看", "点击", "搜索", "打开", "选择", "页面",
    "结果符合预期", "页面正常", "操作成功", "功能正常", "跳转成功"
}

SHORT_PROMPT_CONTEXT_WORDS = (
    "弹窗", "页面", "按钮", "入口", "底部", "顶部", "右上角", "左上角", "Tab",
    "列表", "区域", "卡片", "搜索框", "输入框", "详情页", "配置页", "结果页",
    "首页", "弹窗中", "对话框", "确认页", "预览页", "打印页"
)

SUPPORTED_FLOW_ITEMS = {
    "ai", "aiAct", "aiAction", "aiTap", "aiHover", "aiInput", "aiKeyboardPress",
    "aiScroll", "aiAssert", "aiWaitFor", "aiQuery", "aiAsk", "aiBoolean", "aiNumber",
    "aiString", "sleep", "launch", "terminate", "javascript", "recordToReport",
    "runAdbShell", "runWdaRequest"
}


def dedupe_keep_order(items):
    seen = set()
    result = []
    for item in items or []:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def normalize_ai_object(value, *, default_key="items"):
    """Normalize AI model output before dict-style access.

    Some model calls may return a list/string/null instead of the expected
    object.  Repair logic must never call ``.get`` on those raw values.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {default_key: value}
    if isinstance(value, str):
        return {"text": value}
    return {}


def read_text(path, default=""):
    return read_text_file(str(path), default=default)


def read_json(path, default=None):
    return read_json_file(str(path), default=default)


def business_alignment_report(old_block, new_block):
    old_names = yaml_task_names(old_block or "")
    new_names = yaml_task_names(new_block or "")
    warnings = []
    if old_names and new_names and old_names != new_names:
        warnings.append("修复前后 task 名称发生变化，请人工确认业务链路是否一致")
    return {"ok": not warnings, "warnings": warnings, "oldTasks": old_names, "newTasks": new_names}


def validate_yaml_business_flow_preserved(old_yaml, new_yaml):
    report = business_alignment_report(old_yaml, new_yaml)
    return report.get("warnings") or []


def validate_repair_safety(old_yaml, new_yaml, ctx=None, task_name=""):
    warnings = []
    if not str(new_yaml or "").strip():
        warnings.append("修复后 YAML 为空")
    if "tasks:" in str(old_yaml or "") and "tasks:" not in str(new_yaml or ""):
        warnings.append("修复后 YAML 缺少 tasks")
    if task_name and task_name not in str(new_yaml or ""):
        warnings.append(f"修复后未保留目标用例名称：{task_name}")
    return warnings


def should_use_rule_only_repair(old_text, guard_changes, stdout="", stderr="", summary=None):
    return bool(guard_changes)


def repair_by_failure_type(yaml_text, ctx):
    return {"content": yaml_text, "changes": [], "analysis": "未命中特定失败类型修复规则"}


def apply_task_repair_patches(task_block, patches):
    return task_block, []


def attach_repair_result_metadata(result, old_yaml, new_yaml, repair_dir="", before_version=None, yaml_check=None, safety_warnings=None, business_check=None):
    row = dict(result or {})
    row["changed_line_count"] = changed_line_count(old_yaml or "", new_yaml or "")
    row["diff_summary"] = yaml_diff_summary(old_yaml or "", new_yaml or "")
    row["repair_dir"] = repair_dir
    row["before_version"] = before_version
    row["yaml_check"] = yaml_check
    row["safety_warnings"] = safety_warnings or []
    row["business_check"] = business_check or {}
    return row


def can_generate_yaml_repair(failure_type: Any) -> bool:
    """判断故障类型是否可生成 YAML 修复。

    仅 ``SCRIPT_ISSUE`` 类故障允许走 YAML 自动/半自动修复路径，
    其他类型必须留在人工审核或 Bug Draft 流程。
    """
    return str(failure_type or "").upper() in REPAIRABLE_FAILURE_TYPES


# ---------------------------------------------------------------------------
# Draft normalization
# ---------------------------------------------------------------------------

REPAIR_DRAFT_STATUSES = {"DRAFTED", "WAIT_CONFIRM", "APPLIED", "REJECTED", "EXPIRED"}

_MAX_DRAFTS_KEPT = 500


def _now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_risk_hits(value: Any) -> List[str]:
    if isinstance(value, str):
        items: Iterable[Any] = [seg.strip() for seg in re.split(r"[,，、\s]+", value) if seg.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []
    return [str(item) for item in items if str(item).strip()]


def normalize_repair_draft(draft: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """统一字段命名 / 默认值，迁移自 midscene-upload.py。"""
    draft = dict(draft or {})
    draft_id = str(draft.get("draftId") or draft.get("draft_id") or "").strip()
    if not draft_id:
        draft_id = unique_millis_id("repair")
    job_id = str(draft.get("jobId") or draft.get("job_id") or "").strip()
    status = str(draft.get("status") or "DRAFTED").upper()
    if status not in REPAIR_DRAFT_STATUSES:
        status = "DRAFTED"
    risk_hits = _normalize_risk_hits(draft.get("riskHits") or draft.get("risk_hits") or [])
    fixed_yaml = draft.get("fixedYaml") or draft.get("fixed_yaml") or draft.get("yaml") or ""
    original_yaml = draft.get("originalYaml") or draft.get("original_yaml") or ""
    file_name = draft.get("file") or ""
    if file_name:
        file_name = clean_filename(file_name)
    now = _now_ts()
    normalized: Dict[str, Any] = {
        **draft,
        "draftId": draft_id,
        "draft_id": draft_id,
        "jobId": job_id,
        "job_id": job_id,
        "module": str(draft.get("module") or "").strip(),
        "file": file_name,
        "taskName": str(draft.get("taskName") or draft.get("task_name") or "").strip(),
        "status": status,
        "failureType": str(
            draft.get("failureType") or draft.get("failure_type") or "SCRIPT_ISSUE"
        ).upper(),
        "riskLevel": str(draft.get("riskLevel") or draft.get("risk_level") or "medium").lower(),
        "riskHits": risk_hits,
        "risk_hits": risk_hits,
        "analysis": draft.get("analysis") or "",
        "originalYaml": original_yaml,
        "original_yaml": original_yaml,
        "fixedYaml": fixed_yaml,
        "fixed_yaml": fixed_yaml,
        "diff": draft.get("diff") or draft.get("diff_summary") or "",
        "validation": draft.get("validation") or {},
        "requireConfirm": True,
        "require_confirm": True,
        "createdAt": draft.get("createdAt") or draft.get("created_at") or now,
        "created_at": draft.get("created_at") or draft.get("createdAt") or now,
        "updatedAt": draft.get("updatedAt") or draft.get("updated_at") or now,
        "updated_at": draft.get("updated_at") or draft.get("updatedAt") or now,
    }
    return normalized


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_repair_drafts() -> List[Dict[str, Any]]:
    """加载修复草稿列表（带 TTL 缓存）。"""
    data = read_json_cached(REPAIR_DRAFTS_FILE, ttl_seconds=2, default={"drafts": []})
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get("drafts") or []
    else:
        raw = []
    return [normalize_repair_draft(item) for item in raw if isinstance(item, dict)]


def save_repair_drafts(drafts: List[Dict[str, Any]]) -> None:
    """保存修复草稿列表（原子写入）。"""
    payload = drafts if isinstance(drafts, list) else []
    write_json_file(REPAIR_DRAFTS_FILE, {"drafts": payload})


def get_repair_draft(draft_id: Any) -> Optional[Dict[str, Any]]:
    """根据 draftId 获取单个草稿。"""
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return None
    for draft in load_repair_drafts():
        if draft.get("draftId") == draft_id or draft.get("draft_id") == draft_id:
            return draft
    return None


def repair_drafts_for_job(job_id: Any) -> List[Dict[str, Any]]:
    """返回指定 jobId 的全部草稿。"""
    job_id = str(job_id or "").strip()
    if not job_id:
        return []
    return [
        draft
        for draft in load_repair_drafts()
        if draft.get("jobId") == job_id or draft.get("job_id") == job_id
    ]


def active_repair_draft_for_job(job_id: Any) -> Optional[Dict[str, Any]]:
    """返回 job 当前可处理的草稿（DRAFTED / WAIT_CONFIRM）。"""
    for draft in repair_drafts_for_job(job_id):
        if draft.get("status") in ("DRAFTED", "WAIT_CONFIRM"):
            return draft
    return None


def upsert_repair_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    """创建或更新修复草稿，按 ``draftId`` 替换。"""
    normalized = normalize_repair_draft(draft)
    normalized["updatedAt"] = normalized["updated_at"] = _now_ts()
    drafts = load_repair_drafts()
    replaced = False
    for idx, item in enumerate(drafts):
        if item.get("draftId") == normalized.get("draftId"):
            drafts[idx] = normalized
            replaced = True
            break
    if not replaced:
        drafts.insert(0, normalized)
    save_repair_drafts(drafts[:_MAX_DRAFTS_KEPT])
    return normalized


def reject_repair_draft(draft_id: Any, reason: str = "") -> Dict[str, Any]:
    """拒绝草稿，写回 ``REJECTED`` 状态并记录原因。"""
    draft = get_repair_draft(draft_id)
    if not draft:
        raise ValueError("修复草稿不存在")
    if draft.get("status") in ("APPLIED", "REJECTED", "EXPIRED"):
        raise ValueError(f"草稿当前状态不可拒绝：{draft.get('status')}")
    draft["status"] = "REJECTED"
    reason_text = str(reason or "").strip()
    draft["rejectReason"] = reason_text
    draft["reject_reason"] = reason_text
    draft["rejectedAt"] = draft["rejected_at"] = _now_ts()
    return upsert_repair_draft(draft)


# ---------------------------------------------------------------------------
# Risk assessment
# ---------------------------------------------------------------------------


def assess_repair_risk(old_yaml: str, new_yaml: str) -> Dict[str, Any]:
    """评估修复风险——重点关注修复后**新增**的高风险关键词。

    返回：
        {
            "riskLevel": "high" | "medium" | "low",
            "riskHits": [关键词],
            "newHits":  [新引入的关键词],
            "removedHits": [移除的关键词],
        }
    """
    old_text = str(old_yaml or "")
    new_text = str(new_yaml or "")
    old_hits = {kw for kw in HIGH_RISK_KEYWORDS if kw in old_text}
    new_hits = {kw for kw in HIGH_RISK_KEYWORDS if kw in new_text}
    introduced = sorted(new_hits - old_hits)
    removed = sorted(old_hits - new_hits)
    if introduced:
        level = "high"
    elif new_hits:
        level = "medium"
    else:
        level = "low"
    return {
        "riskLevel": level,
        "riskHits": sorted(new_hits),
        "newHits": introduced,
        "removedHits": removed,
    }


# ---------------------------------------------------------------------------
# Backup before repair
# ---------------------------------------------------------------------------


def _version_dir_for(module: str, file: str) -> str:
    """``$LEARNING_DIR/versions/<module>/<file>/`` 形式的版本目录。"""
    safe_module = clean_id(module or "default", "module")
    safe_file = clean_filename(file or "task.yaml")
    return safe_join(VERSION_DIR, safe_module, safe_file)


def backup_before_repair(
    module: str,
    file: str,
    *,
    content: Optional[str] = None,
    reason: str = "before_repair_draft_apply",
) -> Optional[Dict[str, Any]]:
    """修复前备份原始 YAML 文件。

    与 midscene-upload.py 中 ``save_file_version`` 行为保持一致：

    * ``content=None`` 时从基线读取当前文件内容
    * 备份至 ``$LEARNING_DIR/versions/<module>/<file>/<ts>_<reason>.yaml``
    * 同步写入元数据 ``.json``
    * 出错时返回 ``None``，不抛异常以避免阻塞修复主流程
    """
    try:
        cleaned_file = clean_filename(file)
        fpath = safe_join(TASK_DIR, module, cleaned_file)
        if content is None:
            if not os.path.exists(fpath):
                return None
            content = read_text_file(fpath, default="")
        vdir = _version_dir_for(module, cleaned_file)
        os.makedirs(vdir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        vid = f"{ts}_{clean_id(reason, 'version')}"
        yaml_name = f"{vid}.yaml"
        meta_name = f"{vid}.json"
        write_text_file(safe_join(vdir, yaml_name), content or "")
        meta = {
            "id": vid,
            "module": module,
            "file": cleaned_file,
            "reason": reason,
            "yaml": yaml_name,
            "created_at": _now_ts(),
            "size": len((content or "").encode("utf-8")),
        }
        write_json_file(safe_join(vdir, meta_name), meta)
        return meta
    except Exception as exc:  # noqa: BLE001 — backup failure must not block repair
        print(f"backup_before_repair failed: {module}/{file}: {exc}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


class RepairApplyError(Exception):
    """应用修复草稿时的业务级失败。"""

    def __init__(self, message: str, *, status: int = 400, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


def apply_repair_draft(
    draft_id: Any,
    *,
    confirm: bool = False,
    confirm_risk: bool = False,
    yaml_validator=None,
) -> Dict[str, Any]:
    """应用修复草稿到基线 YAML。

    步骤：
      1. 校验草稿存在且处于可应用状态（DRAFTED / WAIT_CONFIRM）
      2. 强制人工确认 ``confirm=True``；如风险命中则需 ``confirm_risk=True``
      3. ``ENABLE_AUTOMATIC_BASELINE_REPAIR=False`` 时禁止自动覆盖基线
      4. 可选 ``yaml_validator(yaml_text)`` 执行外部校验，结果不为 ok 则中止
      5. 备份原文件 → 原子写入新 YAML → 标记草稿为 APPLIED
      6. 记录修复历史到知识库（失败不影响主流程）
    """
    draft = get_repair_draft(draft_id)
    if not draft:
        raise RepairApplyError("修复草稿不存在", status=404)
    if draft.get("status") not in ("DRAFTED", "WAIT_CONFIRM"):
        raise RepairApplyError(f"当前草稿状态不可应用：{draft.get('status')}")
    if not confirm:
        raise RepairApplyError("必须人工确认 confirm=True 后才能应用修复草稿")

    risk_hits = draft.get("riskHits") or draft.get("risk_hits") or []
    if risk_hits and not confirm_risk:
        raise RepairApplyError("修复草稿包含高风险动作，必须 confirm_risk=True")

    if not ENABLE_AUTOMATIC_BASELINE_REPAIR and not confirm:
        # 双保险：除非 confirm=True 显式确认，否则禁止覆盖基线
        raise RepairApplyError("禁止自动覆盖基线：未启用 ENABLE_AUTOMATIC_BASELINE_REPAIR 且未人工确认")

    module = (draft.get("module") or "").strip()
    file = clean_filename(draft.get("file") or "")
    fixed_yaml = draft.get("fixedYaml") or draft.get("fixed_yaml") or ""
    if not module or not file:
        raise RepairApplyError("草稿缺少 module/file，不能应用")
    if not str(fixed_yaml).strip():
        raise RepairApplyError("草稿缺少 fixedYaml，不能应用")

    yaml_check: Dict[str, Any] = {"ok": True}
    if callable(yaml_validator):
        try:
            yaml_check = yaml_validator(fixed_yaml) or {"ok": True}
        except Exception as exc:  # noqa: BLE001
            raise RepairApplyError(f"YAML 校验异常：{exc}") from exc
        if not yaml_check.get("ok"):
            raise RepairApplyError(
                "YAML 校验未通过，不能应用",
                payload={"yaml_check": yaml_check},
            )

    try:
        target_path = safe_join(TASK_DIR, module, file)
    except ValueError as exc:
        raise RepairApplyError("非法路径") from exc

    # 必须先备份再写入
    backup = backup_before_repair(module, file, reason="before_repair_draft_apply")

    # 记录旧 YAML 哈希用于知识库
    old_content = ""
    try:
        old_content = read_text_file(target_path, default="")
    except Exception:
        pass

    try:
        write_text_file(target_path, fixed_yaml)
    except Exception as exc:  # noqa: BLE001
        raise RepairApplyError(f"写入基线 YAML 失败：{exc}", status=500) from exc

    draft["status"] = "APPLIED"
    draft["appliedAt"] = draft["applied_at"] = _now_ts()
    draft["backup"] = backup or {}
    draft["yaml_check"] = yaml_check
    saved = upsert_repair_draft(draft)

    # 知识记录钩子：修复成功后记录修复历史
    try:
        from task_server.services import knowledge_service
        knowledge_service.record_repair_history(
            yaml_file=draft.get("file") or "",
            module=module,
            old_yaml_hash=hashlib.md5(old_content.encode()).hexdigest()[:8],
            new_yaml_hash=hashlib.md5(fixed_yaml.encode()).hexdigest()[:8],
            repair_reason=draft.get("analysis") or draft.get("failureType") or "",
            success=True,
        )
    except Exception:
        pass  # 知识记录失败不影响主流程

    return {
        "ok": True,
        "applied": True,
        "draft": saved,
        "backup": backup,
        "yaml_check": yaml_check,
    }


__all__ = [
    "REPAIRABLE_FAILURE_TYPES",
    "NON_REPAIRABLE_FAILURE_TYPES",
    "REPAIR_DRAFT_STATUSES",
    "RepairApplyError",
    "active_repair_draft_for_job",
    "apply_repair_draft",
    "assess_repair_risk",
    "backup_before_repair",
    "build_repair_draft_from_ai",
    "call_dashscope_repair_yaml_task_patch",
    "call_dashscope_repair_yaml_task",
    "call_dashscope_repair_yaml",
    "call_dashscope_failure_review",
    "can_generate_yaml_repair",
    "get_repair_draft",
    "load_repair_drafts",
    "normalize_repair_draft",
    "reject_repair_draft",
    "repair_drafts_for_job",
    "save_repair_drafts",
    "upsert_repair_draft",
    "validate_repair_draft",
]


# ``LEARNING_DIR`` import retained for callers that build sibling paths.
_ = LEARNING_DIR


# ---------------------------------------------------------------------------
# AI 修复草稿构建 & 校验
# ---------------------------------------------------------------------------

def build_repair_draft_from_ai(
    job: Dict[str, Any],
    analysis: Dict[str, Any],
    fixed_yaml: str,
) -> Dict[str, Any]:
    """从 AI 分析输出构建修复草稿。

    整合 job 上下文、AI 失败分析结果和 AI 生成的修复 YAML，
    构建一个标准化的 repair draft 并持久化。

    Args:
        job: 失败的 job 记录（需包含 ``job_id`` / ``module`` / ``file`` 等）。
        analysis: AI 分析结果，通常包含 ``failureType`` / ``summary`` / ``suggestion``。
        fixed_yaml: AI 生成的修复后 YAML 文本。

    Returns:
        标准化并持久化后的 repair draft 字典。

    Raises:
        ValueError: 当故障类型不可修复时。
    """
    failure_type = str(
        analysis.get("failureType") or analysis.get("failure_type") or "UNKNOWN"
    ).upper()

    if not can_generate_yaml_repair(failure_type):
        raise ValueError(
            f"故障类型 {failure_type} 不可生成 YAML 修复，"
            f"仅 {REPAIRABLE_FAILURE_TYPES} 允许自动修复"
        )

    job_id = str(
        job.get("job_id") or job.get("jobId") or job.get("id") or ""
    ).strip()
    module = str(job.get("module") or "").strip()
    file_name = str(job.get("file") or "").strip()

    # 读取原始 YAML 用于差异对比
    original_yaml = ""
    if module and file_name:
        try:
            original_yaml = read_text_file(
                safe_join(TASK_DIR, module, clean_filename(file_name)),
                default="",
            )
        except Exception:
            pass

    # 风险评估
    risk = assess_repair_risk(original_yaml, fixed_yaml)

    # 构建 diff（使用 yaml_service 的 diff_yaml）
    diff_text = ""
    try:
        from .yaml_service import diff_yaml
        diff_text = diff_yaml(original_yaml, fixed_yaml)
    except Exception:
        diff_text = ""

    draft: Dict[str, Any] = {
        "jobId": job_id,
        "job_id": job_id,
        "module": module,
        "file": file_name,
        "taskName": str(
            job.get("target_task_name")
            or job.get("targetTaskName")
            or job.get("current_task_name")
            or ""
        ).strip(),
        "status": "DRAFTED",
        "failureType": failure_type,
        "riskLevel": risk.get("riskLevel", "medium"),
        "riskHits": risk.get("riskHits", []),
        "newHits": risk.get("newHits", []),
        "analysis": str(analysis.get("summary") or analysis.get("suggestion") or ""),
        "originalYaml": original_yaml,
        "original_yaml": original_yaml,
        "fixedYaml": fixed_yaml,
        "fixed_yaml": fixed_yaml,
        "diff": diff_text,
        "validation": {},
    }
    return upsert_repair_draft(draft)


def validate_repair_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    """校验修复草稿（YAML 语法 + Midscene flow + 风险）。

    校验维度：
    1. **YAML 语法**：使用 :func:`yaml_service.validate_yaml` 检查格式合法性。
    2. **Midscene flow**：检查 flow 动作是否在白名单内。
    3. **风险扫描**：检查修复后 YAML 是否命中高风险关键词。

    Args:
        draft: 修复草稿字典，需包含 ``fixedYaml`` / ``fixed_yaml`` 字段。

    Returns:
        校验结果字典::

            {
                "ok": bool,
                "yaml_warnings": [...],
                "flow_warnings": [...],
                "risk_assessment": {...},
                "can_apply": bool,
            }
    """
    fixed_yaml = str(
        draft.get("fixedYaml") or draft.get("fixed_yaml") or ""
    )

    result: Dict[str, Any] = {
        "ok": True,
        "yaml_warnings": [],
        "flow_warnings": [],
        "risk_assessment": {},
        "can_apply": True,
    }

    # 1. YAML 语法校验
    yaml_warnings: List[str] = []
    try:
        from .yaml_service import validate_yaml, validate_midscene_flow
        yaml_warnings = validate_yaml(fixed_yaml)
        result["yaml_warnings"] = yaml_warnings
        if yaml_warnings:
            result["ok"] = False
            result["can_apply"] = False

        # 2. Midscene flow 校验
        flow_warnings = validate_midscene_flow(fixed_yaml)
        result["flow_warnings"] = flow_warnings
        if flow_warnings:
            result["ok"] = False
            result["can_apply"] = False
    except Exception as exc:
        yaml_warnings.append(f"YAML 校验异常：{exc}")
        result["yaml_warnings"] = yaml_warnings
        result["ok"] = False
        result["can_apply"] = False

    # 3. 风险扫描
    original_yaml = str(
        draft.get("originalYaml") or draft.get("original_yaml") or ""
    )
    risk = assess_repair_risk(original_yaml, fixed_yaml)
    result["risk_assessment"] = risk
    if risk.get("riskLevel") == "high":
        # 高风险不阻止应用，但需要额外 confirm_risk
        result["can_apply"] = False

    return result


# ---------------------------------------------------------------------------
# DashScope AI 修复函数（从 midscene-upload.py 全量迁移）
# ---------------------------------------------------------------------------

def call_dashscope_repair_yaml_task_patch(module, file, task_name, yaml_text, task_block, stdout, stderr, summary, execution_images=None):
    """AI 修复单条 task：生成补丁而非重写整个 YAML。

    Migrated from ``midscene-upload.py:call_dashscope_repair_yaml_task_patch``.
    """
    log_text = "\n".join([
        "STDOUT:",
        (stdout or "")[-6000:],
        "STDERR:",
        (stderr or "")[-3000:],
        "SUMMARY:",
        json.dumps(summary, ensure_ascii=False)[:4000] if summary is not None else ""
    ])
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    knowledge_text, knowledge_images, used_pages = repair_knowledge_context(module, file, yaml_text, log_text, task_name)
    execution_images = execution_images or []
    repair_images = (execution_images + knowledge_images)[:6]
    business_context = task_business_context(task_block, knowledge_text)
    skill_payload = {
        "module": module,
        "file": file,
        "task_name": task_name,
        "business_context": business_context,
        "failure_brief": failure_brief,
        "page_knowledge": knowledge_text or "",
        "task_block": task_block,
        "log_text": log_text,
        "repair_strategy": repair_strategy_guide(),
        "framework": {
            "task": "保存 YAML、应用补丁、校验语法和业务链路",
            "qwen": "失败分析和补丁规划",
            "midscene": "执行自然语言 UI intent",
            "sonic": "基线回归稳定性"
        }
    }
    try:
        parsed_obj = normalize_ai_object(
            run_ai_skill("repair_patch_planner", skill_payload, image_assets=repair_images, timeout=240),
            default_key="patches",
        )
        patches = parsed_obj.get("patches") or []
        if not isinstance(patches, list):
            raise ValueError("模型返回的 patches 必须是数组")
        return {
            "analysis": parsed_obj.get("analysis") or "",
            "changes": parsed_obj.get("changes") or [],
            "patches": patches,
            "used_knowledge_pages": used_pages,
            "used_execution_screenshots": [item.get("name", "") for item in execution_images],
            "repair_patch_skill": "repair_patch_planner.v1"
        }
    except Exception as exc:
        legacy_error = str(exc)
    prompt_center_prefix = _repair_prompt_center_prefix(
        module=module,
        file=file,
        task_name=task_name,
        yaml_text=yaml_text,
        task_block=task_block,
        knowledge_text=knowledge_text,
        failure_brief=failure_brief,
        business_context=business_context,
    )
    prompt = f"""
{prompt_center_prefix}

你是 Midscene Android UI 自动化 YAML 单用例修复助手。
你不能直接重写 YAML，只能输出补丁。服务端会应用补丁并校验语法与业务链路。

严格要求：
1. 只输出合法 JSON，不要 Markdown。
2. JSON 必须包含 analysis、changes、patches。
3. patches 最多 2 条，只允许修改当前失败点附近，不要批量重写。
4. 每条 patch 格式：
   {{"op":"insert_after|insert_before|replace_step|remove_step","anchor":"原 YAML 中某个完整步骤的关键文本","lines":["aiWaitFor: ...","timeout: 30000"],"reason":"为什么改"}}
5. lines 不要写 android/tasks/name/flow 外层，只写 flow 里的步骤或子字段；缩进由服务端处理。
6. 必须保留原业务主线：{business_context.get("business_path", "")}
7. 如果 failure_brief.repair_plan.can_repair_yaml=false，patches 必须为空，并在 analysis 说明应先处理环境/产品问题。
8. 不要输出完整 YAML，不要输出 task 字符串。
9. 必须按业务域修复等待条件：只有 3D/模型/建模/切片/STL/OBJ/模型导入链路才允许出现"模型处理进度/100%"等待；2D/文档/错题/基础打印/相册/扫描/格式转换链路禁止写"模型处理进度"，应等待目标按钮、打印前准备完成、确认弹窗或真实业务页面状态。
10. 不要把"确认打印"单独当成模型处理；它可能属于 2D 打印确认。是否需要长等待必须结合当前 task 的 goal/path/actions 判断。
11. 不能瞎改原流程：补丁只能围绕失败点附近做最小改动，禁止删除、重排、替换失败点之前已经成功执行的核心业务步骤；禁止把原本测试 A 功能的链路改成 B 功能链路。
12. 如果要替换步骤，replace_step 只能替换当前失败步骤或紧邻失败步骤；不得整段替换从入口到结果的主链路。服务端会校验业务锚点和步骤顺序，不通过会拒绝保存。

模块：{module}
文件：{file}
目标用例：{task_name}

业务链路上下文：
{json.dumps(business_context, ensure_ascii=False, indent=2)}

失败摘要：
{json.dumps(failure_brief, ensure_ascii=False, indent=2)}

页面知识：
{knowledge_text or "无"}

当前 task：
{task_block}

执行日志：
{log_text}

输出示例：
{{
  "analysis": "点击完成后 PNG 尚未渲染，需在完成后等待 PNG 出现",
  "changes": ["在 aiTap: 完成 后插入等待 PNG 出现"],
  "patches": [
    {{"op":"insert_after","anchor":"aiTap: 完成","lines":["aiWaitFor: 页面已完成处理并出现 PNG 选项","timeout: 30000"],"reason":"PNG 未渲染"}}
  ]
}}
"""
    parsed_obj = normalize_ai_object(
        normalize_model_json(dashscope_chat_content(prompt, repair_images, temperature=0.1)),
        default_key="patches",
    )
    patches = parsed_obj.get("patches") or parsed_obj.get("patch") or []
    if not isinstance(patches, list):
        raise ValueError("模型返回的 patches 必须是数组")
    return {
        "analysis": parsed_obj.get("analysis") or parsed_obj.get("reason") or "",
        "changes": parsed_obj.get("changes") or [],
        "patches": patches,
        "used_knowledge_pages": used_pages,
        "used_execution_screenshots": [item.get("name", "") for item in execution_images],
        "repair_patch_skill": "fallback_legacy_repair_prompt",
        "repair_patch_skill_error": legacy_error
    }


def call_dashscope_repair_yaml_task(module, file, task_name, yaml_text, task_block, stdout, stderr, summary, execution_images=None):
    """AI 修复单条 task：重写整个 task block。

    Migrated from ``midscene-upload.py:call_dashscope_repair_yaml_task``.
    """
    dashscope_api_key()

    log_text = "\n".join([
        "STDOUT:",
        (stdout or "")[-6000:],
        "STDERR:",
        (stderr or "")[-3000:],
        "SUMMARY:",
        json.dumps(summary, ensure_ascii=False)[:4000] if summary is not None else ""
    ])
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    knowledge_text, knowledge_images, used_pages = repair_knowledge_context(module, file, yaml_text, log_text, task_name)
    execution_images = execution_images or []
    repair_images = (execution_images + knowledge_images)[:6]
    business_context = task_business_context(task_block, knowledge_text)
    prompt_center_prefix = _repair_prompt_center_prefix(
        module=module,
        file=file,
        task_name=task_name,
        yaml_text=yaml_text,
        task_block=task_block,
        knowledge_text=knowledge_text,
        failure_brief=failure_brief,
        business_context=business_context,
    )
    prompt = f"""
{prompt_center_prefix}

你是 Midscene Android UI 自动化 YAML 单用例修复助手。
请根据执行日志，只修复指定的这一条 task，让它更有机会在下一轮执行通过。

严格要求：
1. 只输出合法 JSON，不要 Markdown，不要解释。
2. JSON 必须包含 analysis、changes、task。
3. task 必须是字符串类型，内容是一条 YAML task block，必须从 "- name:" 开始；不要输出 JSON 对象、数组或 android/tasks 外层。
4. 只修复名为「{task_name}」的用例，不要新增其他用例。
5. 按 Midscene 1.7.20 YAML 语法生成：优先使用 ai、aiTap、aiInput + value、aiWaitFor、aiAssert、sleep、launch、runAdbShell；aiAction 仅作为旧脚本兼容，不要在新增步骤里优先使用；修复时不要新增 recordToReport。
5.1 flowItem 名称大小写必须严格正确，例如 aiTap、aiInput、aiWaitFor、aiAssert、runAdbShell；禁止输出 aitap、aiinput、aiwaitfor、runadbshell 这类小写变体。
6. 不要生成坐标点击。
7. 如果是断言过严，优先改成更贴近页面可见状态的 aiAssert 或 aiWaitFor 验证。
8. 如果是找不到入口，优先增加更明确的导航步骤、等待或先回到首页的步骤。
9. 保留前置 HOME + force-stop + launch 和后置 force-stop，包名沿用原内容。
10. 不要写 deviceId。
11. 如果日志表现为弹窗、权限框、升级框、广告、活动浮层、引导浮层遮挡，应在关键步骤前增加自然语言弹窗处理，不要用坐标。
12. 如果失败原因是当前页面不在预期页面，应增加回到首页、点击底部首页 Tab、重新进入目标入口等稳定导航步骤。
13. 如果提供了 APP 页面知识和辅助截图，必须优先参考真实页面标题、入口文案、按钮文案、Tab、常用断言来修复步骤和断言。
14. 辅助截图只用于理解真实页面，不要写坐标；如果截图和 YAML 文案冲突，优先使用截图/页面知识中的真实可见文案。
15. 保留并更新 task 内的 # baseline.goal / # baseline.start_page / # baseline.path / # baseline.expected / # baseline.repair_hint 注释；这些注释是基线链路说明，不是执行步骤。
16. 如果当前 task 缺少 baseline 注释，请根据用例名、步骤、页面知识和截图补齐；不要把这些说明改成执行步骤。
17. 不要滥用固定长等待。普通页面切换用短 sleep；如果网络、接口、资源加载可能较慢，要用 aiWaitFor + timeout 等待"页面标题/按钮/列表/空态/目标入口可见"，不要用 sleep: 5000 这类无条件等待；timeout 上限 300000ms，不能越修越长。
18. 如果失败更像产品 Bug、环境问题、模型配置问题、设备问题，不要为了通过而篡改业务断言；analysis 中说明原因，task 尽量只做安全的稳定性补充。
19. 华为/系统文件管理器、相册、文件选择器里的搜索框输入必须优先使用 Midscene 1.7.20 标准写法：先 aiTap 搜索输入框，再 aiInput: "当前页面的搜索输入框或文本输入框" 并在同级写 value: "实际输入内容"、autoDismissKeyboard: false、mode: "replace"；不要默认再补 runAdbShell: "input text xxx"，只有失败日志明确证明 aiInput 没有实际输入、输入框为空或无法输入时，才允许增加 adb input text 兜底，避免重复输入。
20. 必须先按业务链路上下文理解原 YAML：goal 是测试目的，business_path 是核心路径，expected_result 是业务预期，current_actions/current_assertions 是现有执行链路。修复只能围绕这些内容做最小改动，不能替换成另一个业务流程。
21. 保存、下载、导出、生成、转换类结果操作如果失败原因是"没看到成功/已保存/完成提示"，要先看原业务链路，不要模板化批量插入校验。只允许围绕失败点做最小改动，例如调整一个等待条件或补一个失败态断言；不要把中间"完成/确认/PNG"等步骤误判成最终保存结果。
22. 如果报错说明点击"完成/确认/下一步"后下一个目标按钮或格式选项尚未渲染，例如 PNG/PDF/Word/导出/确认按钮未出现，修复应只在该失败点附近补等待，不要顺手改其它步骤。
23. 业务域不能串台：只有 3D/模型/建模/切片/STL/OBJ/模型导入链路才允许写"模型处理进度/100%"等待；2D/文档/错题/基础打印/相册/扫描/格式转换链路禁止套用"模型处理进度"，要等待目标按钮、打印前准备完成、确认弹窗/按钮或真实业务页面状态。
24. 报告关键帧若明确显示同级入口行在屏幕边缘被裁切，应在失败等待前补官方 aiScroll。区域必须用当前页真实可见文案描述，使用 `scrollType: "singleAction"` + `direction: "right"` + 不超过 400 的 `distance`，滑动后重新等待目标；一次不足时最多补第二次。禁止坐标、ADB swipe、整页盲滑和方向互相矛盾的描述。
25. 不能瞎改原流程：原 YAML 中已经成功到达的核心业务步骤、入口顺序和目标断言必须保留。修复只能调整失败点附近的定位描述、等待条件、输入参数、弹窗处理或断言表达；不得删除核心业务动作，不得把链路改成另一个功能。
26. 如果当前失败是产品 toast、业务错误、数据不满足、环境问题或模型配置问题，analysis 中说明原因，task 保持原流程，不要为了通过删断言、改目标、绕开失败页面。
27. 字符串必须完整闭合：任何 `ai/aiTap/aiAssert/aiWaitFor/runAdbShell` 等带引号的值，行尾必须有对应的结束引号；禁止输出 `- ai: "xxx` 这种未闭合字符串。无法确定时不要加外层引号，由服务端统一转义。

{repair_strategy_guide()}

模块：{module}
文件：{file}
目标用例：{task_name}

业务链路上下文：
{json.dumps(business_context, ensure_ascii=False, indent=2)}

失败摘要：
{json.dumps(failure_brief, ensure_ascii=False, indent=2)}

自动匹配到的 APP 页面知识：
{knowledge_text or "无"}

已附加 Midscene 失败现场截图数量：{len(execution_images)}
已附加页面辅助截图数量：{len(knowledge_images)}

完整 YAML 仅供上下文参考：
{yaml_text[-10000:]}

当前要修复的 task：
{task_block}

执行日志：
{log_text}

输出格式：
{{
  "analysis": "失败原因简述",
  "changes": ["修改点1", "修改点2"],
  "task": "- name: \\"{task_name}\\"\\n  flow:\\n    - ..."
}}
"""
    result = normalize_yaml_task_block_from_model(dashscope_chat_content(prompt, repair_images, temperature=0.1))
    result["used_knowledge_pages"] = used_pages
    result["used_execution_screenshots"] = [item.get("name", "") for item in execution_images]
    return result


def call_dashscope_repair_yaml(module, file, yaml_text, stdout, stderr, summary, execution_images=None):
    """AI 修复整个 YAML 文件。

    Migrated from ``midscene-upload.py:call_dashscope_repair_yaml``.
    """
    dashscope_api_key()

    log_text = "\n".join([
        "STDOUT:",
        (stdout or "")[-6000:],
        "STDERR:",
        (stderr or "")[-3000:],
        "SUMMARY:",
        json.dumps(summary, ensure_ascii=False)[:4000] if summary is not None else ""
    ])
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    knowledge_text, knowledge_images, used_pages = repair_knowledge_context(module, file, yaml_text, log_text)
    execution_images = execution_images or []
    repair_images = (execution_images + knowledge_images)[:6]
    names = yaml_task_names(yaml_text)
    business_contexts = []
    for name in names[:12]:
        try:
            info = find_yaml_task_block(yaml_text, name)
            business_contexts.append({"task": name, **task_business_context(info["block"], knowledge_text)})
        except Exception:
            continue
    prompt_center_prefix = _repair_prompt_center_prefix(
        module=module,
        file=file,
        yaml_text=yaml_text,
        knowledge_text=knowledge_text,
        failure_brief=failure_brief,
        business_context=business_contexts,
    )
    prompt = f"""
{prompt_center_prefix}

你是 Midscene Android UI 自动化 YAML 修复助手。
请根据执行日志修复 YAML，让它更有机会在下一轮执行通过。

严格要求：
1. 只输出合法 JSON，不要 Markdown，不要解释。
2. JSON 必须包含 analysis、changes、content。
3. content 必须是字符串类型，内容是完整 YAML，不是片段；不要输出 JSON 对象或数组。
4. 只修改和失败原因相关的步骤，不要重写无关用例。
5. 按 Midscene 1.7.20 YAML 语法生成：优先使用 ai、aiTap、aiInput + value、aiWaitFor、aiAssert、sleep、launch、runAdbShell；aiAction 仅作为旧脚本兼容，不要在新增步骤里优先使用；修复时不要新增 recordToReport。
5.1 flowItem 名称大小写必须严格正确，例如 aiTap、aiInput、aiWaitFor、aiAssert、runAdbShell；禁止输出 aitap、aiinput、aiwaitfor、runadbshell 这类小写变体。
6. 不要生成坐标点击。
7. 如果是断言过严，优先把断言改成更贴近页面可见状态的 aiAssert 或 aiWaitFor 验证。
8. 如果是找不到入口，优先增加更明确的导航步骤或等待。
9. 保留 android 节点，不要写 deviceId。
10. 每条 task 都必须能独立执行：包含启动 App、处理弹窗/浮层、进入稳定起点、执行步骤、验证结果、关闭 App。
11. 如果日志表现为弹窗、权限框、升级框、广告、活动浮层、引导浮层遮挡，应在关键步骤前增加自然语言弹窗处理，不要用坐标。
12. 如果失败原因是当前页面不在预期页面，应增加回到首页、点击底部首页 Tab、重新进入目标入口等稳定导航步骤。
13. 如果提供了 APP 页面知识和辅助截图，必须优先参考真实页面标题、入口文案、按钮文案、Tab、常用断言来修复步骤和断言。
14. 辅助截图只用于理解真实页面，不要写坐标；如果截图和 YAML 文案冲突，优先使用截图/页面知识中的真实可见文案。
15. 保留并更新每条 task 内的 # baseline.goal / # baseline.start_page / # baseline.path / # baseline.expected / # baseline.repair_hint 注释；这些注释是基线链路说明，不是执行步骤。
16. 如果某条 task 缺少 baseline 注释，请根据用例名、步骤、页面知识和截图补齐；不要把这些说明改成执行步骤。
17. 不要滥用固定长等待。普通页面切换用短 sleep；如果网络、接口、资源加载可能较慢，要用 aiWaitFor + timeout 等待"页面标题/按钮/列表/空态/目标入口可见"，不要用 sleep: 5000 这类无条件等待；timeout 上限 300000ms，不能越修越长。
18. 如果失败更像产品 Bug、环境问题、模型配置问题、设备问题，不要为了通过而篡改业务断言；analysis 中说明原因，content 尽量只做安全的稳定性补充。
19. 华为/系统文件管理器、相册、文件选择器里的搜索框输入必须优先使用 Midscene 1.7.20 标准写法：先 aiTap 搜索输入框，再 aiInput: "当前页面的搜索输入框或文本输入框" 并在同级写 value: "实际输入内容"、autoDismissKeyboard: false、mode: "replace"；不要默认再补 runAdbShell: "input text xxx"，只有失败日志明确证明 aiInput 没有实际输入、输入框为空或无法输入时，才允许增加 adb input text 兜底，避免重复输入。
20. 必须先按业务链路上下文理解原 YAML：goal 是测试目的，business_path 是核心路径，expected_result 是业务预期，current_actions/current_assertions 是现有执行链路。修复只能围绕这些内容做最小改动，不能替换成另一个业务流程。
21. 保存、下载、导出、生成、转换类结果操作如果失败原因是"没看到成功/已保存/完成提示"，要先看原业务链路，不要模板化批量插入校验。只允许围绕失败点做最小改动，例如调整一个等待条件或补一个失败态断言；不要把中间"完成/确认/PNG"等步骤误判成最终保存结果。
22. 如果报错说明点击"完成/确认/下一步"后下一个目标按钮或格式选项尚未渲染，例如 PNG/PDF/Word/导出/确认按钮未出现，修复应只在该失败点附近补等待，不要顺手改其它步骤。

{repair_strategy_guide()}

模块：{module}
文件：{file}

业务链路上下文：
{json.dumps(business_contexts, ensure_ascii=False, indent=2)}

失败摘要：
{json.dumps(failure_brief, ensure_ascii=False, indent=2)}

自动匹配到的 APP 页面知识：
{knowledge_text or "无"}

已附加 Midscene 失败现场截图数量：{len(execution_images)}
已附加页面辅助截图数量：{len(knowledge_images)}

当前 YAML：
{yaml_text}

执行日志：
{log_text}

输出格式：
{{
  "analysis": "失败原因简述",
  "changes": ["修改点1", "修改点2"],
  "content": "完整 YAML 内容"
}}
"""
    result = normalize_yaml_from_model(dashscope_chat_content(prompt, repair_images, temperature=0.1))
    result["used_knowledge_pages"] = used_pages
    result["used_execution_screenshots"] = [item.get("name", "") for item in execution_images]
    return result


def call_dashscope_failure_review(job, stdout, stderr, summary):
    """AI 失败复检：判断失败类别和可修复性。

    Migrated from ``midscene-upload.py:call_dashscope_failure_review``.
    """
    dashscope_api_key()
    module = job.get("module", "")
    file = job.get("file", "")
    yaml_path = safe_join(TASK_DIR, module, file)
    yaml_text = ""
    if os.path.exists(yaml_path):
        with open(yaml_path, encoding="utf-8") as f:
            yaml_text = f.read()
    deterministic_issues = []
    if "launch:" not in yaml_text:
        deterministic_issues.append("缺少 launch 前置启动 App")
    if "terminate:" not in yaml_text and "am force-stop" not in yaml_text:
        deterministic_issues.append("缺少后置关闭 App")
    if deterministic_issues:
        return {
            "category": "script_issue",
            "confidence": 0.95,
            "reason": "；".join(deterministic_issues),
            "evidence": deterministic_issues,
            "suggested_action": "优先执行规则修复，补齐启动/关闭等运行时守卫后再重跑",
            "can_auto_repair": True
        }
    ctx = build_failure_context(job, yaml_text, stdout, stderr, summary)
    review_images = (execution_screenshot_context(job, limit=4) + report_image_context(job, limit=4))[:6]
    log_text = "\n".join([
        "STDOUT:",
        (stdout or "")[-6000:],
        "STDERR:",
        (stderr or "")[-3000:],
        "SUMMARY:",
        json.dumps(summary, ensure_ascii=False)[:4000] if summary is not None else "",
        "REPORT_TEXT:",
        (ctx.get("report_text") or "")[-6000:]
    ])
    deterministic_review = classify_failure_by_context(ctx)
    if deterministic_review:
        low_confidence_visual_types = {"assertion_too_strict", "element_not_found", "wait_strategy", "popup_overlay"}
        if not (review_images and deterministic_review.get("failure_type") in low_confidence_visual_types):
            return sanitize_failure_review_against_sources(deterministic_review, yaml_text, stdout, stderr, summary, ctx)
    wait_issue = detect_wait_strategy_issue(yaml_text, log_text)
    if wait_issue:
        return sanitize_failure_review_against_sources(wait_issue, yaml_text, stdout, stderr, summary, ctx)
    horizontal_scroll_issue = detect_horizontal_scroll_script_issue(yaml_text, log_text)
    if horizontal_scroll_issue:
        return sanitize_failure_review_against_sources(horizontal_scroll_issue, yaml_text, stdout, stderr, summary, ctx)
    yaml_syntax_signals = ("unknown flowitem", "failed to load", "property \"tasks\" is required", "cannot use 'in' operator", "yaml格式", "yaml语法")
    if any(signal in log_text.lower() for signal in yaml_syntax_signals):
        return {
            "category": "script_issue",
            "confidence": 0.92,
            "reason": "执行日志显示 YAML 语法或 flowItem 不兼容",
            "evidence": [line for line in log_text.splitlines() if any(signal in line.lower() for signal in yaml_syntax_signals)][:6],
            "suggested_action": "优先规则修复 YAML 语法、flowItem 名称和缩进结构，不改业务断言",
            "can_auto_repair": True
        }
    prompt = f"""
你是移动 App 测试失败复检助手。
请根据 YAML、执行日志和 summary 判断失败更可能属于哪一类。

分类只能选择：
- product_bug：疑似产品缺陷
- script_issue：疑似脚本问题
- env_issue：疑似环境/设备/模型/网络问题
- data_issue：疑似测试数据或账号状态问题
- unknown：无法判断

要求：
1. 只输出合法 JSON，不要 Markdown。
2. 不要因为脚本失败就默认是脚本问题；测试模式下要优先保护真实缺陷。
3. 如果页面行为和需求预期不一致，归为 product_bug。
4. 如果 YAML 动作明显不合理、入口文案臆造、断言过严，归为 script_issue。
4.1 如果业务本身需要长时间处理（模型加载、切片、上传、生成、进度条到 100%、确认打印按钮出现），并且 YAML 只等待 20~30 秒或等待条件过于泛化，可以先归为 script_issue，建议只做一次等待策略修复；如果 YAML 已经有足够长的条件等待后仍失败，不要继续放宽脚本，应回到 product_bug/env_issue/unknown 复核。
4.2 如果截图/报告明确显示同级 icon 或导入入口行在屏幕边缘被裁切，目标可能位于屏外：原 YAML 没有横向 aiScroll 时属于遗漏屏外探索，已有 aiScroll 但仍只显示前几个入口时属于滑动未生效；两者都先归为 script_issue，允许基于关键帧做一次有界横向修复后重跑，不要直接判 product_bug。
5. 如果截图或报告中出现 toast/浮层/运行时错误文案，例如"The mapper function returned a null value."、"系统异常"、"操作失败"，并且页面没有达到业务预期，应优先归为 product_bug 或 data_issue，can_auto_repair=false；不要把它当成普通"按钮没找到"去放宽断言。
6. 如果是设备断连、模型配置、adb、超时、网络，归为 env_issue。
7. 严格禁止引用当前 YAML、日志、summary、报告文本中没有出现过的按钮、控件或步骤；如果无法确认，就归为 unknown，can_auto_repair=false。比如当前 YAML 没有"确认打印"，日志也没有"确认打印"，就不能说脚本等待"确认打印"。
8. 区分"YAML 当前内容"和"产品知识/历史经验"：只能把当前 YAML flow 中真实存在的步骤称为"脚本步骤"。

任务：{module}/{file}
执行模式：{job.get("run_mode", "test")}

YAML：
{yaml_text[-8000:]}

日志：
{log_text}

如果本次任务有执行截图，下面会随请求一起发送。请重点观察截图中间或底部是否出现 toast、半透明浮层、错误文案、加载失败、系统异常等短暂提示。

输出格式：
{{
  "category": "product_bug",
  "confidence": 0.8,
  "reason": "失败原因简述",
  "evidence": ["证据1", "证据2"],
  "suggested_action": "建议动作",
  "can_auto_repair": false
}}
"""
    review = normalize_model_json(dashscope_chat_content(prompt, image_assets=review_images, temperature=0.1, timeout=240))
    category = review.get("category") or "unknown"
    if category not in ("product_bug", "script_issue", "env_issue", "data_issue", "unknown"):
        category = "unknown"
    normalized_review = {
        "category": category,
        "confidence": float(review.get("confidence") or 0),
        "reason": review.get("reason", ""),
        "evidence": review.get("evidence") or [],
        "suggested_action": review.get("suggested_action", ""),
        "can_auto_repair": safe_bool(review.get("can_auto_repair")) and category == "script_issue"
    }
    return sanitize_failure_review_against_sources(normalized_review, yaml_text, stdout, stderr, summary, ctx)




# ---------------------------------------------------------------------------
# Migrated from midscene-upload.py
# ---------------------------------------------------------------------------

def repair_file_latest_result(d, job_id=None):
    mod = d.get("module", "")
    file = d.get("file", "")
    create_next = safe_bool(d.get("createJob"), True)
    force = safe_bool(d.get("forceRepair") or d.get("force_repair") or d.get("force"))
    if not mod or not file:
        raise ValueError("module 和 file 不能为空")
    if job_id:
        update_generate_job(job_id, progress=15, step="查找失败记录", message="正在查找该 YAML 最近一次失败执行记录")
    job = latest_failed_job_for_file(mod, file)
    if not job:
        if job_id:
            update_generate_job(job_id, progress=35, step="静态体检", message="没有失败记录，执行静态规则体检")
        result = static_repair_yaml_file(mod, file, create_next=create_next)
        if job_id:
            update_generate_job(job_id, progress=85, step="校验保存", message="静态体检完成，正在校验并保存结果")
        result["mode"] = "static"
        return result
    if job_id:
        update_generate_job(job_id, progress=35, step="分析日志", message="已找到失败记录，正在结合日志和页面知识修复")
        update_generate_job(job_id, progress=55, step="生成修复方案", message="正在生成修复方案并保护原业务链路")
    result = repair_job_and_create_next(job, create_next=create_next, force=force)
    if job_id:
        update_generate_job(job_id, progress=85, step="校验保存", message="修复方案已生成，正在校验 YAML 和保存版本")
    with JOB_LOCK:
        jobs = load_jobs()
        for item in jobs:
            if item.get("job_id") == job.get("job_id"):
                item["manual_repair_result"] = result
                break
        save_jobs(jobs)
    result["mode"] = "ai"
    return result



def repair_job_and_create_next(job, create_next=True, force=False):
    run_dir = job.get("run_dir", "")
    stdout = read_text(Path(run_dir) / "stdout.log") if run_dir else job.get("stdout_tail", "")
    stderr = read_text(Path(run_dir) / "stderr.log") if run_dir else job.get("stderr_tail", "")
    summary = read_json(Path(run_dir) / "summary.json") if run_dir else None
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    if failure_brief.get("failure_type") in ("model_config", "device_env"):
        raise ValueError("当前失败属于环境/配置问题，不应修改 YAML：" + "；".join(failure_brief.get("signals", [])[:3]))
    blocked = None if force else should_block_manual_repair(job, stdout, stderr, summary)
    if blocked:
        raise ValueError(f"当前失败更像 {blocked.get('category')}，不建议自动修复脚本：{blocked.get('reason') or '请人工确认'}")
    repaired, repair_dir = optimize_yaml_after_failure(job, stdout, stderr, summary)
    next_job = None
    if create_next:
        next_job = create_pending_job(
            job["module"],
            job["file"],
            auto_optimize=False,
            max_attempt=safe_int(job.get("max_attempt"), 2),
            attempt=safe_int(job.get("attempt"), 1) + 1,
            parent_job_id=job.get("job_id", ""),
            device_id=job.get("device_id", ""),
            runner_id=job.get("target_runner_id") or job.get("runner_id", ""),
            device_strategy=job.get("device_strategy") or job.get("deviceStrategy") or "",
            run_mode=job.get("run_mode", "test"),
            target_task_name=job.get("target_task_name", "")
        )
    return {
        "ok": True,
        "analysis": repaired.get("analysis", ""),
        "changes": repaired.get("changes", []),
        "repair_dir": repair_dir,
        "updated_file": f"{job.get('module', '')}/{job.get('file', '')}",
        "before_version": repaired.get("before_version"),
        "changed_line_count": repaired.get("changed_line_count", 0),
        "diff_summary": repaired.get("diff_summary", ""),
        "yamlCheck": repaired.get("yamlCheck"),
        "safetyCheck": repaired.get("safetyCheck"),
        "businessFlowCheck": repaired.get("businessFlowCheck"),
        "next_job": next_job
    }



def run_repair_job(job_id, request_data):
    try:
        scope = request_data.get("scope") or "file"
        update_generate_job(job_id, status="running", progress=5, step="开始修复", message="修复任务已启动")
        result = repair_task_latest_result(request_data, job_id=job_id) if scope == "task" else repair_file_latest_result(request_data, job_id=job_id)
        update_generate_job(
            job_id,
            status="success",
            progress=100,
            step="修复完成",
            message="修复完成，已保存 YAML" + ("，并创建重跑任务" if result.get("next_job") else ""),
            result=result
        )
    except Exception as e:
        update_generate_job(job_id, status="failed", progress=90, step="修复失败", message=str(e), error=str(e))



def repair_task_latest_result(d, job_id=None):
    mod = d.get("module", "")
    file = d.get("file", "")
    task_name = d.get("taskName") or d.get("task_name") or ""
    create_next = safe_bool(d.get("createJob"), True)
    force = safe_bool(d.get("forceRepair") or d.get("force_repair") or d.get("force"))
    if not mod or not file or not task_name:
        raise ValueError("module、file 和 taskName 不能为空")
    if job_id:
        update_generate_job(job_id, progress=15, step="查找失败记录", message="正在查找该用例最近一次失败执行记录")
    job = latest_failed_job_for_file_task(mod, file, task_name)
    if not job:
        if job_id:
            update_generate_job(job_id, progress=35, step="静态体检", message="没有失败记录，执行单条静态规则体检")
        result = static_repair_yaml_task(mod, file, task_name, create_next=create_next)
        if job_id:
            update_generate_job(job_id, progress=85, step="校验保存", message="单条静态体检完成，正在校验并保存结果")
        result["mode"] = "static"
        return result
    if job_id:
        update_generate_job(job_id, progress=35, step="分析日志", message="已找到失败记录，正在结合日志和页面知识修复单条用例")
        update_generate_job(job_id, progress=55, step="生成修复方案", message="正在生成单条用例修复方案并保护原业务链路")
    result = repair_job_task_and_create_next(job, task_name, create_next=create_next, force=force)
    if job_id:
        update_generate_job(job_id, progress=85, step="校验保存", message="单条修复方案已生成，正在校验 YAML 和保存版本")
    with JOB_LOCK:
        jobs = load_jobs()
        for item in jobs:
            if item.get("job_id") == job.get("job_id"):
                item["manual_task_repair_result"] = result
                break
        save_jobs(jobs)
    result["mode"] = "ai"
    return result



def safe_repair_artifact_dir(value):
    root = safe_join(LEARNING_DIR, "repairs")
    path = os.path.abspath(value or "")
    if not path:
        raise ValueError("repair_dir 不能为空")
    if path != root and not path.startswith(root + os.sep):
        raise ValueError("非法修复目录")
    return path



def validate_midscene_yaml_executability(yaml_text):
    """Compatibility wrapper for the shared YAML executability validator."""
    from task_server.services.yaml_service import validate_midscene_yaml_executability as _validate
    return _validate(yaml_text)



def optimize_job_yaml_by_scope(job, stdout, stderr, summary):
    task_name = (job.get("target_task_name") or "").strip()
    if task_name:
        return optimize_yaml_task_after_failure(job, task_name, stdout, stderr, summary)
    return optimize_yaml_after_failure(job, stdout, stderr, summary)



def latest_failed_job_for_file(module, file):
    with JOB_LOCK:
        jobs = load_jobs()
    for job in reversed(jobs):
        if job.get("module") == module and job.get("file") == file and job.get("status") != "success" and job.get("run_dir"):
            return job
    return None



def latest_failed_job_for_file_task(module, file, task_name):
    with JOB_LOCK:
        jobs = load_jobs()
    target = (task_name or "").strip()
    for job in reversed(jobs):
        if job.get("module") != module or job.get("file") != file or job.get("status") == "success" or not job.get("run_dir"):
            continue
        if target and job.get("target_task_name") == target:
            return job
        stdout = read_text(Path(job.get("run_dir", "")) / "stdout.log")
        stderr = read_text(Path(job.get("run_dir", "")) / "stderr.log")
        if not target or target in stdout or target in stderr:
            return job
    return None



def should_block_manual_repair(job, stdout, stderr, summary):
    run_mode = job.get("run_mode", "test")
    if run_mode == "baseline":
        return None
    try:
        review = call_dashscope_failure_review(job, stdout, stderr, summary)
    except Exception:
        return None
    if review.get("category") in ("product_bug", "env_issue", "data_issue"):
        return review
    return None



def repair_job_task_and_create_next(job, task_name, create_next=True, force=False):
    run_dir = job.get("run_dir", "")
    stdout = read_text(Path(run_dir) / "stdout.log") if run_dir else job.get("stdout_tail", "")
    stderr = read_text(Path(run_dir) / "stderr.log") if run_dir else job.get("stderr_tail", "")
    summary = read_json(Path(run_dir) / "summary.json") if run_dir else None
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    if failure_brief.get("failure_type") in ("model_config", "device_env"):
        raise ValueError("当前失败属于环境/配置问题，不应修改 YAML：" + "；".join(failure_brief.get("signals", [])[:3]))
    blocked = None if force else should_block_manual_repair(job, stdout, stderr, summary)
    if blocked:
        raise ValueError(f"当前失败更像 {blocked.get('category')}，不建议自动修复脚本：{blocked.get('reason') or '请人工确认'}")
    repaired, repair_dir = optimize_yaml_task_after_failure(job, task_name, stdout, stderr, summary)
    next_job = None
    if create_next:
        next_job = create_pending_job(
            job["module"],
            job["file"],
            auto_optimize=False,
            max_attempt=safe_int(job.get("max_attempt"), 2),
            attempt=safe_int(job.get("attempt"), 1) + 1,
            parent_job_id=job.get("job_id", ""),
            device_id=job.get("device_id", ""),
            runner_id=job.get("target_runner_id") or job.get("runner_id", ""),
            device_strategy=job.get("device_strategy") or job.get("deviceStrategy") or "",
            run_mode=job.get("run_mode", "test"),
            target_task_name=task_name
        )
    return {
        "ok": True,
        "taskName": task_name,
        "source_job_id": job.get("job_id", ""),
        "analysis": repaired.get("analysis", ""),
        "changes": repaired.get("changes", []),
        "repair_dir": repair_dir,
        "updated_file": f"{job.get('module', '')}/{job.get('file', '')}",
        "before_version": repaired.get("before_version"),
        "changed_line_count": repaired.get("changed_line_count", 0),
        "diff_summary": repaired.get("diff_summary", ""),
        "yamlCheck": repaired.get("yamlCheck"),
        "safetyCheck": repaired.get("safetyCheck"),
        "businessFlowCheck": repaired.get("businessFlowCheck"),
        "next_job": next_job
    }



def static_repair_yaml_file(module, file, create_next=False):
    yaml_path = safe_join(TASK_DIR, module, file)
    with open(yaml_path, encoding="utf-8") as f:
        old_yaml = f.read()
    app_package = resolve_app_package(module, file, old_yaml)
    repaired_yaml, changes = normalize_yaml_runtime_guards(old_yaml, app_package=app_package, evidence_text="")
    repaired_yaml = normalize_full_yaml_structure(repaired_yaml)
    yaml_check = validate_midscene_yaml(repaired_yaml)
    if not yaml_check["ok"]:
        raise ValueError("静态修复后的 YAML 基础检查未通过：" + "；".join(yaml_check["warnings"]))
    repair_dir = safe_join(LEARNING_DIR, "repairs", f"{generate_job_id()}_static_{clean_id(file, 'file')}")
    os.makedirs(repair_dir, exist_ok=True)
    write_text_file(safe_join(repair_dir, "before.yaml"), old_yaml)
    write_text_file(safe_join(repair_dir, "after.yaml"), repaired_yaml)
    business_check = validate_yaml_business_flow_preserved(old_yaml, repaired_yaml)
    before_version = None
    if repaired_yaml.strip() != old_yaml.strip():
        before_version = save_file_version(module, file, content=old_yaml, reason="before_static_repair")
        write_text_file(yaml_path, repaired_yaml)
    update_task_meta(module, file, {
        "last_repair_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_repair_status": "static_updated" if changes else "static_noop",
        "last_repair_changes": changes[:20]
    })
    next_job = create_pending_job(module, file, auto_optimize=False, run_mode="baseline") if create_next else None
    result = {
        "ok": True,
        "mode": "static",
        "analysis": "未找到失败执行记录，已执行静态规则体检：修复 YAML 语法、flowItem、前置启动、后置关闭、长等待和基线注释。",
        "changes": changes,
        "updated_file": f"{module}/{file}",
        "yamlCheck": yaml_check,
        "next_job": next_job
    }
    attach_repair_result_metadata(
        result,
        old_yaml,
        repaired_yaml,
        repair_dir=repair_dir,
        before_version=before_version,
        yaml_check=yaml_check,
        safety_warnings=[],
        business_check=business_check
    )
    write_json_file(safe_join(repair_dir, "repair.json"), {
        "module": module,
        "file": file,
        "mode": "static",
        "analysis": result["analysis"],
        "changes": changes,
        "yamlCheck": yaml_check,
        "safetyCheck": result["safetyCheck"],
        "businessFlowCheck": business_check,
        "changed_line_count": result["changed_line_count"],
        "diff_summary": result["diff_summary"],
        "before_version": before_version,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    return result



def static_repair_yaml_task(module, file, task_name, create_next=False):
    yaml_path = safe_join(TASK_DIR, module, file)
    with open(yaml_path, encoding="utf-8") as f:
        old_yaml = f.read()
    task_info = find_yaml_task_block(old_yaml, task_name)
    app_package = resolve_app_package(module, file, old_yaml)
    platform = detect_yaml_platform(old_yaml)
    repaired_block, changes = normalize_task_block_runtime_guards(task_info["block"], app_package=app_package, evidence_text="", platform=platform)
    new_yaml = normalize_full_yaml_structure(replace_yaml_task_block(old_yaml, task_info, repaired_block))
    yaml_check = validate_midscene_yaml(new_yaml)
    if not yaml_check["ok"]:
        raise ValueError("静态修复后的 YAML 基础检查未通过：" + "；".join(yaml_check["warnings"]))
    repair_dir = safe_join(LEARNING_DIR, "repairs", f"{generate_job_id()}_static_{clean_id(task_info['name'], 'task')}")
    os.makedirs(repair_dir, exist_ok=True)
    write_text_file(safe_join(repair_dir, "before.yaml"), old_yaml)
    write_text_file(safe_join(repair_dir, "before-task.yaml"), task_info["block"] + "\n")
    write_text_file(safe_join(repair_dir, "after-task.yaml"), repaired_block.strip("\n") + "\n")
    write_text_file(safe_join(repair_dir, "after.yaml"), new_yaml)
    business_check = business_alignment_report(task_info["block"], repaired_block)
    before_version = None
    if new_yaml.strip() != old_yaml.strip():
        before_version = save_file_version(module, file, content=old_yaml, reason="before_static_task_repair")
        write_text_file(yaml_path, new_yaml)
    update_task_meta(module, file, {
        "last_repair_task_name": task_info["name"],
        "last_repair_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_repair_status": "static_updated" if changes else "static_noop",
        "last_repair_changes": changes[:20]
    })
    next_job = create_pending_job(module, file, auto_optimize=False, run_mode="baseline", target_task_name=task_info["name"]) if create_next else None
    result = {
        "ok": True,
        "mode": "static",
        "taskName": task_info["name"],
        "analysis": "未找到该用例失败执行记录，已执行单条静态规则体检：修复 YAML 语法、flowItem、前置启动、后置关闭、长等待和基线注释。",
        "changes": changes,
        "updated_file": f"{module}/{file}",
        "yamlCheck": yaml_check,
        "next_job": next_job
    }
    attach_repair_result_metadata(
        result,
        old_yaml,
        new_yaml,
        repair_dir=repair_dir,
        before_version=before_version,
        yaml_check=yaml_check,
        safety_warnings=[],
        business_check=business_check
    )
    write_json_file(safe_join(repair_dir, "repair.json"), {
        "module": module,
        "file": file,
        "taskName": task_info["name"],
        "mode": "static",
        "analysis": result["analysis"],
        "changes": changes,
        "yamlCheck": yaml_check,
        "safetyCheck": result["safetyCheck"],
        "businessFlowCheck": business_check,
        "changed_line_count": result["changed_line_count"],
        "diff_summary": result["diff_summary"],
        "before_version": before_version,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    return result



def optimize_yaml_after_failure(job, stdout, stderr, summary):
    module = job.get("module", "")
    file = job.get("file", "")
    yaml_path = safe_join(TASK_DIR, module, file)
    with open(yaml_path, encoding="utf-8") as f:
        old_yaml = f.read()

    evidence_text = "\n".join([stdout or "", stderr or "", json.dumps(summary, ensure_ascii=False)[:3000] if summary is not None else ""])
    app_package = resolve_app_package(module, file, old_yaml)
    ctx = build_failure_context(job, old_yaml, stdout, stderr, summary)
    classification = classify_failure_by_context(ctx) or {}
    ctx["classification"] = classification
    guarded_yaml, guard_changes = repair_by_failure_type(old_yaml, ctx)
    execution_images = execution_screenshot_context(job)
    ai_repaired = None
    if classification.get("can_auto_repair") and classification.get("failure_type") in ("yaml_syntax", "scroll_not_effective", "wait_strategy", "input_failed", "popup_overlay"):
        repaired = {
            "analysis": classification.get("reason", "确定性规则修复"),
            "changes": guard_changes,
            "content": guarded_yaml
        }
    elif should_use_rule_only_repair(old_yaml, guard_changes, stdout, stderr, summary):
        repaired = {
            "analysis": "规则优先修复：脚本缺少运行时前置/后置、未进入 App、或尚未可靠跑到业务步骤，先只补齐启动/关闭/等待等确定性问题，不改业务步骤和断言。",
            "changes": guard_changes,
            "content": guarded_yaml
        }
    else:
        ai_repaired = call_dashscope_repair_yaml(module, file, guarded_yaml, stdout, stderr, summary, execution_images=execution_images)
        normalized_content, normalized_changes = normalize_yaml_runtime_guards(ai_repaired["content"], app_package=app_package, evidence_text=evidence_text)
        repaired = {
            "analysis": ai_repaired.get("analysis", ""),
            "changes": guard_changes + ai_repaired.get("changes", []) + normalized_changes,
            "content": normalized_content
        }
    repaired["content"] = normalize_full_yaml_structure(repaired["content"])
    safety_warnings = validate_repair_safety(old_yaml, repaired["content"], ctx)
    if safety_warnings:
        raise ValueError("修复后的 YAML 安全检查未通过：" + "；".join(safety_warnings[:8]))
    yaml_check = validate_midscene_yaml(repaired["content"])

    repair_dir = safe_join(LEARNING_DIR, "repairs", job.get("job_id", new_job_id()))
    os.makedirs(repair_dir, exist_ok=True)
    write_text_file(safe_join(repair_dir, "before.yaml"), old_yaml)
    write_text_file(safe_join(repair_dir, "after.yaml"), repaired["content"])
    business_check = validate_yaml_business_flow_preserved(old_yaml, repaired["content"])
    write_json_file(safe_join(repair_dir, "repair.json"), {
        "job": job,
        "analysis": repaired.get("analysis", ""),
        "changes": repaired.get("changes", []),
        "rule_changes": guard_changes,
        "ai_repaired": bool(ai_repaired),
        "used_knowledge_pages": repaired.get("used_knowledge_pages", []),
        "used_execution_screenshots": repaired.get("used_execution_screenshots", []),
        "yamlCheck": yaml_check,
        "classification": classification,
        "safetyCheck": {"ok": not safety_warnings, "warnings": safety_warnings},
        "businessFlowCheck": business_check,
        "changed_line_count": changed_line_count(old_yaml, repaired["content"]),
        "diff_summary": yaml_diff_summary(old_yaml, repaired["content"]),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })

    before_version = save_file_version(module, file, content=old_yaml, reason="before_ai_repair")
    write_text_file(yaml_path, repaired["content"])
    update_task_meta(module, file, {
        "last_repair_job_id": job.get("job_id", ""),
        "last_repair_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_repair_status": "updated",
        "last_repair_changes": repaired.get("changes", [])[:20]
    })
    attach_repair_result_metadata(
        repaired,
        old_yaml,
        repaired["content"],
        repair_dir=repair_dir,
        before_version=before_version,
        yaml_check=yaml_check,
        safety_warnings=safety_warnings,
        business_check=business_check
    )
    return repaired, repair_dir



def optimize_yaml_task_after_failure(job, task_name, stdout, stderr, summary):
    module = job.get("module", "")
    file = job.get("file", "")
    yaml_path = safe_join(TASK_DIR, module, file)
    with open(yaml_path, encoding="utf-8") as f:
        old_yaml = f.read()

    task_info = find_yaml_task_block(old_yaml, task_name)
    evidence_text = "\n".join([stdout or "", stderr or "", json.dumps(summary, ensure_ascii=False)[:3000] if summary is not None else ""])
    app_package = resolve_app_package(module, file, old_yaml)
    platform = detect_yaml_platform(old_yaml)
    ctx = build_failure_context(job, old_yaml, stdout, stderr, summary, task_name=task_info["name"])
    classification = classify_failure_by_context(ctx) or {}
    ctx["classification"] = classification
    guarded_yaml_for_ctx, whole_guard_changes = repair_by_failure_type(old_yaml, ctx)
    try:
        guarded_block = find_yaml_task_block(guarded_yaml_for_ctx, task_info["name"])["block"]
        guard_changes = whole_guard_changes
    except Exception:
        guarded_block, guard_changes = normalize_task_block_runtime_guards(task_info["block"], app_package=app_package, evidence_text=evidence_text, platform=platform)
    execution_images = execution_screenshot_context(job)
    ai_repaired = None
    if classification.get("can_auto_repair") and classification.get("failure_type") in ("yaml_syntax", "scroll_not_effective", "wait_strategy", "input_failed", "popup_overlay"):
        repaired = {
            "analysis": classification.get("reason", "确定性规则修复"),
            "changes": guard_changes,
            "content": guarded_block
        }
    elif should_use_rule_only_repair(task_info["block"], guard_changes, stdout, stderr, summary):
        repaired = {
            "analysis": "规则优先修复：该用例缺少运行时前置/后置、未进入 App、或尚未可靠跑到业务步骤，先只补齐启动/关闭/等待等确定性问题，不改业务步骤和断言。",
            "changes": guard_changes,
            "content": guarded_block
        }
    else:
        ai_repaired = call_dashscope_repair_yaml_task_patch(module, file, task_info["name"], old_yaml, guarded_block, stdout, stderr, summary, execution_images=execution_images)
        patched_block, applied_patches = apply_task_repair_patches(guarded_block, ai_repaired.get("patches") or [])
        normalized_block, normalized_changes = normalize_task_block_runtime_guards(patched_block, app_package=app_package, evidence_text=evidence_text, platform=platform)
        repaired = {
            "analysis": ai_repaired.get("analysis", ""),
            "changes": guard_changes + ai_repaired.get("changes", []) + [f"应用补丁：{item.get('op')} {item.get('anchor')}" for item in applied_patches] + normalized_changes,
            "content": normalized_block,
            "patches": applied_patches
        }
    new_yaml = normalize_full_yaml_structure(replace_yaml_task_block(old_yaml, task_info, repaired["content"]))
    safety_warnings = validate_repair_safety(old_yaml, new_yaml, ctx, task_name=task_info["name"])
    if safety_warnings:
        raise ValueError("修复后的 YAML 安全检查未通过：" + "；".join(safety_warnings[:8]))
    yaml_check = validate_midscene_yaml(new_yaml)

    repair_dir = safe_join(LEARNING_DIR, "repairs", f"{job.get('job_id', new_job_id())}_{clean_id(task_info['name'], 'task')}")
    os.makedirs(repair_dir, exist_ok=True)
    write_text_file(safe_join(repair_dir, "before.yaml"), old_yaml)
    write_text_file(safe_join(repair_dir, "before-task.yaml"), task_info["block"] + "\n")
    write_text_file(safe_join(repair_dir, "after-task.yaml"), repaired["content"].strip("\n") + "\n")
    write_text_file(safe_join(repair_dir, "after.yaml"), new_yaml)
    business_check = business_alignment_report(task_info["block"], repaired["content"])
    write_json_file(safe_join(repair_dir, "repair.json"), {
        "job": job,
        "taskName": task_info["name"],
        "analysis": repaired.get("analysis", ""),
        "changes": repaired.get("changes", []),
        "patches": repaired.get("patches", []),
        "rule_changes": guard_changes,
        "ai_repaired": bool(ai_repaired),
        "used_knowledge_pages": repaired.get("used_knowledge_pages", []),
        "used_execution_screenshots": repaired.get("used_execution_screenshots", []),
        "yamlCheck": yaml_check,
        "classification": classification,
        "safetyCheck": {"ok": not safety_warnings, "warnings": safety_warnings},
        "businessFlowCheck": business_check,
        "changed_line_count": changed_line_count(old_yaml, new_yaml),
        "diff_summary": yaml_diff_summary(old_yaml, new_yaml),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })

    before_version = save_file_version(module, file, content=old_yaml, reason="before_ai_task_repair")
    write_text_file(yaml_path, new_yaml)
    update_task_meta(module, file, {
        "last_repair_job_id": job.get("job_id", ""),
        "last_repair_task_name": task_info["name"],
        "last_repair_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_repair_status": "updated",
        "last_repair_changes": repaired.get("changes", [])[:20]
    })
    attach_repair_result_metadata(
        repaired,
        old_yaml,
        new_yaml,
        repair_dir=repair_dir,
        before_version=before_version,
        yaml_check=yaml_check,
        safety_warnings=safety_warnings,
        business_check=business_check
    )
    return repaired, repair_dir



def repair_draft_by_id(draft_id):
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return None
    for draft in load_repair_drafts():
        if draft.get("draftId") == draft_id or draft.get("draft_id") == draft_id:
            return draft
    return None



def validate_midscene_yaml_executability_text(yaml_text, stats=None):
    stats = stats or {
        "task_count": 0,
        "tasks_without_assertion": 0,
        "ambiguous_prompt_count": 0,
        "long_sleep_count": 0,
        "missing_wait_after_action_count": 0,
    }
    suggestions = []
    text = yaml_text or ""
    task_blocks = []
    current = []
    for line in text.splitlines():
        if re.match(r"^\s{2}-\s+name\s*:", line):
            if current:
                task_blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        task_blocks.append("\n".join(current))
    for task_idx, block in enumerate(task_blocks, 1):
        name = f"第 {task_idx} 条 task"
        name_m = re.search(r"^\s{2}-\s+name\s*:\s*(.+?)\s*$", block, flags=re.M)
        if name_m:
            name = strip_yaml_quotes(name_m.group(1)) or name
        stats["task_count"] += 1
        has_launch = re.search(r"^\s*-\s+launch\s*:", block, flags=re.M) is not None
        has_cleanup = re.search(r"^\s*-\s+terminate\s*:", block, flags=re.M) is not None or (
            re.search(r"^\s*-\s+runAdbShell\s*:\s*.*force-stop", block, flags=re.M) is not None
        )
        has_assertion = re.search(r"^\s*-\s+(aiAssert|aiWaitFor)\s*:", block, flags=re.M) is not None
        last_interactive_line = 0
        wait_after_interactive = False
        long_sleeps = 0
        for line_no, line in enumerate(block.splitlines(), 1):
            m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", line)
            if not m:
                continue
            action, raw_value = m.groups()
            value = strip_yaml_quotes(raw_value)
            if action == "sleep":
                try:
                    sleep_ms = float(value)
                except Exception:
                    sleep_ms = 0
                if sleep_ms >= 5000:
                    long_sleeps += 1
                    stats["long_sleep_count"] += 1
                    suggestions.append(f"{name}：第 {line_no} 步 sleep {int(sleep_ms)}ms 偏长，建议改为 aiWaitFor 等待真实 UI 信号")
            if action in ("aiTap", "aiInput", "aiAssert", "aiWaitFor", "ai"):
                if prompt_is_too_ambiguous(value):
                    stats["ambiguous_prompt_count"] += 1
                    suggestions.append(f"{name}：第 {line_no} 步 {action} 提示词「{value}」过泛，建议补页面/弹窗/区域上下文")
            if action in ("aiTap", "aiInput", "aiKeyboardPress", "aiScroll"):
                last_interactive_line = line_no
                wait_after_interactive = False
            if last_interactive_line and line_no > last_interactive_line and action in ("aiWaitFor", "aiAssert"):
                wait_after_interactive = True
        if not has_assertion:
            stats["tasks_without_assertion"] += 1
            suggestions.append(f"{name}：缺少 aiAssert 或业务目标型 aiWaitFor，执行报告难以判断是否真正通过")
        if detect_yaml_platform(text) == "android" and not has_launch:
            suggestions.append(f"{name}：缺少 launch，建议从稳定 App 起点独立执行")
        if detect_yaml_platform(text) == "android" and not has_cleanup:
            suggestions.append(f"{name}：缺少收尾关闭 App，可能影响下一条用例状态")
        if last_interactive_line and not wait_after_interactive:
            stats["missing_wait_after_action_count"] += 1
            suggestions.append(f"{name}：最后一次交互后缺少可见等待/断言，建议补结果页、按钮、列表或空态检查")
        if long_sleeps >= 3:
            suggestions.append(f"{name}：存在多段固定等待，建议合并为更明确的 aiWaitFor 目标以提升速度")
    suggestions = dedupe_keep_order(suggestions)[:20]
    penalty = (
        stats["tasks_without_assertion"] * 18
        + stats["ambiguous_prompt_count"] * 6
        + stats["long_sleep_count"] * 4
        + stats["missing_wait_after_action_count"] * 8
    )
    score = max(0, min(100, 100 - penalty))
    level = "good" if score >= 80 else "needs_review" if score >= 55 else "risky"
    return {
        "ok": score >= 55,
        "level": level,
        "score": score,
        "suggestions": suggestions,
        "stats": stats,
        "mode": "text"
    }



def prompt_is_too_ambiguous(text):
    text = strip_yaml_quotes(text or "").strip()
    if not text:
        return True
    compact = re.sub(r"\s+", "", text)
    if compact in GENERIC_PROMPT_TEXTS:
        return True
    if len(compact) <= 3 and not any(word in text for word in SHORT_PROMPT_CONTEXT_WORDS):
        return True
    if any(pattern in text for pattern in ("结果符合预期", "页面正常", "功能正常", "操作成功")):
        return True
    return False



def parsed_flow_item_action(item):
    if not isinstance(item, dict):
        return None, []
    action_keys = [key for key in item.keys() if key in SUPPORTED_FLOW_ITEMS]
    return (action_keys[0] if action_keys else None), action_keys
