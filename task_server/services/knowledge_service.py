"""Knowledge base service.

Migrated from ``midscene-upload.py``: handle the per-app page knowledge
library that backs ``/api/knowledge/*`` endpoints.

A knowledge "app" maps to a directory under ``KNOWLEDGE_DIR`` keyed by
``app_package``.  Each page lives in its own subdirectory containing a
``meta.json`` and (optionally) a screenshot image.

This module keeps the original on-disk layout and behavior intact so the
legacy server can continue to read/write the same files.

Extended with deep knowledge integration:
- Failure pattern knowledge base
- Case execution history & repair history
- Enhanced page knowledge queries for Agent
- Knowledge statistics
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import html as html_lib

from ..config import (
    ASSET_DIR,
    BASELINE_REFS_FILE,
    CASE_HISTORY_FILE,
    DEFAULT_APP_PACKAGE,
    DEFAULT_DASHSCOPE_BASE_URL,
    DEFAULT_FIGMA_API_BASE,
    DEFAULT_VL_MODEL,
    FAILURE_PATTERNS_FILE,
    FALLBACK_DASHSCOPE_API_KEY,
    FIGMA_IMAGE_EXPORT,
    FIGMA_MAX_REFERENCE_LIMIT,
    FIGMA_PARENT_LOOKUP,
    FIGMA_PARSE_LIMIT,
    FIGMA_REFERENCE_LIMIT,
    FIGMA_RETRY_COUNT,
    FIGMA_TIMEOUT_SECONDS,
    FIGMA_VISUAL_IMAGE_LIMIT,
    KNOWLEDGE_DIR,
    KNOWLEDGE_DATA_DIR,
    LEARNING_DIR,
    REPAIR_HISTORY_FILE,
    env_int,
    safe_int,
)
from ..storage import (
    clean_asset_filename,
    clean_filename,
    clean_id,
    read_json_file,
    safe_join,
    unique_millis_id,
    write_bytes_file,
    write_json_file,
    write_text_file,
)


# ---------------------------------------------------------------------------
# Small helpers (kept local — migrated verbatim from midscene-upload.py)
# ---------------------------------------------------------------------------

def _is_image_file(filename: str) -> bool:
    return str(filename or "").lower().endswith((".png", ".jpg", ".jpeg"))


def _guess_mime(filename: str) -> str:
    lower = str(filename or "").lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    return "application/octet-stream"


def _write_bytes_file(path: str, data: bytes) -> None:
    """Atomically write bytes to *path* (mirrors midscene-upload.write_bytes_file)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(
        directory,
        f".{os.path.basename(path)}.tmp.{os.getpid()}.{threading.get_ident()}",
    )
    try:
        with open(tmp, "wb") as f:
            f.write(data or b"")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def _normalize_lines(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]
    return []


def is_image_file(filename: str) -> bool:
    return _is_image_file(filename)


def guess_mime(filename: str) -> str:
    return _guess_mime(filename)


def normalize_lines(value: Any) -> List[str]:
    return _normalize_lines(value)


def is_text_asset(filename: str) -> bool:
    return str(filename or "").lower().endswith((".txt", ".md", ".json", ".pdf", ".doc", ".docx", ".mm"))


def supported_asset_file(filename: str) -> bool:
    return is_text_asset(filename) or is_image_file(filename)


def extract_asset_text(path: str, name: str) -> str:
    from .yaml_service import extract_asset_text as _extract_asset_text
    return _extract_asset_text(path, name)


def _normalize_tier(value: Any, default: str = "test") -> str:
    tier = str(value or default or "test").strip().lower()
    mapping = {
        "baseline": "baseline",
        "base": "baseline",
        "stable": "baseline",
        "基线": "baseline",
        "基线库": "baseline",
        "test": "test",
        "testing": "test",
        "draft": "test",
        "测试": "test",
        "测试库": "test",
        "草稿": "test",
    }
    return mapping.get(tier, "test")


def _normalize_model_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def _dashscope_api_key(required: bool = True) -> str:
    value = (
        os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("MIDSCENE_API_KEY")
        or FALLBACK_DASHSCOPE_API_KEY
        or ""
    ).strip().strip("\"'")
    if required and not value:
        raise ValueError("未配置 DASHSCOPE_API_KEY/OPENAI_API_KEY/MIDSCENE_API_KEY")
    return value


def _dashscope_base_url() -> str:
    return (
        os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("MIDSCENE_BASE_URL")
        or DEFAULT_DASHSCOPE_BASE_URL
    ).rstrip("/")


def _dashscope_vl_model() -> str:
    return (
        os.getenv("DASHSCOPE_VL_MODEL")
        or os.getenv("MIDSCENE_MODEL_NAME")
        or DEFAULT_VL_MODEL
    ).strip()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _knowledge_app_dir(app_package: str) -> str:
    return safe_join(KNOWLEDGE_DIR, clean_id(app_package, DEFAULT_APP_PACKAGE))


def _knowledge_page_dir(app_package: str, page_id: str) -> str:
    return safe_join(
        _knowledge_app_dir(app_package),
        clean_id(page_id, "page"),
    )


def _knowledge_meta_path(app_package: str, page_id: str) -> str:
    return safe_join(_knowledge_page_dir(app_package, page_id), "meta.json")


def _resolve_app_package(app_id: Any) -> str:
    return (
        str(app_id).strip()
        if app_id
        else os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    )


# ---------------------------------------------------------------------------
# App-level operations
# ---------------------------------------------------------------------------

def list_knowledge_apps() -> List[str]:
    """列出知识库中的应用 (returns sorted list of app_package directory names)."""
    if not os.path.exists(KNOWLEDGE_DIR):
        return []
    return sorted([
        name for name in os.listdir(KNOWLEDGE_DIR)
        if os.path.isdir(safe_join(KNOWLEDGE_DIR, name))
    ])


def get_knowledge_app(app_id: str) -> Dict[str, Any]:
    """获取单个应用详情。

    Returns aggregate page counts plus the page list. Raises ValueError if
    *app_id* is empty.
    """
    if not app_id:
        raise ValueError("app_package 不能为空")
    app_package = _resolve_app_package(app_id)
    pages = list_knowledge_pages(app_package)
    return {
        "package": app_package,
        "page_count": len(pages),
        "test_count": len([p for p in pages if p.get("tier") != "baseline"]),
        "baseline_count": len([p for p in pages if p.get("tier") == "baseline"]),
        "has_knowledge": bool(pages),
        "pages": pages,
    }


def delete_knowledge_app(app_id: str) -> bool:
    """删除知识库应用 (recursively removes the app directory).

    Returns True when the directory existed and was removed, False otherwise.
    """
    if not app_id:
        raise ValueError("app_package 不能为空")
    app_dir = _knowledge_app_dir(_resolve_app_package(app_id))
    if not os.path.exists(app_dir):
        return False
    shutil.rmtree(app_dir)
    return True


# ---------------------------------------------------------------------------
# Page-level operations
# ---------------------------------------------------------------------------

def list_knowledge_pages(app_id: str, tier: str = "all") -> List[Dict[str, Any]]:
    """列出应用下的页面。

    *tier* may be ``"all"`` (default), ``"baseline"`` or ``"test"``.
    """
    app_package = _resolve_app_package(app_id)
    app_dir = _knowledge_app_dir(app_package)
    if not os.path.exists(app_dir):
        return []
    selected_tier = _normalize_tier(tier, "") if tier and tier != "all" else "all"
    pages: List[Dict[str, Any]] = []
    for page_id in sorted(os.listdir(app_dir)):
        meta = read_json_file(_knowledge_meta_path(app_package, page_id), default=None)
        if not meta:
            continue
        meta["tier"] = _normalize_tier(meta.get("tier"), "test")
        if selected_tier != "all" and meta["tier"] != selected_tier:
            continue
        pages.append(meta)
    return pages


def save_knowledge_page(app_id: str, page_data: Dict[str, Any]) -> Dict[str, Any]:
    """保存知识库页面。

    *page_data* is the request body originally accepted by
    ``POST /api/knowledge/page``.  The optional ``app_id`` argument overrides
    ``page_data['app_package']`` when supplied.
    """
    data = dict(page_data or {})
    app_package = (
        _resolve_app_package(app_id)
        if app_id
        else (
            data.get("app_package")
            or data.get("appPackage")
            or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
        )
    )
    page_name = (data.get("page_name") or data.get("pageName") or "未命名页面").strip()
    page_id = clean_id(data.get("page_id") or data.get("pageId") or page_name)
    page_dir = _knowledge_page_dir(app_package, page_id)
    os.makedirs(page_dir, exist_ok=True)

    screenshot = data.get("screenshot") or {}
    screenshot_name = ""
    if screenshot.get("contentBase64"):
        raw_name = clean_asset_filename(screenshot.get("name") or f"{page_id}.png")
        if not _is_image_file(raw_name):
            raise ValueError("页面截图只支持 png / jpg / jpeg")
        screenshot_name = raw_name
        _write_bytes_file(
            safe_join(page_dir, screenshot_name),
            base64.b64decode(screenshot["contentBase64"]),
        )

    existed = read_json_file(_knowledge_meta_path(app_package, page_id), default={}) or {}
    if not screenshot_name:
        screenshot_name = existed.get("screenshot", "")

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    meta = {
        "app_package": app_package,
        "page_id": page_id,
        "page_name": page_name,
        "route": data.get("route", ""),
        "description": data.get("description", ""),
        "key_elements": _normalize_lines(data.get("key_elements") or data.get("keyElements")),
        "common_assertions": _normalize_lines(
            data.get("common_assertions") or data.get("commonAssertions")
        ),
        "tags": _normalize_lines(data.get("tags")),
        "tier": _normalize_tier(
            data.get("tier") or data.get("library") or existed.get("tier"),
            "test",
        ),
        "screenshot": screenshot_name,
        "source": data.get("source") or ("figma" if data.get("figma") else existed.get("source", "manual")),
        "figma": data.get("figma") or existed.get("figma") or {},
        "updated_at": now,
        "created_at": existed.get("created_at") or now,
    }
    write_json_file(_knowledge_meta_path(app_package, page_id), meta)
    return meta


def delete_knowledge_page(app_id: str, page_id: str) -> bool:
    """删除知识库页面 (recursively removes the page directory).

    Returns True when the directory existed and was removed, False otherwise.
    """
    if not page_id:
        raise ValueError("page_id 不能为空")
    page_dir = _knowledge_page_dir(_resolve_app_package(app_id), page_id)
    if not os.path.exists(page_dir):
        return False
    shutil.rmtree(page_dir)
    return True


def get_knowledge_screenshot(app_id: str, page_id: str) -> Optional[Dict[str, str]]:
    """获取页面截图路径。

    Returns ``{"path": <absolute>, "mime": <content-type>, "name": <file>}``
    when a screenshot exists, otherwise ``None``.
    """
    if not page_id:
        raise ValueError("page_id 不能为空")
    app_package = _resolve_app_package(app_id)
    meta = read_json_file(_knowledge_meta_path(app_package, page_id), default=None)
    if not meta or not meta.get("screenshot"):
        return None
    image_path = safe_join(_knowledge_page_dir(app_package, page_id), meta["screenshot"])
    if not os.path.exists(image_path):
        return None
    return {
        "path": image_path,
        "mime": _guess_mime(meta["screenshot"]),
        "name": meta["screenshot"],
    }


def analyze_knowledge_screenshot(
    app_id: str,
    page_id: str = "",
    hint: str = "",
    screenshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """分析知识库页面截图（调用 AI）。

    Two call shapes are supported:

    1. ``analyze_knowledge_screenshot(app_id, page_id)`` — load the existing
       page screenshot from disk and analyze it.
    2. ``analyze_knowledge_screenshot(app_id, screenshot={...})`` — analyze
       a freshly uploaded screenshot payload (the legacy ``/api/knowledge/
       analyze`` request body).
    """
    api_key = _dashscope_api_key()
    app_package = _resolve_app_package(app_id)

    page_meta: Dict[str, Any] = {}
    image_b64 = ""
    image_name = ""

    if screenshot and screenshot.get("contentBase64"):
        image_name = clean_asset_filename(screenshot.get("name") or "page.png")
        if not _is_image_file(image_name):
            raise ValueError("页面截图只支持 png / jpg / jpeg")
        image_b64 = screenshot["contentBase64"]
    elif page_id:
        page_meta = read_json_file(
            _knowledge_meta_path(app_package, page_id), default={}
        ) or {}
        if not page_meta.get("screenshot"):
            raise ValueError("请先上传页面截图")
        image_name = page_meta["screenshot"]
        image_path = safe_join(
            _knowledge_page_dir(app_package, page_id), image_name
        )
        if not os.path.exists(image_path):
            raise ValueError("页面截图不存在")
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("ascii")
    else:
        raise ValueError("请提供 page_id 或 screenshot 参数")

    existing_page_name = page_meta.get("page_name") or ""
    prompt = f"""
你是移动 App UI 自动化测试知识库维护助手。
请根据截图识别这个页面，生成可维护的页面知识草稿。

要求：
1. 只输出合法 JSON，不要 Markdown，不要解释。
2. 不要编造截图里看不到的按钮、入口、Tab 或文案。
3. key_elements 用真实可见文案或稳定入口描述，适合给 Midscene 的 aiTap/aiAction 使用。
4. common_assertions 必须是页面上可以视觉验证的内容。
5. route 如果截图无法判断，可给出空字符串或"待补充"。
6. page_name 尽量用页面标题、Tab 名、核心业务名。

APP 包名：{app_package}
人工提示：{hint}
已有页面名称：{existing_page_name}

输出格式：
{{
  "page_name": "我的页",
  "route": "点击底部 Tab「我的」",
  "description": "用户个人中心页面，包含我的收藏、打印记录等入口。",
  "key_elements": ["底部 Tab「我的」", "入口「我的收藏」", "入口「打印记录」"],
  "common_assertions": ["页面展示「我的收藏」入口", "页面展示「打印记录」入口"],
  "tags": ["我的", "个人中心"]
}}
"""

    body = json.dumps({
        "model": _dashscope_vl_model(),
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{_guess_mime(image_name)};base64,{image_b64}",
                        },
                    },
                ],
            },
        ],
        "temperature": 0.1,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{_dashscope_base_url()}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        resp_data = json.loads(resp.read().decode("utf-8"))

    draft = _normalize_model_json(resp_data["choices"][0]["message"]["content"])
    return {
        "page_name": draft.get("page_name") or draft.get("pageName") or existing_page_name or "未命名页面",
        "route": draft.get("route") or "",
        "description": draft.get("description") or "",
        "key_elements": _normalize_lines(draft.get("key_elements") or draft.get("keyElements")),
        "common_assertions": _normalize_lines(
            draft.get("common_assertions") or draft.get("commonAssertions")
        ),
        "tags": _normalize_lines(draft.get("tags")),
    }


# ===================================================================
# ========== 失败知识库 ==========
# ===================================================================

def _load_failure_patterns() -> Dict[str, Any]:
    """加载失败模式库，确保目录和文件存在。"""
    os.makedirs(KNOWLEDGE_DATA_DIR, exist_ok=True)
    data = read_json_file(FAILURE_PATTERNS_FILE, default=None)
    if not isinstance(data, dict) or "patterns" not in data:
        data = {"patterns": []}
    return data


def _save_failure_patterns(data: Dict[str, Any]) -> None:
    """保存失败模式库。"""
    os.makedirs(KNOWLEDGE_DATA_DIR, exist_ok=True)
    write_json_file(FAILURE_PATTERNS_FILE, data)


def record_failure_knowledge(
    job_id: str,
    log_text: str,
    failure_type: str,
    root_cause: str,
    repair_method: Optional[str] = None,
) -> Dict[str, Any]:
    """执行失败时自动记录失败知识。

    存储到 ``FAILURE_PATTERNS_FILE``。
    结构: ``{patterns: [{id, logSignature, failureType, rootCause, repairMethod, hitCount, successCount, createdAt, updatedAt}]}``

    Args:
        job_id: 关联的 job ID。
        log_text: 失败时的日志文本。
        failure_type: 故障类型（如 SCRIPT_ISSUE / PRODUCT_BUG）。
        root_cause: 根因分析摘要。
        repair_method: 可选的修复方法描述。

    Returns:
        新创建的失败模式记录。
    """
    log_sig = hashlib.md5((log_text or "").encode()).hexdigest()[:12]
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    data = _load_failure_patterns()

    # 查重：相同 logSignature 视为同一模式，更新统计
    for pattern in data["patterns"]:
        if pattern.get("logSignature") == log_sig:
            pattern["hitCount"] = pattern.get("hitCount", 0) + 1
            pattern["updatedAt"] = now
            if repair_method:
                pattern["repairMethod"] = repair_method
            _save_failure_patterns(data)
            return pattern

    new_pattern: Dict[str, Any] = {
        "id": f"fp-{int(time.time() * 1000)}-{os.getpid()}",
        "jobId": str(job_id or ""),
        "logSignature": log_sig,
        "logKeywords": _extract_keywords(log_text),
        "failureType": str(failure_type or "UNKNOWN").upper(),
        "rootCause": str(root_cause or ""),
        "repairMethod": str(repair_method or ""),
        "hitCount": 1,
        "successCount": 0,
        "createdAt": now,
        "updatedAt": now,
    }
    data["patterns"].append(new_pattern)
    # 保留最近 500 条
    if len(data["patterns"]) > 500:
        data["patterns"] = data["patterns"][-500:]
    _save_failure_patterns(data)
    return new_pattern


def _extract_keywords(text: str, max_words: int = 10) -> List[str]:
    """从日志文本中提取关键词（简单实现：提取错误相关片段）。"""
    if not text:
        return []
    # 提取包含错误关键词的行片段
    error_indicators = [
        "error", "fail", "timeout", "exception", "assert",
        "错误", "失败", "超时", "异常",
    ]
    keywords: List[str] = []
    for line in text.splitlines():
        line_lower = line.strip().lower()
        if any(ind in line_lower for ind in error_indicators):
            # 截取前80字符作为关键词片段
            snippet = line.strip()[:80]
            if snippet and snippet not in keywords:
                keywords.append(snippet)
            if len(keywords) >= max_words:
                break
    return keywords


def match_failure_pattern(log_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """根据日志文本匹配历史失败模式。

    使用关键词相似度匹配，返回最相似的 top_k 条记录。

    Args:
        log_text: 当前失败的日志文本。
        top_k: 返回最多条数，默认 3。

    Returns:
        匹配结果列表 ``[{pattern, similarity, failureType, suggestedAction}]``。
    """
    if not log_text:
        return []

    data = _load_failure_patterns()
    patterns = data.get("patterns", [])
    if not patterns:
        return []

    current_keywords = set(_extract_keywords(log_text, max_words=15))
    log_lower = log_text.lower()

    scored: List[Dict[str, Any]] = []
    for pattern in patterns:
        # 关键词匹配得分
        pattern_keywords = set(pattern.get("logKeywords", []))
        keyword_overlap = len(current_keywords & pattern_keywords) if current_keywords and pattern_keywords else 0

        # 故障类型和根因在日志中的出现得分
        type_score = 0
        root_cause = pattern.get("rootCause", "")
        if root_cause and root_cause.lower() in log_lower:
            type_score = 0.3

        failure_type = pattern.get("failureType", "")
        if failure_type and failure_type.lower() in log_lower:
            type_score += 0.2

        # 综合相似度
        max_kw = max(len(current_keywords), 1)
        similarity = min(1.0, keyword_overlap / max_kw + type_score)

        if similarity > 0.01:
            suggested_action = pattern.get("repairMethod", "")
            if pattern.get("failureType") == "PRODUCT_BUG":
                suggested_action = suggested_action or "建议提交缺陷工单"
            elif pattern.get("failureType") == "ENV_ISSUE":
                suggested_action = suggested_action or "建议检查环境配置"
            elif pattern.get("failureType") == "SCRIPT_ISSUE":
                suggested_action = suggested_action or "建议修复 YAML 脚本"

            scored.append({
                "pattern": pattern,
                "similarity": round(similarity, 3),
                "failureType": pattern.get("failureType", "UNKNOWN"),
                "suggestedAction": suggested_action,
            })

    # 按相似度降序
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


def update_failure_pattern_stats(pattern_id: str, hit: bool = True, success: bool = True) -> Optional[Dict[str, Any]]:
    """更新失败模式的命中和成功统计。

    Args:
        pattern_id: 失败模式 ID。
        hit: 是否增加命中计数。
        success: 是否增加成功计数（修复成功）。

    Returns:
        更新后的模式记录，未找到返回 ``None``。
    """
    data = _load_failure_patterns()
    for pattern in data["patterns"]:
        if pattern.get("id") == pattern_id:
            if hit:
                pattern["hitCount"] = pattern.get("hitCount", 0) + 1
            if success:
                pattern["successCount"] = pattern.get("successCount", 0) + 1
            pattern["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_failure_patterns(data)
            return pattern
    return None


# ===================================================================
# ========== 用例知识库 ==========
# ===================================================================

def _load_case_history() -> Dict[str, Any]:
    """加载用例执行历史，确保目录和文件存在。"""
    os.makedirs(KNOWLEDGE_DATA_DIR, exist_ok=True)
    data = read_json_file(CASE_HISTORY_FILE, default=None)
    if not isinstance(data, dict) or "cases" not in data:
        data = {"cases": []}
    return data


def _save_case_history(data: Dict[str, Any]) -> None:
    """保存用例执行历史。"""
    os.makedirs(KNOWLEDGE_DATA_DIR, exist_ok=True)
    write_json_file(CASE_HISTORY_FILE, data)


def _load_repair_history() -> Dict[str, Any]:
    """加载修复历史，确保目录和文件存在。"""
    os.makedirs(KNOWLEDGE_DATA_DIR, exist_ok=True)
    data = read_json_file(REPAIR_HISTORY_FILE, default=None)
    if not isinstance(data, dict) or "repairs" not in data:
        data = {"repairs": []}
    return data


def _save_repair_history(data: Dict[str, Any]) -> None:
    """保存修复历史。"""
    os.makedirs(KNOWLEDGE_DATA_DIR, exist_ok=True)
    write_json_file(REPAIR_HISTORY_FILE, data)


def record_execution_result(
    yaml_file: str,
    module: str,
    job_id: str,
    status: str,
    duration_ms: int = 0,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """执行完成后自动记录用例执行历史。

    存储到 ``CASE_HISTORY_FILE``。
    结构: ``{cases: [{yamlFile, module, executions: [{jobId, status, durationMs, error, timestamp}]}]}``

    Args:
        yaml_file: YAML 文件名。
        module: 所属模块。
        job_id: Job ID。
        status: 执行状态（success / failed）。
        duration_ms: 执行耗时（毫秒）。
        error: 可选的错误信息。

    Returns:
        新增的执行记录。
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    execution: Dict[str, Any] = {
        "jobId": str(job_id or ""),
        "status": str(status or "unknown").lower(),
        "durationMs": int(duration_ms or 0),
        "error": str(error or "")[:500] if error else "",
        "timestamp": now,
    }

    data = _load_case_history()

    # 查找是否已有该 YAML 的记录
    for case in data["cases"]:
        if case.get("yamlFile") == yaml_file and case.get("module") == module:
            executions = case.get("executions", [])
            executions.append(execution)
            # 保留最近 50 条执行记录
            if len(executions) > 50:
                case["executions"] = executions[-50:]
            else:
                case["executions"] = executions
            _save_case_history(data)
            return execution

    # 新增用例记录
    new_case: Dict[str, Any] = {
        "yamlFile": str(yaml_file or ""),
        "module": str(module or ""),
        "executions": [execution],
    }
    data["cases"].append(new_case)
    # 保留最近 500 个用例
    if len(data["cases"]) > 500:
        data["cases"] = data["cases"][-500:]
    _save_case_history(data)
    return execution


def record_repair_history(
    yaml_file: str,
    module: str,
    old_yaml_hash: str,
    new_yaml_hash: str,
    repair_reason: str,
    success: bool,
) -> Dict[str, Any]:
    """修复应用后自动记录修复历史。

    存储到 ``REPAIR_HISTORY_FILE``。

    Args:
        yaml_file: YAML 文件名。
        module: 所属模块。
        old_yaml_hash: 修复前的 YAML 哈希。
        new_yaml_hash: 修复后的 YAML 哈希。
        repair_reason: 修复原因。
        success: 修复是否成功。

    Returns:
        新增的修复记录。
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    record: Dict[str, Any] = {
        "yamlFile": str(yaml_file or ""),
        "module": str(module or ""),
        "oldYamlHash": str(old_yaml_hash or ""),
        "newYamlHash": str(new_yaml_hash or ""),
        "repairReason": str(repair_reason or "")[:500],
        "success": bool(success),
        "timestamp": now,
    }

    data = _load_repair_history()
    data["repairs"].append(record)
    # 保留最近 500 条
    if len(data["repairs"]) > 500:
        data["repairs"] = data["repairs"][-500:]
    _save_repair_history(data)
    return record


def get_case_history(yaml_file: str, limit: int = 10) -> Dict[str, Any]:
    """查询 YAML 文件的执行历史和修复记录。

    Args:
        yaml_file: YAML 文件名。
        limit: 最近执行记录返回条数，默认 10。

    Returns:
        ``{yamlFile, totalExecutions, successRate, recentResults: [], repairHistory: []}``
    """
    if not yaml_file:
        return {
            "yamlFile": "",
            "totalExecutions": 0,
            "successRate": 0.0,
            "recentResults": [],
            "repairHistory": [],
        }

    case_data = _load_case_history()
    case_record = None
    for case in case_data.get("cases", []):
        if case.get("yamlFile") == yaml_file:
            case_record = case
            break

    if not case_record:
        return {
            "yamlFile": yaml_file,
            "totalExecutions": 0,
            "successRate": 0.0,
            "recentResults": [],
            "repairHistory": [],
        }

    executions = case_record.get("executions", [])
    total = len(executions)
    success_count = len([e for e in executions if e.get("status") == "success"])
    success_rate = round(success_count / total, 3) if total > 0 else 0.0

    # 查询修复历史
    repair_data = _load_repair_history()
    repair_history = [
        r for r in repair_data.get("repairs", [])
        if r.get("yamlFile") == yaml_file
    ]

    return {
        "yamlFile": yaml_file,
        "module": case_record.get("module", ""),
        "totalExecutions": total,
        "successRate": success_rate,
        "recentResults": executions[-limit:],
        "repairHistory": repair_history[-limit:],
    }


def get_case_stats(module: Optional[str] = None) -> Dict[str, Any]:
    """获取用例统计：按模块的成功率、失败率、最常失败用例。

    Args:
        module: 可选，按模块过滤统计。

    Returns:
        ``{moduleStats: [{module, total, success, failed, successRate}], topFailedCases: [{yamlFile, module, failCount}]}``
    """
    case_data = _load_case_history()
    cases = case_data.get("cases", [])

    if module:
        cases = [c for c in cases if c.get("module") == module]

    # 按模块统计
    module_map: Dict[str, Dict[str, int]] = {}
    for case in cases:
        mod = case.get("module") or "unknown"
        if mod not in module_map:
            module_map[mod] = {"total": 0, "success": 0, "failed": 0}
        executions = case.get("executions", [])
        for exe in executions:
            module_map[mod]["total"] += 1
            if exe.get("status") == "success":
                module_map[mod]["success"] += 1
            else:
                module_map[mod]["failed"] += 1

    module_stats = []
    for mod_name, counts in module_map.items():
        rate = round(counts["success"] / counts["total"], 3) if counts["total"] > 0 else 0.0
        module_stats.append({
            "module": mod_name,
            "total": counts["total"],
            "success": counts["success"],
            "failed": counts["failed"],
            "successRate": rate,
        })
    module_stats.sort(key=lambda x: x["failed"], reverse=True)

    # 最常失败用例 Top 10
    fail_counts: List[Dict[str, Any]] = []
    for case in cases:
        executions = case.get("executions", [])
        fail_count = len([e for e in executions if e.get("status") != "success"])
        if fail_count > 0:
            fail_counts.append({
                "yamlFile": case.get("yamlFile", ""),
                "module": case.get("module", ""),
                "failCount": fail_count,
            })
    fail_counts.sort(key=lambda x: x["failCount"], reverse=True)

    return {
        "moduleStats": module_stats,
        "topFailedCases": fail_counts[:10],
    }


# ===================================================================
# ========== 页面知识查询（增强） ==========
# ===================================================================

def query_page_elements(app_id: str, page_name: str) -> Dict[str, Any]:
    """Agent 生成 YAML 时查询页面元素。

    Args:
        app_id: 应用包名。
        page_name: 页面名称（模糊匹配）。

    Returns:
        ``{pageName, elements: [{name, type, description}], waitConditions: [], commonPopups: []}``
    """
    if not app_id or not page_name:
        return {
            "pageName": str(page_name or ""),
            "elements": [],
            "waitConditions": [],
            "commonPopups": [],
        }

    app_package = _resolve_app_package(app_id)
    pages = list_knowledge_pages(app_package)

    # 模糊匹配页面名称
    matched_page = None
    page_name_lower = page_name.lower()
    for page in pages:
        pn = (page.get("page_name") or page.get("pageName") or "").lower()
        pid = (page.get("page_id") or page.get("pageId") or "").lower()
        if page_name_lower in pn or page_name_lower in pid or pn in page_name_lower:
            matched_page = page
            break

    if not matched_page:
        return {
            "pageName": str(page_name),
            "elements": [],
            "waitConditions": [],
            "commonPopups": [],
        }

    # 从 key_elements 构建 elements 列表
    elements = []
    for elem_text in _normalize_lines(matched_page.get("key_elements") or matched_page.get("keyElements")):
        elements.append({
            "name": elem_text,
            "type": _guess_element_type(elem_text),
            "description": elem_text,
        })

    # 从 common_assertions 构建 waitConditions
    wait_conditions = []
    for assertion in _normalize_lines(matched_page.get("common_assertions") or matched_page.get("commonAssertions")):
        wait_conditions.append({
            "condition": assertion,
            "type": "visual_assert",
        })

    # commonPopups 暂无数据源，预留结构
    common_popups: List[Dict[str, Any]] = []

    return {
        "pageName": matched_page.get("page_name") or matched_page.get("pageName") or page_name,
        "elements": elements,
        "waitConditions": wait_conditions,
        "commonPopups": common_popups,
    }


def _guess_element_type(text: str) -> str:
    """根据元素描述猜测类型。"""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ("按钮", "button", "点击", "tab", "入口")):
        return "button"
    if any(kw in text_lower for kw in ("输入", "input", "搜索", "框")):
        return "input"
    if any(kw in text_lower for kw in ("列表", "list", "recycler", "scroll")):
        return "list"
    if any(kw in text_lower for kw in ("图标", "icon", "图片", "image")):
        return "image"
    if any(kw in text_lower for kw in ("文字", "text", "标题", "label")):
        return "text"
    return "element"


def query_navigation_path(app_id: str, from_page: str, to_page: str) -> Dict[str, Any]:
    """查询页面间的导航路径。

    基于页面知识库中的 route 信息推导导航步骤。
    当前为简化实现：基于 route 描述文本解析。

    Args:
        app_id: 应用包名。
        from_page: 起始页面。
        to_page: 目标页面。

    Returns:
        ``{fromPage, toPage, steps: [{action, target, description}]}``
    """
    if not app_id or not to_page:
        return {
            "fromPage": str(from_page or ""),
            "toPage": str(to_page or ""),
            "steps": [],
        }

    app_package = _resolve_app_package(app_id)
    pages = list_knowledge_pages(app_package)

    # 查找目标页面
    target_page = None
    to_page_lower = to_page.lower()
    for page in pages:
        pn = (page.get("page_name") or page.get("pageName") or "").lower()
        if to_page_lower in pn or pn in to_page_lower:
            target_page = page
            break

    steps: List[Dict[str, Any]] = []

    if target_page:
        route = target_page.get("route", "")
        if route:
            # 解析 route 描述中的操作步骤
            # 支持格式如 "点击底部 Tab「我的」" / "首页 → 搜索 → 结果页"
            if "→" in route or "->" in route:
                segments = re.split(r"\s*[→->]+\s*", route)
                for seg in segments:
                    seg = seg.strip()
                    if seg:
                        steps.append({
                            "action": "tap",
                            "target": seg,
                            "description": f"点击「{seg}」",
                        })
            elif route:
                steps.append({
                    "action": "tap",
                    "target": route,
                    "description": route,
                })

        # 如果找不到 route 信息，尝试用 key_elements 推导
        if not steps:
            key_elements = _normalize_lines(
                target_page.get("key_elements") or target_page.get("keyElements")
            )
            for elem in key_elements[:3]:
                steps.append({
                    "action": "tap",
                    "target": elem,
                    "description": f"点击「{elem}」进入目标页面",
                })

    return {
        "fromPage": str(from_page or ""),
        "toPage": str(to_page or ""),
        "steps": steps,
    }


# ===================================================================
# ========== 知识可信度 ==========
# ===================================================================

def get_knowledge_stats() -> Dict[str, Any]:
    """获取知识库整体统计。

    Returns:
        ``{failurePatterns: {total, avgHitRate}, caseHistory: {total, avgSuccessRate}, pages: {total}}``
    """
    # 失败模式统计
    failure_data = _load_failure_patterns()
    patterns = failure_data.get("patterns", [])
    total_patterns = len(patterns)
    avg_hit_rate = 0.0
    if total_patterns > 0:
        total_hits = sum(p.get("hitCount", 0) for p in patterns)
        avg_hit_rate = round(total_hits / total_patterns, 2)

    # 用例执行统计
    case_data = _load_case_history()
    cases = case_data.get("cases", [])
    total_cases = len(cases)
    avg_success_rate = 0.0
    if total_cases > 0:
        rates = []
        for case in cases:
            executions = case.get("executions", [])
            if executions:
                success_count = len([e for e in executions if e.get("status") == "success"])
                rates.append(success_count / len(executions))
        if rates:
            avg_success_rate = round(sum(rates) / len(rates), 3)

    # 页面知识统计
    apps = list_knowledge_apps()
    total_pages = 0
    for app in apps:
        try:
            pages = list_knowledge_pages(app)
            total_pages += len(pages)
        except Exception:
            pass

    return {
        "failurePatterns": {
            "total": total_patterns,
            "avgHitRate": avg_hit_rate,
        },
        "caseHistory": {
            "total": total_cases,
            "avgSuccessRate": avg_success_rate,
        },
        "pages": {
            "total": total_pages,
        },
    }


# ===================================================================
# ========== 页面知识文本 & 上下文查询（从 midscene-upload.py 迁移） ==========
# ===================================================================

def knowledge_page_text(meta: Dict[str, Any]) -> str:
    """将知识库页面元数据转换为可读文本。

    Migrated from ``midscene-upload.py:knowledge_page_text``.
    """
    parts = [
        f"页面名称：{meta.get('page_name', '')}",
        f"知识库类型：{'基线库' if meta.get('tier') == 'baseline' else '测试库'}",
        f"到达路径：{meta.get('route', '')}",
        f"页面说明：{meta.get('description', '')}",
    ]
    if meta.get("key_elements"):
        parts.append("关键元素：\n" + "\n".join(f"- {item}" for item in meta["key_elements"]))
    if meta.get("common_assertions"):
        parts.append("常用断言：\n" + "\n".join(f"- {item}" for item in meta["common_assertions"]))
    if meta.get("tags"):
        parts.append("标签：" + "、".join(meta["tags"]))
    return "\n".join(part for part in parts if part.strip())


def _guess_mime(filename: str) -> str:
    """Migrated from ``midscene-upload.py:guess_mime``."""
    lower = str(filename or "").lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".yaml") or lower.endswith(".yml"):
        return "text/yaml"
    if lower.endswith(".svg"):
        return "image/svg+xml"
    if lower.endswith(".webp"):
        return "image/webp"
    return "application/octet-stream"


def load_knowledge_context(
    app_package: str,
    query_text: str,
    limit: int = 5,
    selected_page_ids: Optional[List[str]] = None,
    tier: str = "all",
) -> Tuple[List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """加载知识库上下文（文本、图片、使用页面列表）。

    Migrated from ``midscene-upload.py:load_knowledge_context``.

    Returns:
        ``(text_assets, image_assets, used_pages)``
    """
    pages = list_knowledge_pages(app_package, tier=tier)
    if not pages:
        return [], [], []
    query = (query_text or "").lower()
    selected_page_ids = [str(item) for item in (selected_page_ids or []) if str(item).strip()]

    def score(meta: Dict[str, Any]) -> int:
        source = " ".join([
            meta.get("page_name", ""),
            meta.get("route", ""),
            meta.get("description", ""),
            " ".join(meta.get("key_elements") or []),
            " ".join(meta.get("common_assertions") or []),
            " ".join(meta.get("tags") or []),
        ]).lower()
        points = 0
        for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", source):
            if token and token in query:
                points += 1
        return points

    if selected_page_ids:
        manual = [page for page in pages if page.get("page_id") in selected_page_ids]
        remaining = [page for page in pages if page.get("page_id") not in selected_page_ids]
        ranked = sorted(remaining, key=score, reverse=True)
        selected = (manual + [page for page in ranked if score(page) > 0])[:limit]
    else:
        ranked = sorted(pages, key=score, reverse=True)
        selected = [page for page in ranked if score(page) > 0][:limit] or ranked[:min(3, limit)]

    text_assets: List[str] = []
    image_assets: List[Dict[str, Any]] = []
    for page in selected:
        text_assets.append("[APP页面知识]\n" + knowledge_page_text(page))
        screenshot = page.get("screenshot")
        if screenshot and len(image_assets) < 4:
            path = safe_join(_knowledge_page_dir(app_package, page["page_id"]), screenshot)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    image_assets.append({
                        "name": screenshot,
                        "mime": _guess_mime(screenshot),
                        "base64": base64.b64encode(f.read()).decode("ascii"),
                    })
    used_pages = [
        {
            "app_package": page.get("app_package"),
            "page_id": page.get("page_id"),
            "page_name": page.get("page_name"),
            "route": page.get("route", ""),
            "tier": page.get("tier", "test"),
            "screenshot": page.get("screenshot", ""),
        }
        for page in selected
    ]
    return text_assets, image_assets, used_pages


# ===================================================================
# ========== 修复知识上下文 & 业务上下文 ==========
# ===================================================================

def _extract_app_package_from_yaml(yaml_text: str) -> str:
    """Migrated from ``midscene-upload.py:extract_app_package_from_yaml``."""
    packages: List[str] = []
    for line in (yaml_text or "").splitlines():
        m = re.match(r"^\s*-\s+(?:launch|terminate)\s*:\s*[\"']?([^\"'\s#]+)", line)
        if m:
            pkg = m.group(1).strip()
            if pkg and pkg not in packages:
                packages.append(pkg)
    for pkg in packages:
        if "." in pkg:
            return pkg
    return packages[0] if packages else ""


def _app_package_for_module(module: str) -> str:
    """Migrated from ``midscene-upload.py:app_package_for_module``."""
    try:
        # Late import to avoid circular dependency
        from .sonic_service import sonic_notify_known_apps
        apps = sonic_notify_known_apps()
        for app in apps:
            if module in (app.get("modules") or []):
                package = (app.get("package") or "").strip()
                if package:
                    return package
    except Exception:
        pass
    return ""


def _resolve_app_package(
    module: str = "",
    file: str = "",
    yaml_text: str = "",
    explicit: str = "",
    allow_default: bool = False,
) -> str:
    """Migrated from ``midscene-upload.py:resolve_app_package``."""
    resolved = (
        (explicit or "").strip()
        or _app_package_for_module(module)
        or _extract_app_package_from_yaml(yaml_text)
    ).strip()
    if resolved:
        return resolved
    return (os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE).strip() if allow_default else "")


def _load_baseline_refs() -> Dict[str, Any]:
    """Migrated from ``midscene-upload.py:load_baseline_refs``."""
    data = read_json_file(BASELINE_REFS_FILE, default={})
    return data if isinstance(data, dict) else {}


def _baseline_ref_key(app_package: str, module: str, file: str, task_name: str = "") -> str:
    """Migrated from ``midscene-upload.py:baseline_ref_key``."""
    return "::".join([
        app_package or _app_package_for_module(module) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE),
        module or "",
        clean_filename(file or ""),
        task_name or "",
    ])


def _get_baseline_ref_page_ids(
    app_package: str,
    module: str,
    file: str,
    task_name: str = "",
) -> List[str]:
    """Migrated from ``midscene-upload.py:get_baseline_ref_page_ids``."""
    refs = _load_baseline_refs()
    app_package = app_package or _app_package_for_module(module) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    page_ids: List[str] = []
    for key in (
        _baseline_ref_key(app_package, module, file, ""),
        _baseline_ref_key(app_package, module, file, task_name),
    ):
        row = refs.get(key) or {}
        for page_id in row.get("page_ids") or []:
            if page_id and page_id not in page_ids:
                page_ids.append(page_id)
    return page_ids


def repair_knowledge_context(
    module: str,
    file: str,
    yaml_text: str,
    log_text: str,
    task_name: str = "",
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """执行修复时加载知识库上下文。

    Migrated from ``midscene-upload.py:repair_knowledge_context``.

    Returns:
        ``(text, knowledge_images, used_pages)``
    """
    app_package = _resolve_app_package(module, file, yaml_text)
    selected_page_ids = _get_baseline_ref_page_ids(app_package, module, file, task_name)
    query_text = "\n".join([
        module or "",
        file or "",
        task_name or "",
        (yaml_text or "")[-6000:],
        (log_text or "")[-3000:],
    ])
    knowledge_texts, knowledge_images, used_pages = load_knowledge_context(
        app_package, query_text, limit=6,
        selected_page_ids=selected_page_ids, tier="baseline",
    )
    if not knowledge_texts:
        return "", [], []
    text = "\n\n".join(knowledge_texts)
    return text, knowledge_images, used_pages


def _strip_yaml_quotes(value: Any) -> str:
    """Migrated from ``midscene-upload.py:strip_yaml_quotes``."""
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
        value = value[1:-1]
    return value.replace('\\"', '"').replace("\\'", "'").strip()


def _flow_texts_from_task_block(
    block: str,
    keys: Optional[set] = None,
) -> List[Tuple[str, str]]:
    """Migrated from ``midscene-upload.py:flow_texts_from_task_block``."""
    keys = set(keys or [])
    result: List[Tuple[str, str]] = []
    for line in (block or "").splitlines():
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key = m.group(1)
        if keys and key not in keys:
            continue
        value = _strip_yaml_quotes(m.group(2))
        if value:
            result.append((key, value))
    return result


def _task_name_from_block(block: str) -> str:
    """Migrated from ``midscene-upload.py:task_name_from_block``."""
    m = re.search(r"^\s*-\s+name:\s*(.+?)\s*$", block or "", flags=re.M)
    return _strip_yaml_quotes(m.group(1)) if m else "未命名用例"


def _extract_baseline_meta_from_block(block: str) -> Dict[str, str]:
    """Migrated from ``midscene-upload.py:extract_baseline_meta_from_block``."""
    meta: Dict[str, str] = {}
    for line in (block or "").splitlines():
        m = re.match(r"^\s*#\s*baseline\.([A-Za-z_]+)\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def task_business_context(
    task_block: str,
    knowledge_text: str = "",
) -> Dict[str, Any]:
    """从 task 块提取业务上下文，用于 AI 修复。

    Migrated from ``midscene-upload.py:task_business_context``.
    """
    meta = _extract_baseline_meta_from_block(task_block)
    actions: List[str] = []
    assertions: List[str] = []
    waits: List[str] = []
    for key, text in _flow_texts_from_task_block(task_block, {"aiTap", "aiAction", "aiAssert", "aiWaitFor"}):
        if key == "aiTap":
            actions.append(text)
        elif key in ("aiAssert", "aiWaitFor"):
            (waits if key == "aiWaitFor" else assertions).append(text)
        elif key == "aiAction":
            if text.startswith("验证："):
                assertions.append(text.replace("验证：", "", 1).strip())
            elif not text.startswith("确认前置条件："):
                actions.append(text)

    knowledge_lines: List[str] = []
    for line in (knowledge_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if any(key in line for key in ("页面", "路径", "入口", "关键元素", "常用断言", "route", "page")):
            knowledge_lines.append(line)
        if len(knowledge_lines) >= 18:
            break

    return {
        "goal": meta.get("goal") or f"验证{_task_name_from_block(task_block)}",
        "scenario": meta.get("scenario") or "",
        "start_page": meta.get("start_page") or "",
        "business_path": meta.get("path") or " -> ".join(actions[:10]),
        "expected_result": meta.get("expected") or "；".join(assertions[:8]),
        "repair_hint": meta.get("repair_hint") or "",
        "risk": meta.get("risk") or "",
        "coverage": meta.get("coverage") or "",
        "data_requirements": meta.get("data") or "",
        "automation_reason": meta.get("automation") or "",
        "current_actions": actions[:20],
        "current_assertions": assertions[:12],
        "current_waits": waits[:8],
        "matched_page_knowledge": knowledge_lines,
    }


# ===================================================================
# ========== Figma 集成（从 midscene-upload.py 迁移） ==========
# ===================================================================

def figma_proxy_url() -> str:
    """Migrated from ``midscene-upload.py:figma_proxy_url``."""
    return (
        os.getenv("FIGMA_PROXY")
        or os.getenv("FIGMA_HTTPS_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or ""
    ).strip()


def _urlopen_with_retry(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    retries: int = 0,
    binary: bool = False,
    max_bytes: Optional[int] = None,
) -> Any:
    """Migrated from ``midscene-upload.py:urlopen_with_retry``."""
    headers = headers or {}
    last_error: Optional[Exception] = None
    opener = None
    proxy = figma_proxy_url() if "figma.com" in url or "figma" in url else ""
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    for attempt in range(max(1, retries + 1)):
        try:
            req = urllib.request.Request(url, headers=headers)
            open_fn = opener.open if opener else urllib.request.urlopen
            with open_fn(req, timeout=timeout) as resp:
                if binary:
                    if max_bytes:
                        data = resp.read(max_bytes + 1)
                    else:
                        data = resp.read()
                    if max_bytes and len(data) > max_bytes:
                        raise ValueError("图片过大，请选择更小的 Frame 或降低导出范围")
                    return data
                return resp.read().decode("utf-8")
        except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
            last_error = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_error  # type: ignore[misc]


def _urlopen_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    retries: int = 0,
) -> Any:
    """Migrated from ``midscene-upload.py:urlopen_json``."""
    return json.loads(_urlopen_with_retry(url, headers=headers, timeout=timeout, retries=retries))


def _urlopen_bytes(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    max_bytes: int = 8 * 1024 * 1024,
    retries: int = 0,
) -> bytes:
    """Migrated from ``midscene-upload.py:urlopen_bytes``."""
    data = _urlopen_with_retry(
        url, headers=headers, timeout=timeout,
        retries=retries, binary=True, max_bytes=max_bytes,
    )
    if len(data) > max_bytes:
        raise ValueError("图片过大，请选择更小的 Frame 或降低导出范围")
    return data


def figma_token() -> str:
    """Migrated from ``midscene-upload.py:figma_token``."""
    return (os.getenv("FIGMA_TOKEN") or os.getenv("FIGMA_ACCESS_TOKEN") or "").strip()


def parse_figma_url(figma_url: str) -> Tuple[str, str]:
    """Migrated from ``midscene-upload.py:parse_figma_url``.

    Returns:
        ``(file_key, node_id)``
    """
    raw = (figma_url or "").strip()
    if not raw:
        raise ValueError("Figma 链接不能为空")
    parsed = urllib.parse.urlparse(raw)
    parts = [part for part in parsed.path.split("/") if part]
    file_key = ""
    for key in ("file", "design", "proto"):
        if key in parts:
            idx = parts.index(key)
            if idx + 1 < len(parts):
                file_key = parts[idx + 1]
                break
    qs = urllib.parse.parse_qs(parsed.query)
    node_id = (qs.get("node-id") or qs.get("node_id") or [""])[0].replace("-", ":")
    if not file_key:
        raise ValueError("无法从 Figma 链接中解析 file key，请复制 design/file 链接")
    return file_key, node_id


def figma_node_visible(node: Any) -> bool:
    return not (isinstance(node, dict) and node.get("visible") is False)


def figma_api_json(path: str, query: Optional[Dict[str, str]] = None) -> Any:
    """Migrated from ``midscene-upload.py:figma_api_json``."""
    token = figma_token()
    if not token:
        raise ValueError("未配置 FIGMA_TOKEN")
    base = os.getenv("FIGMA_API_BASE", DEFAULT_FIGMA_API_BASE).rstrip("/")
    url = base + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    try:
        return _urlopen_json(
            url, headers={"X-Figma-Token": token},
            timeout=FIGMA_TIMEOUT_SECONDS, retries=FIGMA_RETRY_COUNT,
        )
    except Exception as e:
        proxy_hint = (
            "当前未配置 FIGMA_PROXY/HTTPS_PROXY。"
            if not figma_proxy_url()
            else f"当前代理：{figma_proxy_url()}"
        )
        raise ValueError(
            f"连接 Figma API 失败：{e}。"
            f"腾讯云大陆服务器访问 Figma 可能不稳定，请配置海外代理或海外中转服务。{proxy_hint}"
        )


def figma_node_texts(node: Dict[str, Any], limit: int = 60) -> List[str]:
    """Migrated from ``midscene-upload.py:figma_node_texts``."""
    texts: List[str] = []

    def walk(item: Any) -> None:
        if len(texts) >= limit or not isinstance(item, dict):
            return
        if not figma_node_visible(item):
            return
        if item.get("type") == "TEXT":
            text = (item.get("characters") or item.get("name") or "").strip()
            if text and text not in texts:
                texts.append(text)
        for child in item.get("children") or []:
            walk(child)

    walk(node)
    return texts


def figma_node_size(node: Dict[str, Any]) -> Tuple[float, float]:
    """Migrated from ``midscene-upload.py:figma_node_size``."""
    box = node.get("absoluteBoundingBox") or {}
    width = float(box.get("width") or node.get("width") or 0)
    height = float(box.get("height") or node.get("height") or 0)
    return width, height


def figma_node_area(node: Dict[str, Any]) -> float:
    """Migrated from ``midscene-upload.py:figma_node_area``."""
    width, height = figma_node_size(node)
    return width * height


def figma_child_count(node: Dict[str, Any]) -> int:
    """Migrated from ``midscene-upload.py:figma_child_count``."""
    count = 0

    def walk(item: Any) -> None:
        nonlocal count
        if not isinstance(item, dict):
            return
        if not figma_node_visible(item):
            return
        for child in item.get("children") or []:
            if not figma_node_visible(child):
                continue
            count += 1
            walk(child)

    walk(node)
    return count


def figma_find_node_path(root: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    """Migrated from ``midscene-upload.py:figma_find_node_path``."""
    target = str(node_id or "")
    if not target:
        return []
    path: List[Dict[str, Any]] = []

    def walk(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        path.append(node)
        if node.get("id") == target:
            return True
        for child in node.get("children") or []:
            if walk(child):
                return True
        path.pop()
        return False

    return path if walk(root) else []


def figma_page_score(node: Dict[str, Any], depth: int = 0) -> int:
    """Migrated from ``midscene-upload.py:figma_page_score``."""
    name = (node.get("name") or "").lower()
    width, height = figma_node_size(node)
    area = width * height
    texts = figma_node_texts(node, limit=80)
    children = figma_child_count(node)
    score = 0
    if width >= 280 and height >= 480:
        score += 4
    elif width >= 240 and height >= 360:
        score += 2
    if area >= 180000:
        score += 2
    if 0.28 <= (width / height if height else 0) <= 2.4:
        score += 1
    if len(texts) >= 3:
        score += 2
    if len(texts) >= 8:
        score += 1
    if children >= 8:
        score += 1
    if any(key in name for key in (
        "首页", "我的", "登录", "详情", "列表", "页面", "画板",
        "screen", "page", "home", "profile", "detail", "list",
    )):
        score += 2
    if any(key in name for key in (
        "title", "标题", "header", "navbar", "tab",
        "button", "按钮", "icon", "组件", "component", "编组",
    )):
        score -= 4
    if height < 180 or width < 180:
        score -= 6
    if depth > 8:
        score -= 1
    return score


def figma_nearest_page_root(path: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Migrated from ``midscene-upload.py:figma_nearest_page_root``."""
    if not path:
        return None
    for node in reversed(path):
        if node.get("type") == "FRAME" and figma_page_score(node, int(node.get("_figma_depth") or 0)) >= 3:
            return node
    for node in reversed(path):
        if node.get("type") == "CANVAS":
            return node
    return path[-1]


def figma_direct_node_needs_parent_lookup(root: Dict[str, Any]) -> bool:
    """Migrated from ``midscene-upload.py:figma_direct_node_needs_parent_lookup``."""
    if not isinstance(root, dict):
        return False
    node_type = root.get("type") or ""
    if node_type in {"CANVAS", "FRAME", "SECTION", "COMPONENT", "INSTANCE"} and (root.get("children") or []):
        return False
    return True


def figma_canvas_name(path: List[Dict[str, Any]]) -> str:
    """Migrated from ``midscene-upload.py:figma_canvas_name``."""
    for node in path or []:
        if node.get("type") == "CANVAS":
            return node.get("name") or ""
    return ""


def _normalize_figma_name(value: Any) -> str:
    """Migrated from ``midscene-upload.py:normalize_figma_name``."""
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+copy\s*\d*$", "", text, flags=re.I)
    text = re.sub(
        r"^(frame|group|section|screen|page|页面|画板)\s*[-_:#]?\s*",
        "", text, flags=re.I,
    ).strip()
    return text[:60]


def _is_generic_figma_name(value: Any) -> bool:
    """Migrated from ``midscene-upload.py:is_generic_figma_name``."""
    text = _normalize_figma_name(value).lower()
    if not text:
        return True
    generic = (
        "frame", "group", "section", "screen", "page", "untitled", "copy",
        "iphone", "android", "mobile", "desktop", "标题", "title", "header", "navbar",
        "button", "按钮", "组件", "component", "编组",
    )
    if any(text == key or text.startswith(key + " ") for key in generic):
        return True
    if re.fullmatch(r"[\d\s._#:-]+", text):
        return True
    return False


def figma_likely_title_text(texts: List[str]) -> str:
    """Migrated from ``midscene-upload.py:figma_likely_title_text``."""
    bad = ("确定", "取消", "返回", "更多", "保存", "关闭", "编辑", "删除", "完成", "下一步")
    for text in texts or []:
        text = str(text or "").strip()
        if not text or len(text) > 24 or text in bad:
            continue
        if re.fullmatch(r"[\d\s._#:-]+", text):
            continue
        return text
    return ""


def figma_page_name(frame: Dict[str, Any], canvas_name: str = "") -> str:
    """Migrated from ``midscene-upload.py:figma_page_name``."""
    raw_name = _normalize_figma_name(frame.get("name") or "")
    texts = figma_node_texts(frame, limit=30)
    title = figma_likely_title_text(texts)
    if _is_generic_figma_name(raw_name):
        return _normalize_figma_name(title or canvas_name or raw_name or "Figma页面") or "Figma页面"
    if canvas_name and raw_name.lower() in ("home", "profile", "mine", "list", "detail"):
        return _normalize_figma_name(f"{canvas_name}-{raw_name}")
    return raw_name


def figma_device_profile(width: float, height: float) -> str:
    """Migrated from ``midscene-upload.py:figma_device_profile``."""
    width = float(width or 0)
    height = float(height or 0)
    long_edge = max(width, height)
    short_edge = min(width, height)
    if long_edge >= 900 and short_edge >= 600:
        return "tablet"
    if short_edge <= 520 and long_edge <= 980:
        return "phone"
    if long_edge >= 900:
        return "wide"
    return "unknown"


def figma_device_label(profile: str) -> str:
    """Migrated from ``midscene-upload.py:figma_device_label``."""
    return {
        "tablet": "平板",
        "phone": "手机",
        "wide": "宽屏",
        "unknown": "未知端",
    }.get(profile or "unknown", "未知端")


def figma_variant_signature(node: Dict[str, Any]) -> str:
    """Migrated from ``midscene-upload.py:figma_variant_signature``."""
    texts = figma_node_texts(node, limit=80)
    blob = " ".join(texts)
    variants: List[str] = []
    color_words = (
        "红色", "蓝色", "绿色", "黄色", "紫色", "白色", "黑色", "灰色", "橙色", "粉色", "透明",
    )
    for color in color_words:
        if color in blob and color not in variants:
            variants.append(color)
    color_pattern = "|".join(color_words)
    for match in re.findall(rf"(?:官方|默认|当前|选择)?耗材[-：: ]?(?:{color_pattern})", blob):
        match = match.strip(" ，,。；;")
        if match and match not in variants:
            variants.append(match[:24])
    if variants:
        return "+".join(variants[:4])
    title = figma_likely_title_text(texts)
    return _normalize_figma_name(title or node.get("name") or "")[:30]


def figma_collect_visual_nodes(
    root: Dict[str, Any],
    limit: int = 500,
    canvas_name: str = "",
) -> List[Dict[str, Any]]:
    """Migrated from ``midscene-upload.py:figma_collect_visual_nodes``."""
    visual_types = {"FRAME", "COMPONENT", "INSTANCE", "SECTION"}
    candidates: List[Dict[str, Any]] = []
    if not figma_node_visible(root):
        return candidates
    direct_root = bool(root.get("_figma_direct_link"))
    direct_context_text = " ".join([
        str(root.get("name") or ""),
        " ".join(figma_node_texts(root, limit=40)),
    ]).strip()

    def walk_children(
        parent: Dict[str, Any],
        depth: int = 0,
        parent_name: str = "",
        parent_id: str = "",
        current_canvas: str = "",
        in_direct_group: bool = False,
    ) -> None:
        if not figma_node_visible(parent):
            return
        if parent.get("type") == "CANVAS":
            current_canvas = parent.get("name") or current_canvas
        next_direct_group = bool(
            in_direct_group
            or parent.get("_figma_direct_link")
            or parent.get("_figma_direct_group")
        )
        for child in parent.get("children") or []:
            if len(candidates) >= limit:
                return
            if not figma_node_visible(child):
                continue
            child["_figma_depth"] = depth + 1
            child["_figma_parent_name"] = parent_name
            child["_figma_parent_id"] = parent_id
            child["_figma_canvas_name"] = current_canvas or canvas_name
            if next_direct_group:
                child["_figma_direct_group"] = True
                child["_figma_direct_context"] = direct_context_text
            if child.get("type") in visual_types:
                candidates.append(child)
            walk_children(
                child, depth + 1,
                child.get("name") or parent_name,
                child.get("id") or parent_id,
                current_canvas, next_direct_group,
            )

    if root.get("type") in visual_types:
        root["_figma_depth"] = 0
        root["_figma_parent_name"] = ""
        root["_figma_parent_id"] = ""
        root["_figma_canvas_name"] = canvas_name
        if direct_root:
            root["_figma_direct_group"] = True
            root["_figma_direct_context"] = direct_context_text
        candidates.append(root)
    walk_children(root, 0, root.get("name") or "", root.get("id") or "", canvas_name, direct_root)
    return candidates[:limit]


def figma_frame_candidates(
    root: Dict[str, Any],
    limit: int = 12,
    mode: str = "smart",
    min_width: int = 240,
    min_height: int = 360,
    pinned_node_ids: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Migrated from ``midscene-upload.py:figma_frame_candidates``."""
    mode = (mode or "smart").strip().lower()
    pinned_node_ids = {str(item) for item in (pinned_node_ids or []) if str(item or "").strip()}
    root_canvas_name = root.get("_figma_canvas_name") or (root.get("name") if root.get("type") == "CANVAS" else "")
    visual_nodes = figma_collect_visual_nodes(root, canvas_name=root_canvas_name)
    if not visual_nodes:
        return []

    enriched: List[Dict[str, Any]] = []
    for node in visual_nodes:
        width, height = figma_node_size(node)
        depth = int(node.get("_figma_depth") or 0)
        score = figma_page_score(node, depth)
        enriched.append({
            "node": node,
            "width": width,
            "height": height,
            "area": width * height,
            "depth": depth,
            "score": score,
            "text_count": len(figma_node_texts(node, limit=100)),
            "child_count": figma_child_count(node),
        })
    for item in enriched:
        node_id = str(item["node"].get("id") or "")
        item["pinned"] = bool(node_id and node_id in pinned_node_ids) or bool(item["node"].get("_figma_direct_link"))

    if mode in ("all", "loose"):
        selected = [
            item for item in enriched
            if item["width"] >= max(120, min_width * 0.5) and item["height"] >= max(120, min_height * 0.35)
        ]
    else:
        selected = [
            item for item in enriched
            if item["node"].get("type") == "FRAME"
            and item["width"] >= min_width
            and item["height"] >= min_height
            and item["score"] >= 3
        ]
        if not selected:
            selected = [item for item in enriched if item["node"].get("type") == "FRAME" and item["score"] >= 3]
    pinned_selected = [item for item in enriched if item.get("pinned")]
    if pinned_selected:
        pinned_ids = {item["node"].get("id") for item in pinned_selected}
        selected = pinned_selected + [item for item in selected if item["node"].get("id") not in pinned_ids]

    selected_ids = {item["node"].get("id") for item in selected}
    child_like_ids: set = set()
    for item in selected:
        node = item["node"]
        node_id = node.get("id")
        direct_children = [
            other for other in selected
            if other is not item
            and (other["node"].get("_figma_parent_id") or "") == node_id
            and other["node"].get("type") == "FRAME"
            and other["width"] >= min_width
            and other["height"] >= min_height
            and other["score"] >= 3
        ]
        if len(direct_children) >= 2:
            max_child_width = max((other["width"] for other in direct_children), default=0)
            max_child_height = max((other["height"] for other in direct_children), default=0)
            parent_profile = figma_device_profile(item["width"], item["height"])
            looks_like_screen_group = (
                parent_profile != "phone"
                or item["width"] > max_child_width * 1.25
                or item["height"] > max_child_height * 1.25
            )
            if looks_like_screen_group:
                child_like_ids.add(node_id)
                continue
        for other in selected:
            if other is item:
                continue
            parent_id = other["node"].get("_figma_parent_id") or ""
            if item.get("pinned"):
                continue
            if parent_id and parent_id == node.get("id") and other["area"] >= item["area"] * 0.15:
                child_like_ids.add(node.get("id"))
    deduped = [item for item in selected if item["node"].get("id") not in child_like_ids or len(selected_ids) == 1]

    deduped.sort(
        key=lambda item: (1 if item.get("pinned") else 0, item["score"], item["area"], -item["depth"]),
        reverse=True,
    )
    result: List[Dict[str, Any]] = []
    seen_names: set = set()
    for item in deduped:
        node = item["node"]
        page_name = figma_page_name(node, node.get("_figma_canvas_name") or "")
        device_profile = figma_device_profile(item["width"], item["height"])
        variant_signature = figma_variant_signature(node)
        key = (page_name, device_profile, round(item["width"]), round(item["height"]), variant_signature)
        if key in seen_names:
            continue
        seen_names.add(key)
        node["_figma_score"] = item["score"]
        node["_figma_width"] = item["width"]
        node["_figma_height"] = item["height"]
        node["_figma_device_profile"] = device_profile
        node["_figma_variant_signature"] = variant_signature
        node["_figma_text_count"] = item["text_count"]
        node["_figma_pinned"] = bool(item.get("pinned"))
        result.append(node)
        if len(result) >= limit and not any(
            other.get("pinned") and other["node"].get("id") not in {row.get("id") for row in result}
            for other in deduped
        ):
            break
    return result


def figma_image_map(file_key: str, node_ids: List[str]) -> Dict[str, str]:
    """Migrated from ``midscene-upload.py:figma_image_map``."""
    node_ids = [node_id for node_id in node_ids if node_id]
    if not node_ids or not FIGMA_IMAGE_EXPORT:
        return {}
    data = figma_api_json(f"/images/{urllib.parse.quote(file_key)}", {
        "ids": ",".join(node_ids),
        "format": "png",
        "scale": "1",
    })
    return data.get("images") or {}


def download_figma_screenshots(
    drafts: List[Dict[str, Any]],
    images: Dict[str, str],
    max_workers: int = 4,
) -> None:
    """Migrated from ``midscene-upload.py:download_figma_screenshots``."""
    if not drafts or not images:
        return
    jobs: List[tuple] = []
    for index, draft in enumerate(drafts):
        node_id = (draft.get("figma") or {}).get("node_id") or ""
        image_url = images.get(node_id) or ""
        if image_url:
            jobs.append((index, draft, image_url))
    if not jobs:
        return

    def fetch(job: tuple) -> tuple:
        index, draft, image_url = job
        try:
            image_bytes = _urlopen_bytes(
                image_url,
                timeout=FIGMA_TIMEOUT_SECONDS,
                max_bytes=3 * 1024 * 1024,
                retries=FIGMA_RETRY_COUNT,
            )
            name_bits = [
                draft.get("page_name") or "page",
                (draft.get("figma") or {}).get("device_label") or "",
                (draft.get("figma") or {}).get("variant") or "",
            ]
            image_name = "-".join([str(item) for item in name_bits if item])
            return index, {
                "name": clean_asset_filename(f"figma-{clean_id(image_name)}.png"),
                "mime": "image/png",
                "contentBase64": base64.b64encode(image_bytes).decode("ascii"),
            }
        except Exception as exc:
            return index, {"_error": str(exc)}

    worker_count = max(1, min(int(max_workers or 4), 6, len(jobs)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for index, screenshot in executor.map(fetch, jobs):
            if screenshot.get("_error"):
                drafts[index]["screenshot"] = {}
                drafts[index].setdefault("figma", {})["screenshot_error"] = screenshot.get("_error")
            else:
                drafts[index]["screenshot"] = screenshot


def figma_frame_to_draft(
    app_package: str,
    figma_url: str,
    file_key: str,
    frame: Dict[str, Any],
    image_url: str = "",
    canvas_name: str = "",
) -> Dict[str, Any]:
    """Migrated from ``midscene-upload.py:figma_frame_to_draft``."""
    node_id = frame.get("id") or ""
    canvas_name = canvas_name or frame.get("_figma_canvas_name") or ""
    name = figma_page_name(frame, canvas_name)
    texts = figma_node_texts(frame)
    width, height = figma_node_size(frame)
    device_profile = frame.get("_figma_device_profile") or figma_device_profile(width, height)
    variant_signature = frame.get("_figma_variant_signature") or figma_variant_signature(frame)
    key_elements = texts[:30]
    assertions = [f"页面展示「{text}」" for text in texts[:8]]
    if not assertions:
        assertions = [f"页面展示「{name}」相关内容"]
    screenshot: Dict[str, Any] = {}
    if image_url:
        try:
            image_bytes = _urlopen_bytes(
                image_url, timeout=FIGMA_TIMEOUT_SECONDS,
                max_bytes=3 * 1024 * 1024, retries=FIGMA_RETRY_COUNT,
            )
            screenshot = {
                "name": clean_asset_filename(f"figma-{clean_id(name)}.png"),
                "mime": "image/png",
                "contentBase64": base64.b64encode(image_bytes).decode("ascii"),
            }
        except Exception:
            screenshot = {}
    return {
        "app_package": app_package,
        "page_id": clean_id(name),
        "page_name": name,
        "tier": "test",
        "route": f"Figma 设计稿：{canvas_name + ' / ' if canvas_name else ''}{name}",
        "description": "从 Figma 设计稿导入的页面知识。" + (f" 可见文案：{'、'.join(texts[:12])}" if texts else ""),
        "key_elements": key_elements,
        "common_assertions": assertions,
        "tags": ["Figma", "设计稿"],
        "figma": {
            "file_key": file_key,
            "node_id": node_id,
            "url": figma_url,
            "type": frame.get("type") or "",
            "name": frame.get("name") or name,
            "page_name": name,
            "canvas_name": canvas_name,
            "width": round(width),
            "height": round(height),
            "device_profile": device_profile,
            "device_label": figma_device_label(device_profile),
            "variant": variant_signature,
            "score": frame.get("_figma_score", 0),
            "text_count": frame.get("_figma_text_count", len(texts)),
            "pinned": bool(frame.get("_figma_pinned") or frame.get("_figma_direct_link")),
            "direct_group": bool(frame.get("_figma_direct_group")),
            "direct_context": frame.get("_figma_direct_context") or "",
        },
        "screenshot": screenshot,
    }


def figma_draft_search_blob(draft: Dict[str, Any]) -> str:
    """Migrated from ``midscene-upload.py:figma_draft_search_blob``."""
    figma = draft.get("figma") or {}
    parts = [
        draft.get("page_name", ""),
        draft.get("description", ""),
        " ".join(_normalize_lines(draft.get("key_elements") or draft.get("keyElements"))),
        " ".join(_normalize_lines(draft.get("common_assertions") or draft.get("commonAssertions"))),
        " ".join(_normalize_lines(draft.get("tags"))),
        figma.get("name", ""),
        figma.get("page_name", ""),
        figma.get("canvas_name", ""),
        figma.get("direct_context", ""),
    ]
    return _normalize_requirement_search_text(" ".join(str(part or "") for part in parts))


_CJK_COMPAT_TEXT_MAP = str.maketrans({
    "⻓": "长",
    "⻚": "页",
    "⻔": "门",
    "⻋": "车",
    "⻜": "飞",
    "戶": "户",
})


def _normalize_requirement_search_text(value: Any) -> str:
    """Normalize PDF/Figma text before keyword extraction and matching."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return text.translate(_CJK_COMPAT_TEXT_MAP)


def _figma_draft_identity(draft: Dict[str, Any]) -> str:
    figma = (draft or {}).get("figma") or {}
    return str(
        figma.get("node_id")
        or draft.get("page_id")
        or draft.get("page_name")
        or id(draft)
    )


def figma_requirement_terms(query_text: str) -> List[str]:
    """Migrated from ``midscene-upload.py:figma_requirement_terms``."""
    raw_text = _normalize_requirement_search_text(query_text).lower()
    terms: List[str] = []
    non_chinese_tokens = re.findall(r"[a-z0-9_./-]{2,}", raw_text)
    for token in non_chinese_tokens:
        if not re.fullmatch(r"\d+", token):
            terms.append(token)
    stop = {
        "页面", "功能", "用户", "展示", "进入", "点击", "验证", "正常", "流程", "场景",
        "可以", "进行", "是否", "相关", "测试", "按钮", "入口", "列表", "内容",
        "查看", "打开", "支持", "完成", "实现", "需要", "能够", "应该", "对应",
        "增加", "新增", "修改", "优化",
    }
    useful_short_terms = {
        "登录", "注册", "搜索", "筛选", "排序", "支付", "退款", "下单", "订单", "购物",
        "发票", "地址", "上传", "下载", "分享", "收藏", "点赞", "评论", "审核", "审批",
        "权限", "角色", "配置", "报表", "统计", "导入", "导出", "同步", "回调", "通知",
        "弹窗", "提示", "确认", "取消", "颜色", "耗材", "打印", "模型", "图片", "语音",
        "识别", "生成", "预览", "提交", "编辑", "删除", "保存", "失败", "成功", "异常",
        "边界", "弱网", "缓存", "分页", "刷新", "排队", "并发", "超时",
        "建模", "创作", "长按", "引导",
    }
    chinese_parts = re.findall(r"[\u4e00-\u9fff]{2,}", raw_text)
    for part in chinese_parts:
        part = part.strip()
        if part in stop:
            continue
        if 2 <= len(part) <= 8:
            terms.append(part)
        hits: List[tuple] = []
        for term in useful_short_terms:
            idx = part.find(term)
            if idx >= 0:
                hits.append((idx, term))
        hits.sort()
        for _idx, term in hits:
            terms.append(term)
        for (_idx_a, term_a), (idx_b, term_b) in zip(hits, hits[1:]):
            if term_a in stop or term_b in stop:
                continue
            joined = part[_idx_a:idx_b + len(term_b)]
            if 2 <= len(joined) <= 8:
                terms.append(joined)
    return list(dict.fromkeys(terms))[:120]


def score_figma_draft_for_requirement(
    draft: Dict[str, Any],
    query_text: str,
) -> Tuple[int, List[str]]:
    """Migrated from ``midscene-upload.py:score_figma_draft_for_requirement``."""
    terms = figma_requirement_terms(query_text)
    if not terms:
        return 0, []
    blob = _normalize_requirement_search_text(figma_draft_search_blob(draft)).lower()
    if not blob:
        return 0, []
    matched: List[str] = []
    score = 0
    generic_terms = {
        "打印", "确认", "页面", "按钮", "入口", "弹窗", "状态", "提示", "结果", "流程", "任务",
        "模型", "颜色", "上传", "生成", "查看", "点击", "首页", "列表", "详情", "设置", "验证",
        "ai", "ui", "app", "android", "ios",
    }
    important_terms = [
        term for term in terms
        if len(term) >= 2 and term not in generic_terms and not term.isdigit()
    ]
    for term in terms:
        if term in blob:
            matched.append(term)
            score += 1
            if len(term) >= 4:
                score += 1
    page_name = _normalize_requirement_search_text(draft.get("page_name") or "").lower()
    query = _normalize_requirement_search_text(query_text).lower()
    for term in terms:
        if term and term in page_name:
            score += 4
    if page_name and page_name in query:
        score += 5
    figma = draft.get("figma") or {}
    canvas_name = _normalize_requirement_search_text(figma.get("canvas_name") or "").lower()
    if canvas_name and canvas_name in query:
        score += 1
    matched_important = [term for term in matched if term in important_terms]
    if important_terms and not matched_important:
        score = min(score, 2)
        matched = matched[:6] + ["缺少核心需求词"]
    return score, matched[:12]


def filter_figma_drafts_for_requirement(
    drafts: List[Dict[str, Any]],
    query_text: str,
    limit: int = 12,
    min_score: int = 1,
    fallback_on_no_match: bool = False,
    pinned_node_ids: Optional[set] = None,
    max_limit: int = 24,
    direct_scope_only: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Migrated from ``midscene-upload.py:filter_figma_drafts_for_requirement``.

    Returns:
        ``(selected, ignored)``
    """
    if not drafts:
        return [], []
    limit = max(1, int(limit or 12))
    max_limit = max(limit, int(max_limit or limit))
    pinned_node_ids = {str(item) for item in (pinned_node_ids or []) if str(item or "").strip()}
    if direct_scope_only:
        direct_scope = [
            draft for draft in drafts
            if (draft.get("figma") or {}).get("pinned") or (draft.get("figma") or {}).get("direct_group")
        ]
        if direct_scope:
            for draft in direct_scope:
                figma = draft.setdefault("figma", {})
                figma["relevance_score"] = max(int(figma.get("relevance_score") or 0), min_score)
                figma["relevance_reason"] = (
                    "该页面位于用户粘贴的 Figma 直链范围内，本次只按该范围作为 UI 参考"
                    if not figma.get("pinned")
                    else "Figma 链接直接指定该节点，已强制保留为主要 UI 参考"
                )
            selected_ids = {_figma_draft_identity(draft) for draft in direct_scope}
            ignored = [draft for draft in drafts if _figma_draft_identity(draft) not in selected_ids]
            return direct_scope, ignored
    terms = figma_requirement_terms(query_text)
    if not terms:
        direct_scope = [
            draft for draft in drafts
            if (draft.get("figma") or {}).get("pinned") or (draft.get("figma") or {}).get("direct_group")
        ]
        selected = direct_scope[:max(1, min(max_limit, len(direct_scope)))] if direct_scope else drafts[:max(1, min(limit, len(drafts)))]
        for draft in selected:
            figma = draft.setdefault("figma", {})
            figma["relevance_score"] = figma.get("relevance_score", 0)
            figma["relevance_reason"] = (
                "未提供明确需求关键词，但该页面位于 Figma 直链范围内，作为本次 UI 参考保留"
                if figma.get("pinned") or figma.get("direct_group")
                else "未提供明确需求关键词，保留前几个页面作为弱参考"
            )
        selected_ids = {_figma_draft_identity(draft) for draft in selected}
        ignored = [draft for draft in drafts if _figma_draft_identity(draft) not in selected_ids]
        return selected, ignored

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for draft in drafts:
        score, matched = score_figma_draft_for_requirement(draft, query_text)
        figma = draft.setdefault("figma", {})
        node_id = str(figma.get("node_id") or draft.get("page_id") or "")
        pinned = bool(node_id and node_id in pinned_node_ids) or bool(figma.get("pinned"))
        if pinned:
            score = max(score, 99)
            if "链接直指节点" not in matched:
                matched = ["链接直指节点"] + matched
        elif figma.get("direct_group"):
            score = max(score, min_score)
            if "来自直链设计范围" not in matched:
                matched = matched[:8] + ["来自直链设计范围"]
        figma["relevance_score"] = score
        figma["relevance_terms"] = matched
        figma["pinned"] = pinned
        figma["relevance_reason"] = (
            "Figma 链接直接指定该节点，已强制保留为主要 UI 参考"
            if pinned else (
                "该页面位于用户粘贴的 Figma 直链设计范围内，作为本次需求 UI 参考保留"
                if figma.get("direct_group") else (
                    f"匹配需求关键词：{'、'.join(matched[:8])}"
                    if matched else "未匹配到需求关键词，生成时不作为主要 UI 参考"
                )
            )
        )
        scored.append((score, draft))

    top_score = max([score for score, _draft in scored] or [0])
    dynamic_min_score = min_score
    sorted_scored = sorted(scored, key=lambda item: item[0], reverse=True)
    pinned_drafts = [draft for _score, draft in sorted_scored if (draft.get("figma") or {}).get("pinned")]
    pinned_ids = {_figma_draft_identity(draft) for draft in pinned_drafts}
    direct_group_drafts = [
        draft for _score, draft in sorted_scored
        if (draft.get("figma") or {}).get("direct_group")
        and _figma_draft_identity(draft) not in pinned_ids
    ]
    direct_group_ids = {_figma_draft_identity(draft) for draft in direct_group_drafts}
    matched_pairs = [
        (score, draft) for score, draft in sorted_scored
        if score >= dynamic_min_score
        and _figma_draft_identity(draft) not in pinned_ids
        and _figma_draft_identity(draft) not in direct_group_ids
    ]
    matched = [draft for _score, draft in matched_pairs]
    strong_variant_count = 0
    if top_score >= 8:
        strong_threshold = max(dynamic_min_score, int(top_score * 0.75))
        strong_variant_count = len([draft for score, draft in matched_pairs if score >= strong_threshold])
    if pinned_drafts or direct_group_drafts or matched:
        forced_count = len(pinned_drafts) + len(direct_group_drafts)
        selected_limit = min(max_limit, max(limit, strong_variant_count + forced_count))
        direct_limit = max(0, selected_limit - len(pinned_drafts))
        selected_direct = direct_group_drafts[:min(direct_limit, len(direct_group_drafts))]
        remaining_limit = max(0, selected_limit - len(pinned_drafts) - len(selected_direct))
        selected = pinned_drafts + selected_direct + matched[:min(remaining_limit, len(matched))]
    elif fallback_on_no_match:
        selected = [
            draft for _score, draft in sorted(
                scored, key=lambda item: item[0], reverse=True
            )[:max(1, min(2, limit, len(scored)))]
        ]
        for draft in selected:
            figma = draft.setdefault("figma", {})
            figma["relevance_reason"] = "未命中需求关键词，仅作为低置信度兜底参考；建议复制具体 Frame 链接或补充需求说明"
    else:
        selected = []
    selected_ids = {_figma_draft_identity(draft) for draft in selected}
    ignored = [
        draft for _score, draft in scored
        if _figma_draft_identity(draft) not in selected_ids
    ]
    return selected, ignored


def parse_figma_design(data: Dict[str, Any]) -> Dict[str, Any]:
    """Migrated from ``midscene-upload.py:parse_figma_design``."""
    started_at = time.time()
    stage_started_at = started_at
    stage_timing_ms: Dict[str, int] = {}

    def mark_stage(name: str) -> None:
        nonlocal stage_started_at
        now = time.time()
        stage_timing_ms[name] = int((now - stage_started_at) * 1000)
        stage_started_at = now

    figma_url = data.get("figma_url") or data.get("figmaUrl") or data.get("url") or ""
    app_package = (
        data.get("app_package")
        or data.get("appPackage")
        or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    )
    limit = max(1, min(int(data.get("limit") or FIGMA_PARSE_LIMIT), 120))
    mode = data.get("mode") or data.get("parse_mode") or data.get("parseMode") or "smart"
    min_width = max(1, int(data.get("min_width") or data.get("minWidth") or 240))
    min_height = max(1, int(data.get("min_height") or data.get("minHeight") or 360))
    requirement_query = data.get("requirement_query") or data.get("requirementQuery") or ""
    filter_by_requirement = _safe_bool(data.get("filter_by_requirement", data.get("filterByRequirement", True)))
    direct_scope_only = _safe_bool(data.get("direct_scope_only", data.get("directScopeOnly", False)))
    reference_limit = max(1, min(
        int(data.get("reference_limit") or data.get("referenceLimit") or FIGMA_REFERENCE_LIMIT),
        limit, FIGMA_MAX_REFERENCE_LIMIT,
    ))
    max_reference_limit = max(
        reference_limit,
        min(int(data.get("max_reference_limit") or data.get("maxReferenceLimit") or FIGMA_MAX_REFERENCE_LIMIT), limit, 120),
    )
    file_key, node_id = parse_figma_url(figma_url)
    canvas_name = ""
    selected_node_id = node_id
    file_name = ""
    root: Optional[Dict[str, Any]] = None
    if node_id:
        nodes_payload = figma_api_json(f"/files/{urllib.parse.quote(file_key)}/nodes", {"ids": node_id})
        mark_stage("nodes_api")
        node_wrap = (nodes_payload.get("nodes") or {}).get(node_id) or {}
        root = node_wrap.get("document")
        file_name = nodes_payload.get("name") or ""
        if FIGMA_PARENT_LOOKUP or figma_direct_node_needs_parent_lookup(root):
            try:
                payload = figma_api_json(f"/files/{urllib.parse.quote(file_key)}")
                mark_stage("parent_lookup_api")
                document = payload.get("document")
                file_name = payload.get("name") or file_name
                path = figma_find_node_path(document, node_id) if document else []
                if path:
                    canvas_name = figma_canvas_name(path)
                    parent_root = figma_nearest_page_root(path)
                    if parent_root:
                        root = parent_root
                        if root.get("id") != node_id:
                            selected_node_id = root.get("id") or node_id
            except Exception:
                pass
    else:
        payload = figma_api_json(f"/files/{urllib.parse.quote(file_key)}")
        mark_stage("file_api")
        root = payload.get("document")
        file_name = payload.get("name") or ""
    if not root:
        raise ValueError("没有读取到 Figma 节点，请确认链接权限和 node-id 是否正确")
    pinned_node_ids = (
        {node_id, selected_node_id, root.get("id") if isinstance(root, dict) else ""}
        if node_id else set()
    )
    pinned_node_ids = {str(item) for item in pinned_node_ids if str(item or "").strip()}
    if node_id and isinstance(root, dict):
        root["_figma_direct_link"] = True
    if canvas_name:
        root["_figma_canvas_name"] = canvas_name
    candidate_limit = limit
    if requirement_query and filter_by_requirement:
        candidate_limit = max(
            limit, min(120, max(max_reference_limit * 6, reference_limit * 10, 40)),
        )
    frames = figma_frame_candidates(
        root, limit=candidate_limit, mode=mode,
        min_width=min_width, min_height=min_height, pinned_node_ids=pinned_node_ids,
    )
    mark_stage("frame_candidates")
    if not frames:
        raise ValueError("没有找到可导入的页面级 Frame。可以尝试选择更上层节点，或把解析模式改为\"宽松\"。")
    drafts: List[Dict[str, Any]] = []
    for frame in frames:
        drafts.append(figma_frame_to_draft(
            app_package, figma_url, file_key, frame,
            "", frame.get("_figma_canvas_name") or canvas_name,
        ))
    mark_stage("draft_metadata")
    ignored_drafts: List[Dict[str, Any]] = []
    if requirement_query and filter_by_requirement:
        filter_limit = reference_limit
        min_score = max(0, int(data.get("min_relevance_score") or data.get("minRelevanceScore") or 1))
        fallback_on_no_match = bool(node_id and len(drafts) <= 3)
        drafts, ignored_drafts = filter_figma_drafts_for_requirement(
            drafts, requirement_query,
            limit=filter_limit, min_score=min_score,
            fallback_on_no_match=fallback_on_no_match,
            pinned_node_ids=pinned_node_ids, max_limit=max_reference_limit,
            direct_scope_only=direct_scope_only,
        )
        mark_stage("requirement_filter")
    images = figma_image_map(
        file_key,
        [((draft.get("figma") or {}).get("node_id") or "") for draft in drafts],
    )
    mark_stage("image_export")
    download_figma_screenshots(
        drafts, images,
        max_workers=int(data.get("figma_image_workers") or data.get("figmaImageWorkers") or os.getenv("FIGMA_IMAGE_WORKERS", "4") or 4),
    )
    mark_stage("image_download")
    elapsed_ms = int((time.time() - started_at) * 1000)
    return {
        "file_key": file_key,
        "node_id": selected_node_id,
        "original_node_id": node_id,
        "canvas_name": canvas_name,
        "file_name": file_name,
        "app_package": app_package,
        "drafts": drafts,
        "ignored_drafts": ignored_drafts,
        "timing": {
            "elapsed_ms": elapsed_ms,
            "stages_ms": stage_timing_ms,
            "draft_count": len(drafts),
            "ignored_count": len(ignored_drafts),
            "image_count": len([draft for draft in drafts if draft.get("screenshot")]),
        },
    }


def import_figma_design(data: Dict[str, Any]) -> Dict[str, Any]:
    """Migrated from ``midscene-upload.py:import_figma_design``."""
    provided_drafts = data.get("drafts") or []
    selected_ids = set(
        str(item) for item in (data.get("selected_node_ids") or data.get("selectedNodeIds") or [])
        if str(item).strip()
    )
    app_package = (
        data.get("app_package")
        or data.get("appPackage")
        or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    )
    if provided_drafts:
        imported: List[Dict[str, Any]] = []
        for draft in provided_drafts:
            node_id = str((draft.get("figma") or {}).get("node_id") or draft.get("page_id") or "")
            if selected_ids and node_id not in selected_ids:
                continue
            draft["app_package"] = app_package
            imported.append(save_knowledge_page(app_package, draft))
        return {
            "file_key": "",
            "node_id": "",
            "file_name": "",
            "app_package": app_package,
            "imported": imported,
        }

    parsed = parse_figma_design(data)
    imported = []
    for draft in parsed["drafts"]:
        if selected_ids and draft.get("figma", {}).get("node_id") not in selected_ids:
            continue
        imported.append(save_knowledge_page(app_package, draft))
    parsed["imported"] = imported
    parsed["drafts"] = []
    return parsed


def figma_generation_min_relevance() -> int:
    """Migrated from ``midscene-upload.py:figma_generation_min_relevance``."""
    return max(0, env_int("FIGMA_MIN_RELEVANCE_SCORE", 5))


def figma_draft_generation_allowed(
    draft: Dict[str, Any],
    min_score: Optional[int] = None,
) -> bool:
    """Migrated from ``midscene-upload.py:figma_draft_generation_allowed``."""
    figma = (draft or {}).get("figma") or {}
    if figma.get("pinned") or figma.get("direct_group"):
        return True
    if min_score is None:
        min_score = figma_generation_min_relevance()
    return safe_int(figma.get("relevance_score"), 0) >= min_score


def split_generation_figma_drafts(
    drafts: List[Dict[str, Any]],
    min_score: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Migrated from ``midscene-upload.py:split_generation_figma_drafts``."""
    if min_score is None:
        min_score = figma_generation_min_relevance()
    allowed: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []
    for draft in drafts or []:
        if figma_draft_generation_allowed(draft, min_score=min_score):
            allowed.append(draft)
        else:
            figma = draft.setdefault("figma", {})
            figma["relevance_reason"] = (
                figma.get("relevance_reason")
                or f"匹配度低于 {min_score}，不进入本次模型视觉校准"
            )
            ignored.append(draft)
    return allowed, ignored


def figma_drafts_to_generation_assets(
    drafts: List[Dict[str, Any]],
    limit_images: Optional[int] = None,
) -> Tuple[List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Migrated from ``midscene-upload.py:figma_drafts_to_generation_assets``.

    Returns:
        ``(text_assets, image_assets, used_pages)``
    """
    if limit_images is None:
        limit_images = FIGMA_VISUAL_IMAGE_LIMIT
    min_score = figma_generation_min_relevance()
    text_assets: List[str] = []
    image_assets: List[Dict[str, Any]] = []
    used_pages: List[Dict[str, Any]] = []
    for draft in drafts or []:
        if not figma_draft_generation_allowed(draft, min_score=min_score):
            continue
        figma = draft.get("figma") or {}
        page: Dict[str, Any] = {
            "app_package": draft.get("app_package", ""),
            "page_id": draft.get("page_id", ""),
            "page_name": draft.get("page_name", ""),
            "route": draft.get("route", ""),
            "description": draft.get("description", ""),
            "key_elements": _normalize_lines(draft.get("key_elements") or draft.get("keyElements")),
            "common_assertions": _normalize_lines(draft.get("common_assertions") or draft.get("commonAssertions")),
            "tags": _normalize_lines(draft.get("tags")),
            "screenshot": "",
        }
        figma_context = [
            f"节点：{figma.get('node_id', '')}",
            f"设备形态：{figma.get('device_label') or figma.get('device_profile') or '未知'}",
            f"画布尺寸：{figma.get('width', '')}x{figma.get('height', '')}",
            f"状态/变体：{figma.get('variant') or '未标注'}",
            f"相关性：{figma.get('relevance_score', 0)}；{figma.get('relevance_reason', '')}",
            "设计稿要求：生成场景和用例时需要区分不同设备形态、颜色、弹窗状态和可见文案；同一业务点下的多端/多颜色变体不能当作重复用例忽略。",
        ]
        text_assets.append("[Figma设计稿页面]\n" + "\n".join(figma_context) + "\n" + knowledge_page_text(page))
        screenshot = draft.get("screenshot") or {}
        if screenshot.get("contentBase64") and len(image_assets) < limit_images:
            name = clean_asset_filename(screenshot.get("name") or f"figma-{clean_id(page['page_name'])}.png")
            image_assets.append({
                "name": name,
                "mime": _guess_mime(name),
                "base64": screenshot["contentBase64"],
            })
            page["screenshot"] = name
        used_pages.append({
            "source": "figma",
            "app_package": page["app_package"],
            "page_id": page["page_id"],
            "page_name": page["page_name"],
            "route": page["route"],
            "screenshot": page.get("screenshot", ""),
            "image_name": page.get("screenshot", ""),
            "figma": figma,
            "relevance_score": figma.get("relevance_score", 0),
            "relevance_reason": figma.get("relevance_reason", ""),
        })
    return text_assets, image_assets, used_pages


def figma_ignored_draft_summaries(
    drafts: List[Dict[str, Any]],
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """Migrated from ``midscene-upload.py:figma_ignored_draft_summaries``."""
    rows: List[Dict[str, Any]] = []
    for draft in drafts or []:
        figma = draft.get("figma") or {}
        rows.append({
            "source": "figma_ignored",
            "page_id": draft.get("page_id", ""),
            "page_name": draft.get("page_name", ""),
            "route": draft.get("route", ""),
            "figma": {
                "node_id": figma.get("node_id", ""),
                "canvas_name": figma.get("canvas_name", ""),
                "direct_group": bool(figma.get("direct_group")),
                "direct_context": figma.get("direct_context", ""),
                "relevance_score": figma.get("relevance_score", 0),
                "relevance_terms": figma.get("relevance_terms", []),
                "relevance_reason": figma.get("relevance_reason", ""),
            },
        })
        if len(rows) >= limit:
            break
    return rows


def _safe_bool(value: Any, default: bool = False) -> bool:
    """Migrated from ``midscene-upload.py:safe_bool``."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in ("1", "true", "yes", "on", "y", "是"):
        return True
    if text in ("0", "false", "no", "off", "n", "否"):
        return False
    return default


def _first_non_empty(*values: Any) -> str:
    """Migrated from ``midscene-upload.py:first_non_empty``."""
    for v in values:
        v = str(v or "").strip()
        if v:
            return v
    return ""


def _normalize_text_list(value: Any) -> List[str]:
    """Migrated from ``midscene-upload.py:normalize_text_list``."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]
    return []


def _normalize_design_asset_id(raw: str) -> str:
    """Normalize a design asset ID."""
    text = str(raw or "").strip()
    text = re.sub(r"[^\w.-]+", "-", text)
    return text.strip("-._")[:80] or "asset"


def _load_case_ui_design_meta(case_set_id: str) -> Dict[str, Any]:
    """Load UI design meta for a case set."""
    meta_path = os.path.join(LEARNING_DIR, "case-ui-designs", clean_id(case_set_id, "case"), "meta.json")
    return read_json_file(meta_path, default={}) or {}


def _save_case_ui_design_files(
    case_set_id: str,
    files: List[Dict[str, Any]],
    source: str = "figma",
    title: str = "",
    module: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Save UI design files for a case set."""
    from ..storage import write_bytes_file as _write_bytes_file
    meta_dir = os.path.join(LEARNING_DIR, "case-ui-designs", clean_id(case_set_id, "case"))
    os.makedirs(meta_dir, exist_ok=True)
    meta = _load_case_ui_design_meta(case_set_id)
    if not isinstance(meta, dict):
        meta = {}
    designs = meta.get("designs") or []
    saved: List[Dict[str, Any]] = []
    for file_data in files:
        asset_id = file_data.get("asset_id") or unique_millis_id("design")
        name = file_data.get("name") or "design.png"
        content_b64 = file_data.get("contentBase64") or ""
        if not content_b64:
            continue
        try:
            data_bytes = base64.b64decode(content_b64)
        except Exception:
            continue
        _write_bytes_file(os.path.join(meta_dir, clean_asset_filename(name)), data_bytes)
        entry = {
            "asset_id": asset_id,
            "name": name,
            "source": source,
            "page_name": file_data.get("page_name", ""),
            "route": file_data.get("route", ""),
            "description": file_data.get("description", ""),
            "figma": file_data.get("figma") or {},
            "title": title,
            "module": module,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        designs.append(entry)
        saved.append(entry)
    # Keep max 60 designs per case set
    if len(designs) > 60:
        designs = designs[-60:]
    meta["designs"] = designs
    meta["title"] = title or meta.get("title", "")
    meta["module"] = module or meta.get("module", "")
    write_json_file(os.path.join(meta_dir, "meta.json"), meta)
    return saved, meta


def save_figma_design_assets_for_case(
    case_set_id: str,
    drafts: List[Dict[str, Any]],
    title: str = "",
    module: str = "",
) -> List[Dict[str, Any]]:
    """Migrated from ``midscene-upload.py:save_figma_design_assets_for_case``."""
    files: List[Dict[str, Any]] = []
    min_save_score = max(0, env_int("FIGMA_AUTO_SAVE_MIN_RELEVANCE", 5))
    for draft in drafts or []:
        screenshot = draft.get("screenshot") or {}
        content = screenshot.get("contentBase64")
        if not content:
            continue
        figma = draft.get("figma") or {}
        relevance_score = safe_int(figma.get("relevance_score"), 0)
        if not (figma.get("pinned") or figma.get("direct_group")) and relevance_score < min_save_score:
            continue
        node_id = figma.get("node_id") or draft.get("page_id") or draft.get("page_name") or ""
        files.append({
            "asset_id": _normalize_design_asset_id(f"figma-{node_id}"),
            "name": screenshot.get("name") or clean_asset_filename(f"figma-{clean_id(draft.get('page_name') or node_id)}.png"),
            "contentBase64": content,
            "page_name": draft.get("page_name") or figma.get("page_name") or "",
            "route": draft.get("route") or "",
            "description": draft.get("description") or "",
            "figma": {
                **figma,
                "relevance_score": relevance_score,
                "relevance_reason": figma.get("relevance_reason", ""),
                "auto_save_min_relevance": min_save_score,
            },
        })
    if not files:
        return []
    saved, _meta = _save_case_ui_design_files(case_set_id, files, source="figma", title=title, module=module)
    return saved


def load_figma_generation_context(
    data: Dict[str, Any],
    app_package: str,
    job_id: str = "",
    requirement_query: str = "",
    case_set_id: str = "",
    title: str = "",
    module: str = "",
) -> Tuple[List[str], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Migrated from ``midscene-upload.py:load_figma_generation_context``.

    Returns:
        ``(text_assets, image_assets, used_pages, ignored_pages, saved_designs)``
    """
    figma_url = (data.get("figma_url") or data.get("figmaUrl") or "").strip()
    if not figma_url:
        return [], [], [], [], []
    excluded_node_ids = {
        str(item or "").strip()
        for item in (data.get("excluded_figma_node_ids") or data.get("excludedFigmaNodeIds") or [])
        if str(item or "").strip()
    }
    if case_set_id:
        ui_meta = _load_case_ui_design_meta(case_set_id)
        excluded_node_ids |= {
            str(item or "").strip()
            for item in (ui_meta.get("excluded_figma_node_ids") or [])
            if str(item or "").strip()
        }
        excluded_node_ids |= {
            str(item.get("node_id") or item.get("nodeId") or "").strip()
            for item in (ui_meta.get("excluded_figma_nodes") or [])
            if isinstance(item, dict) and str(item.get("node_id") or item.get("nodeId") or "").strip()
        }
    min_relevance_score = safe_int(
        data.get("figma_min_relevance_score")
        or data.get("figmaMinRelevanceScore")
        or os.getenv("FIGMA_MIN_RELEVANCE_SCORE")
        or 5,
        5,
    )
    parsed = parse_figma_design({
        "figma_url": figma_url,
        "app_package": app_package,
        "mode": data.get("figma_mode") or data.get("figmaMode") or "smart",
        "limit": data.get("figma_limit") or data.get("figmaLimit") or FIGMA_PARSE_LIMIT,
        "min_width": data.get("figma_min_width") or data.get("figmaMinWidth") or 240,
        "min_height": data.get("figma_min_height") or data.get("figmaMinHeight") or 360,
        "requirement_query": requirement_query,
        "filter_by_requirement": True,
        "reference_limit": data.get("figma_reference_limit") or data.get("figmaReferenceLimit") or FIGMA_REFERENCE_LIMIT,
        "max_reference_limit": data.get("figma_max_reference_limit") or data.get("figmaMaxReferenceLimit") or FIGMA_MAX_REFERENCE_LIMIT,
        "min_relevance_score": min_relevance_score,
        "direct_scope_only": _safe_bool(data.get("direct_scope_only", data.get("directScopeOnly", False))),
    })
    if excluded_node_ids:
        kept_drafts: List[Dict[str, Any]] = []
        excluded_drafts: List[Dict[str, Any]] = []
        for draft in parsed.get("drafts") or []:
            node_id = str((draft.get("figma") or {}).get("node_id") or draft.get("page_id") or "").strip()
            if node_id and node_id in excluded_node_ids:
                excluded_drafts.append(draft)
            else:
                kept_drafts.append(draft)
        if excluded_drafts:
            for draft in excluded_drafts:
                figma = draft.setdefault("figma", {})
                figma["relevance_reason"] = "该 Figma 页面已被用户删除并加入排除列表，本次不作为参考"
            parsed["drafts"] = kept_drafts
            parsed["ignored_drafts"] = (parsed.get("ignored_drafts") or []) + excluded_drafts
    generation_drafts, low_score_drafts = split_generation_figma_drafts(
        parsed.get("drafts") or [], min_score=min_relevance_score,
    )
    if low_score_drafts:
        parsed["ignored_drafts"] = (parsed.get("ignored_drafts") or []) + low_score_drafts
        parsed["drafts"] = generation_drafts
    text_assets, image_assets, used_pages = figma_drafts_to_generation_assets(generation_drafts)
    saved_designs = (
        save_figma_design_assets_for_case(case_set_id, generation_drafts, title=title, module=module)
        if case_set_id else []
    )
    ignored_pages = figma_ignored_draft_summaries(parsed.get("ignored_drafts") or [])
    return text_assets, image_assets, used_pages, ignored_pages, saved_designs





# ---------------------------------------------------------------------------
# Migrated from midscene-upload.py
# ---------------------------------------------------------------------------

def load_asset_contents(case_set_id, meta):
    asset_root = safe_join(ASSET_DIR, case_set_id)
    text_assets = []
    image_assets = []

    for item in meta.get("files", []):
        name = item.get("name", "")
        path_to_read = safe_join(asset_root, name)
        if is_text_asset(name):
            extracted_path = safe_join(asset_root, f"{name}.extracted.txt")
            read_path = extracted_path if os.path.exists(extracted_path) else path_to_read
            with open(read_path, encoding="utf-8", errors="ignore") as f:
                text_assets.append(f"文件：{name}\n{f.read()[:30000]}")
        elif is_image_file(name):
            with open(path_to_read, "rb") as f:
                image_assets.append({
                    "name": name,
                    "mime": guess_mime(name),
                    "base64": base64.b64encode(f.read()).decode("ascii")
                })

    return text_assets, image_assets



def save_asset_files(case_set_id, title, module, files):
    if not isinstance(files, list) or not files:
        raise ValueError("files 不能为空")

    asset_root = safe_join(ASSET_DIR, case_set_id)
    os.makedirs(asset_root, exist_ok=True)
    saved = []

    for item in files:
        name = clean_asset_filename(item.get("name", "asset.txt"))
        if not supported_asset_file(name):
            raise ValueError(f"不支持的资产格式：{name}")

        content_base64 = item.get("contentBase64")
        content = item.get("content")
        if content_base64:
            data = base64.b64decode(content_base64)
        else:
            data = (content or "").encode("utf-8")

        path_to_save = safe_join(asset_root, name)
        write_bytes_file(path_to_save, data)

        extract_error = ""
        extracted_size = 0
        if not is_image_file(name):
            try:
                extracted = extract_asset_text(path_to_save, name)
                extracted_size = len(extracted)
                if extracted:
                    extracted_path = safe_join(asset_root, f"{name}.extracted.txt")
                    write_text_file(extracted_path, extracted)
            except Exception as e:
                extract_error = str(e)

        saved.append({
            "name": name,
            "mime": guess_mime(name),
            "size": len(data),
            "type": "image" if is_image_file(name) else "text",
            "extracted_size": extracted_size,
            "extract_error": extract_error
        })

    meta = {
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
        "files": saved,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    write_json_file(asset_meta_path(case_set_id), meta)
    return meta



def append_asset_files(case_set_id, title, module, files):
    if not isinstance(files, list) or not files:
        raise ValueError("files 不能为空")
    existing = read_json_file(asset_meta_path(case_set_id), default=None) or {
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
        "files": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    asset_root = safe_join(ASSET_DIR, case_set_id)
    os.makedirs(asset_root, exist_ok=True)
    by_name = {item.get("name"): item for item in existing.get("files", []) if item.get("name")}

    for item in files:
        name = clean_asset_filename(item.get("name", "asset.txt"))
        if not supported_asset_file(name):
            raise ValueError(f"不支持的资产格式：{name}")
        content_base64 = item.get("contentBase64")
        content = item.get("content")
        data = base64.b64decode(content_base64) if content_base64 else (content or "").encode("utf-8")
        path_to_save = safe_join(asset_root, name)
        write_bytes_file(path_to_save, data)
        extract_error = ""
        extracted_size = 0
        if not is_image_file(name):
            try:
                extracted = extract_asset_text(path_to_save, name)
                extracted_size = len(extracted)
                if extracted:
                    write_text_file(safe_join(asset_root, f"{name}.extracted.txt"), extracted)
            except Exception as e:
                extract_error = str(e)
        by_name[name] = {
            "name": name,
            "mime": guess_mime(name),
            "size": len(data),
            "type": "image" if is_image_file(name) else "text",
            "extracted_size": extracted_size,
            "extract_error": extract_error,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

    existing["title"] = title or existing.get("title")
    existing["module"] = module or existing.get("module")
    existing["files"] = list(by_name.values())
    existing["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json_file(asset_meta_path(case_set_id), existing)
    return existing



def knowledge_app_dir(app_package):
    return safe_join(KNOWLEDGE_DIR, clean_id(app_package, DEFAULT_APP_PACKAGE))



def knowledge_meta_path(app_package, page_id):
    return safe_join(knowledge_page_dir(app_package, page_id), "meta.json")



def knowledge_page_dir(app_package, page_id):
    return safe_join(knowledge_app_dir(app_package), clean_id(page_id, "page"))


def _task_app_map_by_package():
    """Lazy import app bindings to avoid depending on legacy globals."""
    try:
        from .job_service import task_app_map_by_package
        return task_app_map_by_package()
    except Exception:
        return {}



def list_knowledge_app_details():
    app_map = _task_app_map_by_package()
    packages = set(list_knowledge_apps()) | set(app_map.keys())
    details = []
    for package in sorted(packages):
        app = app_map.get(package) or {}
        pages = list_knowledge_pages(package)
        details.append({
            "package": package,
            "name": app.get("name") or package,
            "modules": app.get("modules") or [],
            "page_count": len(pages),
            "test_count": len([page for page in pages if page.get("tier") != "baseline"]),
            "baseline_count": len([page for page in pages if page.get("tier") == "baseline"]),
            "has_knowledge": bool(pages),
            "source": "task-apps+knowledge" if app and pages else ("task-apps" if app else "knowledge")
        })
    return details


def normalize_knowledge_tier(value, default="test"):
    """将知识库层级值规范化为 'baseline' 或 'test'。"""
    tier = str(value or default or "test").strip().lower()
    mapping = {
        "baseline": "baseline",
        "base": "baseline",
        "stable": "baseline",
        "基线": "baseline",
        "基线库": "baseline",
        "test": "test",
        "testing": "test",
        "draft": "test",
        "测试": "test",
        "测试库": "test",
        "草稿": "test",
    }
    return mapping.get(tier, "test")


def list_knowledge_pages(app_package, tier="all"):
    app_dir = knowledge_app_dir(app_package)
    if not os.path.exists(app_dir):
        return []
    tier = normalize_knowledge_tier(tier, "") if tier and tier != "all" else "all"
    pages = []
    for page_id in sorted(os.listdir(app_dir)):
        meta = read_json_file(knowledge_meta_path(app_package, page_id), default=None)
        if meta:
            meta["tier"] = normalize_knowledge_tier(meta.get("tier"), "test")
            if tier != "all" and meta["tier"] != tier:
                continue
            pages.append(meta)
    return pages



def save_knowledge_page(data):
    app_package = data.get("app_package") or data.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    page_name = (data.get("page_name") or data.get("pageName") or "未命名页面").strip()
    page_id = clean_id(data.get("page_id") or data.get("pageId") or page_name)
    page_dir = knowledge_page_dir(app_package, page_id)
    os.makedirs(page_dir, exist_ok=True)

    screenshot = data.get("screenshot") or {}
    screenshot_name = ""
    if screenshot.get("contentBase64"):
        raw_name = clean_asset_filename(screenshot.get("name") or f"{page_id}.png")
        if not is_image_file(raw_name):
            raise ValueError("页面截图只支持 png / jpg / jpeg")
        screenshot_name = raw_name
        write_bytes_file(safe_join(page_dir, screenshot_name), base64.b64decode(screenshot["contentBase64"]))

    existed = read_json_file(knowledge_meta_path(app_package, page_id), default={}) or {}
    if not screenshot_name:
        screenshot_name = existed.get("screenshot", "")

    meta = {
        "app_package": app_package,
        "page_id": page_id,
        "page_name": page_name,
        "route": data.get("route", ""),
        "description": data.get("description", ""),
        "key_elements": normalize_lines(data.get("key_elements") or data.get("keyElements")),
        "common_assertions": normalize_lines(data.get("common_assertions") or data.get("commonAssertions")),
        "tags": normalize_lines(data.get("tags")),
        "tier": normalize_knowledge_tier(data.get("tier") or data.get("library") or existed.get("tier"), "test"),
        "screenshot": screenshot_name,
        "source": data.get("source") or ("figma" if data.get("figma") else existed.get("source", "manual")),
        "figma": data.get("figma") or existed.get("figma") or {},
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at": existed.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S")
    }
    write_json_file(knowledge_meta_path(app_package, page_id), meta)
    return meta



def update_asset_request_context(case_set_id, context=None):
    context = context or {}
    try:
        meta = read_json_file(asset_meta_path(case_set_id), default=None) or {
            "case_set_id": case_set_id,
            "files": []
        }
    except Exception:
        meta = {"case_set_id": case_set_id, "files": []}
    figma_url = (context.get("figma_url") or context.get("figmaUrl") or "").strip()
    if figma_url:
        meta["figma_url"] = figma_url
    for key in ("figma_mode", "figmaMode", "figma_limit", "figmaLimit", "figma_reference_limit", "figmaReferenceLimit"):
        if context.get(key) not in (None, ""):
            meta[key] = context.get(key)
    for key in ("app_package", "appPackage", "knowledge_tier", "knowledgeTier"):
        if context.get(key) not in (None, ""):
            meta[key] = context.get(key)
    page_ids = context.get("knowledge_page_ids") or context.get("knowledgePageIds")
    if page_ids:
        meta["knowledge_page_ids"] = page_ids
    meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json_file(asset_meta_path(case_set_id), meta)
    return meta



def asset_meta_path(case_set_id):
    return safe_join(ASSET_DIR, case_set_id, "meta.json")
