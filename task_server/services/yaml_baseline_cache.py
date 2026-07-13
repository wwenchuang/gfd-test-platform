"""Cached baseline YAML index for generated YAML prompting.

The generator needs examples from the maintained YAML library, but reading and
parsing every YAML file during each generation makes Agent runs slow and noisy.
This module builds a compact cache once, keeps it in memory, persists it on
disk, and serves Top-N similar baseline snippets for prompting.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Tuple

from task_server.config import TASK_DIR, TASK_META_FILE, env_int, safe_int
from task_server.schemas import MIDSCENE_FLOW_ACTIONS
from task_server.storage import clean_filename, read_json_file, read_text_file, write_json_file


CACHE_VERSION = 2
YAML_BASELINE_CACHE_TTL_SECONDS = max(30, env_int("MIDSCENE_YAML_BASELINE_CACHE_TTL_SECONDS", 600))
YAML_BASELINE_CACHE_MAX_FILES = max(50, env_int("MIDSCENE_YAML_BASELINE_CACHE_MAX_FILES", 1200))
YAML_BASELINE_CACHE_SNIPPET_CHARS = max(600, env_int("MIDSCENE_YAML_BASELINE_CACHE_SNIPPET_CHARS", 2400))
YAML_BASELINE_SEARCH_MAX_LIMIT = max(3, env_int("MIDSCENE_YAML_BASELINE_SEARCH_MAX_LIMIT", 20))
YAML_BASELINE_CACHE_PATH = os.getenv(
    "MIDSCENE_YAML_BASELINE_CACHE_PATH",
    os.path.join(TASK_DIR, "cache", "yaml-baseline-cache.json"),
)

_CACHE_LOCK = threading.Lock()
_MEMORY_CACHE: Dict[str, Any] | None = None
_MEMORY_CACHE_AT = 0.0
_LAST_STATUS: Dict[str, Any] = {
    "cacheHit": False,
    "cacheSource": "cold_start",
    "elapsedMs": 0,
    "cachePath": YAML_BASELINE_CACHE_PATH,
}

_STOPWORDS = {
    "测试", "验证", "页面", "功能", "需求", "用例", "执行", "当前", "进行", "是否", "可以", "需要",
    "点击", "进入", "打开", "显示", "相关", "流程", "按钮", "模块", "状态", "结果", "完成", "成功",
    "失败", "检查", "确认", "一个", "这个", "那个", "用户", "操作", "场景", "自动化", "生成",
}


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _fallback_cache_path() -> str:
    return os.path.join(_repo_root(), "task-data", "cache", "yaml-baseline-cache.json")


def _cache_paths() -> List[str]:
    paths = [YAML_BASELINE_CACHE_PATH]
    fallback = _fallback_cache_path()
    if fallback not in paths:
        paths.append(fallback)
    return paths


def _set_status(**updates: Any) -> None:
    _LAST_STATUS.update(updates)


def _baseline_roots() -> List[str]:
    repo_root = _repo_root()
    candidates = [
        TASK_DIR,
        os.path.join(repo_root, "server-tasks"),
        os.path.join(repo_root, "server-tasks-all"),
        "/opt/midscene-task-platform/server-tasks",
        "/opt/midscene-task-platform/server-tasks-all",
    ]
    roots: List[str] = []
    seen = set()
    for root in candidates:
        path = os.path.abspath(str(root or ""))
        if not path or path in seen or not os.path.isdir(path):
            continue
        seen.add(path)
        roots.append(path)
    return roots


def _iter_yaml_files(max_files: int | None = None) -> Iterable[Tuple[str, str, str, os.stat_result]]:
    max_files = safe_int(max_files, YAML_BASELINE_CACHE_MAX_FILES)
    count = 0
    for root in _baseline_roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name for name in dirnames
                if not name.startswith(".") and name not in ("node_modules", "__pycache__")
            ]
            for filename in sorted(filenames):
                if filename.startswith(".") or not filename.lower().endswith((".yaml", ".yml")):
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(path)
                    rel = os.path.relpath(path, root).replace("\\", "/")
                except Exception:
                    continue
                yield root, rel, path, stat
                count += 1
                if count >= max_files:
                    return


def calc_baseline_fingerprint() -> Dict[str, Any]:
    """Return a metadata-only fingerprint for maintained baseline YAML files."""
    started = time.time()
    rows = []
    for root, rel, _path, stat in _iter_yaml_files():
        rows.append(f"{root}|{rel}|{int(stat.st_mtime_ns)}|{int(stat.st_size)}")
    digest = hashlib.sha1("\n".join(sorted(rows)).encode("utf-8", "ignore")).hexdigest()
    return {
        "fingerprint": f"sha1_{digest}",
        "fileCount": len(rows),
        "roots": _baseline_roots(),
        "elapsedMs": int((time.time() - started) * 1000),
    }


def _terms(text: Any, limit: int = 120) -> List[str]:
    raw = str(text or "").lower()
    result: List[str] = []
    seen = set()

    def add(value: str) -> None:
        value = str(value or "").strip().lower()
        if len(value) < 2 or value in _STOPWORDS or value in seen:
            return
        seen.add(value)
        result.append(value)

    for match in re.finditer(r"[A-Za-z0-9_./-]+|[\u4e00-\u9fff]+", raw):
        token = match.group(0).strip()
        if not token:
            continue
        if re.fullmatch(r"[A-Za-z0-9_./-]+", token):
            add(token)
        else:
            add(token)
            if len(token) >= 4:
                for size in (4, 3, 2):
                    for idx in range(0, len(token) - size + 1):
                        add(token[idx:idx + size])
                        if len(result) >= limit:
                            return result
        if len(result) >= limit:
            return result
    return result[:limit]


def _yaml_blocks(text: str) -> List[str]:
    lines = (text or "").splitlines()
    starts = [idx for idx, line in enumerate(lines) if re.match(r"^\s*-\s+name:\s*.+", line)]
    if not starts:
        return []
    blocks = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        if block:
            blocks.append(block)
    return blocks


def _clean_scalar(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] in ("'", '"') and text[-1] == text[0]:
        text = text[1:-1]
    return text.replace('\\"', '"').strip()


def _block_title(block: str, fallback: str) -> str:
    match = re.search(r"^\s*-\s+name:\s*(.+?)\s*$", block or "", flags=re.M)
    return _clean_scalar(match.group(1)) if match else fallback


def _block_actions(block: str) -> List[str]:
    actions: List[str] = []
    for match in re.finditer(r"^\s*-\s*([A-Za-z][A-Za-z0-9_]*)\s*:", block or "", flags=re.M):
        action = match.group(1)
        if action in MIDSCENE_FLOW_ACTIONS and action not in actions:
            actions.append(action)
    return actions[:24]


def _baseline_path(block: str) -> str:
    match = re.search(r"#\s*baseline\.path\s*:\s*(.+)", block or "")
    return match.group(1).strip() if match else ""


def _snippet(block: str) -> str:
    kept = []
    for line in (block or "").splitlines():
        text = line.rstrip()
        if not text:
            continue
        kept.append(text)
        if len(kept) >= 48:
            break
    result = "\n".join(kept).strip()
    if len(result) > YAML_BASELINE_CACHE_SNIPPET_CHARS:
        result = result[:YAML_BASELINE_CACHE_SNIPPET_CHARS].rstrip() + "\n  # ... 已截断，仅保留关键步骤参考"
    return result


def _business_path(block: str) -> str:
    path = _baseline_path(block)
    if path:
        return path
    labels = []
    for match in re.finditer(r"^\s*-\s*(aiWaitFor|aiTap|ai|aiInput|aiAssert)\s*:\s*(.+?)\s*$", block or "", flags=re.M):
        label = _clean_scalar(match.group(2))
        if label:
            labels.append(label[:50])
        if len(labels) >= 5:
            break
    return " -> ".join(labels)


def _load_task_meta() -> Dict[str, Any]:
    data = read_json_file(TASK_META_FILE, default={})
    return data if isinstance(data, dict) else {}


def _task_meta_row(task_meta: Dict[str, Any], module: str, rel_path: str) -> Dict[str, Any]:
    filename = os.path.basename(rel_path)
    candidates = [
        f"{module}::{clean_filename(filename)}",
        f"{module}::{clean_filename(rel_path)}",
        f"{module}/{filename}",
        f"{module}/{rel_path}",
    ]
    for key in candidates:
        row = task_meta.get(key)
        if isinstance(row, dict):
            return row
    return {}


def _last_run_status(row: Dict[str, Any]) -> str:
    raw = str(row.get("last_status") or row.get("lastStatus") or row.get("status") or "").strip().lower()
    if raw in ("success", "passed", "pass"):
        return "success"
    if raw in ("failed", "failure", "error"):
        return "failed"
    if raw in ("running", "pending", "timeout", "cancelled", "draft", "baseline", "active"):
        return raw
    return "unknown"


def _failure_rate(row: Dict[str, Any], last_status: str) -> float:
    for key in ("failureRate", "failure_rate", "recentFailureRate"):
        if key in row:
            try:
                return max(0.0, min(1.0, float(row.get(key) or 0)))
            except Exception:
                pass
    if last_status == "failed":
        return 1.0
    if last_status == "success":
        return 0.0
    return 0.0


def _meta_truthy(row: Dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if str(value or "").strip().lower() in ("1", "true", "yes", "approved", "verified", "baseline"):
            return True
    return False


def _baseline_source_info(root: str, rel_path: str, meta_row: Dict[str, Any], last_status: str) -> Dict[str, Any]:
    """Classify provenance before a YAML block is allowed to teach the model."""
    normalized_root = os.path.abspath(str(root or "")).replace("\\", "/").rstrip("/")
    root_name = os.path.basename(normalized_root).lower()
    maintained_library = root_name == "server-tasks-all" or normalized_root.endswith("/server-tasks-all")
    execution_verified = last_status == "success"
    metadata_verified = _meta_truthy(
        meta_row,
        "baselineUsable", "baseline_usable", "approved", "verified", "isBaseline", "is_baseline",
    )
    if execution_verified:
        source_kind = "verified_execution"
        verification_status = "execution_success"
        trust_score = 100
    elif maintained_library:
        source_kind = "maintained_library"
        verification_status = "maintained"
        trust_score = 80
    elif metadata_verified:
        source_kind = "approved_runtime"
        verification_status = "metadata_verified"
        trust_score = 70
    else:
        source_kind = "working_copy"
        verification_status = "unverified"
        trust_score = 10
    usable = bool(execution_verified or maintained_library or metadata_verified)
    return {
        "sourceKind": source_kind,
        "sourceRoot": root_name or normalized_root,
        "provenancePath": "/".join(filter(None, [root_name, str(rel_path or "").replace("\\", "/")])),
        "verificationStatus": verification_status,
        "sourceTrust": trust_score,
        "trusted": usable,
        "baselineUsable": usable,
    }


def _cache_item(root: str, rel_path: str, path: str, stat: os.stat_result, block: str, task_meta: Dict[str, Any]) -> Dict[str, Any] | None:
    actions = _block_actions(block)
    if not actions:
        return None
    module = rel_path.split("/", 1)[0] if "/" in rel_path else ""
    meta_row = _task_meta_row(task_meta, module, rel_path)
    last_status = _last_run_status(meta_row)
    title = _block_title(block, os.path.splitext(os.path.basename(path))[0])
    business_path = _business_path(block)
    snippet = _snippet(block)
    source_info = _baseline_source_info(root, rel_path, meta_row, last_status)
    item_id = hashlib.sha1(f"{root}|{rel_path}|{block}".encode("utf-8", "ignore")).hexdigest()[:16]
    keyword_text = "\n".join([title, module, rel_path, business_path, snippet[:1000]])
    return {
        "id": item_id,
        "title": title,
        "module": module,
        "file": rel_path,
        "path": path,
        "mtime": int(stat.st_mtime),
        "size": int(stat.st_size),
        "keywords": _terms(keyword_text, limit=60),
        "actions": actions,
        "businessPath": business_path,
        "baseline_path": _baseline_path(block),
        "snippet": snippet,
        **source_info,
        "lastRunStatus": last_status,
        "failureRate": _failure_rate(meta_row, last_status),
        "hash": item_id,
    }


def build_yaml_baseline_cache() -> Dict[str, Any]:
    """Scan baseline YAML once and persist compact prompt-ready examples."""
    started = time.time()
    fingerprint_info = calc_baseline_fingerprint()
    items: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    task_meta = _load_task_meta()
    for root, rel_path, path, stat in _iter_yaml_files():
        text = read_text_file(path, default="")
        if not text or "tasks:" not in text:
            continue
        for block in _yaml_blocks(text):
            block_hash = hashlib.sha1(block.encode("utf-8", "ignore")).hexdigest()
            item = _cache_item(root, rel_path, path, stat, block, task_meta)
            if not item:
                continue
            existing_index = seen.get(block_hash)
            if existing_index is not None:
                existing = items[existing_index]
                if safe_int(item.get("sourceTrust"), 0) > safe_int(existing.get("sourceTrust"), 0):
                    items[existing_index] = item
                continue
            seen[block_hash] = len(items)
            items.append(item)
    cache = {
        "version": CACHE_VERSION,
        "generatedAt": int(time.time()),
        "generatedAtText": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fingerprint": fingerprint_info.get("fingerprint"),
        "fileCount": fingerprint_info.get("fileCount", 0),
        "caseCount": len(items),
        "roots": fingerprint_info.get("roots") or [],
        "items": items,
        "elapsedMs": int((time.time() - started) * 1000),
    }
    save_yaml_baseline_cache(cache)
    return cache


def save_yaml_baseline_cache(cache: Dict[str, Any]) -> None:
    last_error = ""
    for path in _cache_paths():
        try:
            write_json_file(path, cache or {})
            _set_status(cachePath=path, cacheWriteError="")
            return
        except Exception as exc:
            last_error = str(exc)
            continue
    _set_status(cachePath=YAML_BASELINE_CACHE_PATH, cacheWriteError=last_error)


def load_yaml_baseline_cache() -> Dict[str, Any] | None:
    for path in _cache_paths():
        try:
            data = read_json_file(path, default=None)
        except Exception:
            data = None
        if isinstance(data, dict):
            _set_status(cachePath=path)
            return data
    return None


def _fingerprint_matches(cache: Dict[str, Any], fingerprint_info: Dict[str, Any]) -> bool:
    return (
        cache.get("version") == CACHE_VERSION
        and cache.get("fingerprint")
        and cache.get("fingerprint") == fingerprint_info.get("fingerprint")
    )


def get_yaml_baseline_cache(force: bool = False) -> Dict[str, Any]:
    """Return baseline cache from memory, disk, or a fresh rebuild."""
    global _MEMORY_CACHE, _MEMORY_CACHE_AT, _LAST_STATUS
    started = time.time()
    with _CACHE_LOCK:
        now = time.time()
        if (
            not force
            and _MEMORY_CACHE
            and now - _MEMORY_CACHE_AT <= YAML_BASELINE_CACHE_TTL_SECONDS
        ):
            _set_status(cacheHit=True, cacheSource="memory", elapsedMs=int((time.time() - started) * 1000))
            return _MEMORY_CACHE

        fingerprint_info = calc_baseline_fingerprint()
        disk_cache = None if force else load_yaml_baseline_cache()
        if disk_cache and _fingerprint_matches(disk_cache, fingerprint_info):
            _MEMORY_CACHE = disk_cache
            _MEMORY_CACHE_AT = now
            _set_status(cacheHit=True, cacheSource="disk", elapsedMs=int((time.time() - started) * 1000))
            return disk_cache

        cache = build_yaml_baseline_cache()
        _MEMORY_CACHE = cache
        _MEMORY_CACHE_AT = time.time()
        _set_status(cacheHit=False, cacheSource="rebuilt", elapsedMs=int((time.time() - started) * 1000))
        return cache


def _score_item(query_terms: List[str], module: str, item: Dict[str, Any]) -> Tuple[int, List[str]]:
    score = 0
    matched: List[str] = []
    title = str(item.get("title") or "").lower()
    item_module = str(item.get("module") or "").lower()
    business_path = str(item.get("businessPath") or item.get("baseline_path") or "").lower()
    file = str(item.get("file") or "").lower()
    snippet = str(item.get("snippet") or "")[:1800].lower()
    keywords = {str(term).lower() for term in (item.get("keywords") or [])}
    for term in query_terms or []:
        term = str(term or "").lower()
        if not term:
            continue
        term_score = 0
        if term in title:
            term_score += 4
        if term in item_module:
            term_score += 5
        if term in business_path:
            term_score += 4
        if term in file:
            term_score += 3
        if term in keywords:
            term_score += 3
        if term in snippet:
            term_score += 1
        if term_score:
            score += term_score
            matched.append(term)
    module_text = str(module or "").strip().lower()
    if module_text and (module_text in item_module or module_text in file):
        score += 5
        matched.append(module_text)
    if not matched:
        return 0, []
    if item.get("baselineUsable") is True:
        score += 8
    if item.get("lastRunStatus") in ("passed", "success"):
        score += 10
    score += max(0, min(10, safe_int(item.get("sourceTrust"), 0) // 10))
    try:
        failure_rate = float(item.get("failureRate") or 0)
    except Exception:
        failure_rate = 0
    if failure_rate >= 0.5:
        score -= 15
    elif failure_rate >= 0.2:
        score -= 6
    actions = item.get("actions") or []
    if "aiWaitFor" in actions:
        score += 2
    if "aiAssert" in actions:
        score += 2
    return score, matched[:12]


def search_baseline_examples(
    query_text: Any,
    module: str = "",
    limit: int = 3,
    allow_fallback: bool = False,
    trusted_only: bool = True,
) -> List[Dict[str, Any]]:
    """Search cached baseline snippets and return prompt-ready Top-N examples."""
    limit = max(1, min(YAML_BASELINE_SEARCH_MAX_LIMIT, safe_int(limit, 3)))
    started = time.time()
    cache = get_yaml_baseline_cache(force=False)
    query_terms = _terms(query_text, limit=120)
    scored = []
    for item in cache.get("items") or []:
        if not isinstance(item, dict):
            continue
        if trusted_only and (item.get("baselineUsable") is not True or item.get("trusted") is not True):
            continue
        score, matched = _score_item(query_terms, module, item)
        if score <= 0 and not allow_fallback:
            continue
        row = dict(item)
        row["score"] = score
        row["matched_terms"] = matched or ["基线缓存兜底"]
        row["cache_hit"] = bool(_LAST_STATUS.get("cacheHit"))
        scored.append(row)
    scored.sort(key=lambda row: (safe_int(row.get("score"), 0), row.get("title") or "", row.get("file") or ""), reverse=True)
    _LAST_STATUS.update({
        "matchedCount": len(scored[:limit]),
        "searchElapsedMs": int((time.time() - started) * 1000),
    })
    return scored[:limit]


def get_yaml_baseline_cache_status(force: bool = False) -> Dict[str, Any]:
    cache = get_yaml_baseline_cache(force=force)
    items = [item for item in (cache.get("items") or []) if isinstance(item, dict)]
    trusted_count = sum(1 for item in items if item.get("baselineUsable") is True and item.get("trusted") is True)
    status = {
        "ok": True,
        "cacheHit": bool(_LAST_STATUS.get("cacheHit")),
        "cacheSource": _LAST_STATUS.get("cacheSource"),
        "fileCount": cache.get("fileCount", 0),
        "caseCount": cache.get("caseCount", 0),
        "trustedCaseCount": trusted_count,
        "excludedUnverifiedCount": max(0, len(items) - trusted_count),
        "generatedAt": cache.get("generatedAt"),
        "generatedAtText": cache.get("generatedAtText"),
        "fingerprint": cache.get("fingerprint"),
        "path": _LAST_STATUS.get("cachePath") or YAML_BASELINE_CACHE_PATH,
        "configuredPath": YAML_BASELINE_CACHE_PATH,
        "ttlSeconds": YAML_BASELINE_CACHE_TTL_SECONDS,
        "elapsedMs": _LAST_STATUS.get("elapsedMs", 0),
        "matchedCount": _LAST_STATUS.get("matchedCount", 0),
        "searchElapsedMs": _LAST_STATUS.get("searchElapsedMs", 0),
        "cacheWriteError": _LAST_STATUS.get("cacheWriteError", ""),
    }
    return status
