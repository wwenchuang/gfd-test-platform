"""报告索引服务。

管理 ``report-index.json`` 文件，提供报告列表查询、索引重建、
新增报告与移除报告等操作。

索引文件路径：``$LEARNING_DIR/report-index.json``
索引结构::

    {
        "reports": [
            {
                "reportId": "rpt_xxx",
                "jobId": "job_xxx",
                "module": "3D打印基线",
                "file": "关节龙打印.yaml",
                "status": "success",
                "reportUrl": "/reports/xxx/index.html",
                "createdAt": "2026-01-01 12:00:00",
                "summary": "...",
            },
            ...
        ],
        "updatedAt": "2026-01-01 12:00:00"
    }
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import html as html_lib

from ..config import (
    LEARNING_DIR,
    REPORT_DIR,
    REPORT_CLEANUP_INTERVAL_SECONDS,
    REPORT_CLEANUP_ON_STARTUP,
    REPORT_RETENTION_DAYS,
    REPORT_RETENTION_MIN_KEEP,
    safe_int,
)
from ..storage import (
    clean_id,
    read_json_file,
    unique_millis_id,
    write_json_file,
)

# ---------------------------------------------------------------------------
# 索引文件路径
# ---------------------------------------------------------------------------

REPORT_INDEX_FILE = os.path.join(LEARNING_DIR, "report-index.json")


# ---------------------------------------------------------------------------
# 索引读写
# ---------------------------------------------------------------------------

def _load_index() -> Dict[str, Any]:
    """加载报告索引，保证返回结构合法。"""
    data = read_json_file(REPORT_INDEX_FILE, default={"reports": [], "updatedAt": ""})
    if not isinstance(data, dict):
        data = {"reports": [], "updatedAt": ""}
    if not isinstance(data.get("reports"), list):
        data["reports"] = []
    return data


def _save_index(index: Dict[str, Any]) -> None:
    """保存报告索引（原子写入）。"""
    index["updatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json_file(REPORT_INDEX_FILE, index)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def list_reports(
    limit: int = 50,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """从索引读取报告列表。

    Args:
        limit: 最大返回条数，默认 50。
        status: 可选，按状态过滤（success / failed / timeout 等）。

    Returns:
        报告列表，按 ``createdAt`` 倒序。
    """
    index = _load_index()
    reports: List[Dict[str, Any]] = index.get("reports") or []

    if status:
        status = str(status).strip().lower()
        reports = [
            r for r in reports
            if str(r.get("status", "")).lower() == status
        ]

    # 按 createdAt 倒序
    reports.sort(
        key=lambda r: str(r.get("createdAt") or ""),
        reverse=True,
    )

    limit = max(1, min(500, int(limit or 50)))
    return reports[:limit]


def rebuild_index() -> Dict[str, Any]:
    """扫描报告目录重建索引。

    遍历 ``REPORT_DIR`` 下的子目录，将每个包含 ``index.html`` 或
    ``report.json`` 的目录识别为一个报告，重建索引文件。

    Returns:
        重建结果摘要，包含 ``total`` 和 ``errors`` 字段。
    """
    reports: List[Dict[str, Any]] = []
    errors: List[str] = []

    try:
        entries = sorted(os.listdir(REPORT_DIR))
    except Exception:
        entries = []

    for entry in entries:
        entry_path = os.path.join(REPORT_DIR, entry)
        if not os.path.isdir(entry_path):
            continue
        if entry.startswith(".") or entry.startswith("_"):
            continue

        # 检查是否为有效报告目录
        has_index = os.path.exists(os.path.join(entry_path, "index.html"))
        has_report_json = os.path.exists(os.path.join(entry_path, "report.json"))

        if not has_index and not has_report_json:
            continue

        # 尝试从 report.json 读取元数据
        report_meta: Dict[str, Any] = {}
        if has_report_json:
            try:
                report_meta = read_json_file(
                    os.path.join(entry_path, "report.json"),
                    default={},
                )
                if not isinstance(report_meta, dict):
                    report_meta = {}
            except Exception as exc:
                errors.append(f"{entry}: {exc}")

        # 构建索引条目
        report_id = str(
            report_meta.get("reportId") or report_meta.get("report_id") or entry
        ).strip()
        report: Dict[str, Any] = {
            "reportId": report_id,
            "report_id": report_id,
            "jobId": str(report_meta.get("jobId") or report_meta.get("job_id") or "").strip(),
            "module": str(report_meta.get("module") or "").strip(),
            "file": str(report_meta.get("file") or "").strip(),
            "status": str(report_meta.get("status") or "unknown").strip().lower(),
            "reportUrl": f"/reports/{entry}/index.html",
            "createdAt": str(
                report_meta.get("createdAt") or report_meta.get("created_at") or ""
            ).strip(),
            "summary": str(report_meta.get("summary") or "").strip()[:500],
        }
        reports.append(report)

    # 按创建时间倒序
    reports.sort(
        key=lambda r: str(r.get("createdAt") or ""),
        reverse=True,
    )

    index = {"reports": reports}
    _save_index(index)

    return {
        "ok": True,
        "total": len(reports),
        "errors": errors,
    }


def append_report_to_index(report: Dict[str, Any]) -> Dict[str, Any]:
    """新增报告到索引。

    若 ``reportId`` 已存在则更新，否则追加到列表头部。

    Args:
        report: 报告字典，需包含 ``reportId`` 或 ``report_id``。

    Returns:
        写入后的报告条目。
    """
    if not isinstance(report, dict):
        raise ValueError("report 必须是 dict")

    report_id = str(
        report.get("reportId") or report.get("report_id") or ""
    ).strip()
    if not report_id:
        report_id = unique_millis_id("rpt")

    entry: Dict[str, Any] = {
        "reportId": report_id,
        "report_id": report_id,
        "jobId": str(report.get("jobId") or report.get("job_id") or "").strip(),
        "module": str(report.get("module") or "").strip(),
        "file": str(report.get("file") or "").strip(),
        "status": str(report.get("status") or "unknown").strip().lower(),
        "reportUrl": str(report.get("reportUrl") or report.get("report_url") or "").strip(),
        "createdAt": str(
            report.get("createdAt") or report.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S")
        ).strip(),
        "summary": str(report.get("summary") or "").strip()[:500],
    }

    index = _load_index()
    reports: List[Dict[str, Any]] = index.get("reports") or []

    # 更新或追加
    replaced = False
    for idx, item in enumerate(reports):
        if item.get("reportId") == report_id or item.get("report_id") == report_id:
            reports[idx] = entry
            replaced = True
            break
    if not replaced:
        reports.insert(0, entry)

    # 限制索引大小
    if len(reports) > 1000:
        reports = reports[:1000]

    index["reports"] = reports
    _save_index(index)
    return entry


def remove_report_from_index(report_id: str) -> bool:
    """从索引中移除报告。

    Args:
        report_id: 报告 ID。

    Returns:
        是否成功移除（``True`` 表示找到并移除）。
    """
    report_id = str(report_id or "").strip()
    if not report_id:
        return False

    index = _load_index()
    reports: List[Dict[str, Any]] = index.get("reports") or []
    original_len = len(reports)
    reports = [
        r for r in reports
        if r.get("reportId") != report_id and r.get("report_id") != report_id
    ]
    if len(reports) == original_len:
        return False

    index["reports"] = reports
    _save_index(index)
    return True


def get_report_stats() -> Dict[str, Any]:
    """获取报告统计摘要。

    Returns:
        包含总数、成功数、失败数、失败率和最近运行时间的字典。
    """
    index = _load_index()
    reports: List[Dict[str, Any]] = index.get("reports") or []

    total = len(reports)
    success = sum(
        1 for r in reports
        if str(r.get("status", "")).lower() == "success"
    )
    failed = sum(
        1 for r in reports
        if str(r.get("status", "")).lower() in ("failed", "failure", "error", "timeout")
    )
    fail_rate = f"{failed / total * 100:.1f}%" if total > 0 else "0%"

    # 最近运行时间：取 updatedAt 或报告中最晚的 createdAt
    recent_time = str(index.get("updatedAt") or "")
    if not recent_time:
        sorted_reports = sorted(
            reports,
            key=lambda r: str(r.get("createdAt") or ""),
            reverse=True,
        )
        if sorted_reports:
            recent_time = str(sorted_reports[0].get("createdAt") or "")

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "failRate": fail_rate,
        "recentRunTime": recent_time,
    }


__all__ = [
    "REPORT_INDEX_FILE",
    "list_reports",
    "rebuild_index",
    "append_report_to_index",
    "remove_report_from_index",
    "get_report_stats",
    # Report content helpers (migrated from midscene-upload.py)
    "report_html_candidates_for_job",
    "report_text_context",
    "report_image_context",
    # Report cleanup (migrated from midscene-upload.py)
    "report_cleanup_policy",
    "report_cleanup_candidates",
    "cleanup_midscene_reports",
    "report_cleanup_scheduler",
    "start_report_cleanup_scheduler",
    # Report checkpoints (migrated from midscene-upload.py)
    "build_report_checkpoints",
]


# ===================================================================
# ========== 报告内容辅助函数（从 midscene-upload.py 迁移） ==========
# ===================================================================

def _read_text(path: Any, default: str = "") -> str:
    """读取文本文件，失败返回 default。"""
    try:
        p = Path(path)
        if not p.exists():
            return default
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return default


def _html_text_to_plain(text: str, max_chars: int = 12000) -> str:
    """将 HTML 转换为纯文本。

    Migrated from ``midscene-upload.py:html_text_to_plain``。
    """
    raw = str(text or "")
    if not raw:
        return ""
    raw = re.sub(r"(?is)<script\b.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style\b.*?</style>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(div|p|li|tr|section|article|h[1-6])\s*>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = html_lib.unescape(raw)
    lines: List[str] = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line and line not in lines[-3:]:
            lines.append(line)
    plain = "\n".join(lines)
    if max_chars and len(plain) > max_chars:
        return plain[-max_chars:]
    return plain


def report_html_candidates_for_job(job: Dict[str, Any]) -> List[Path]:
    """查找 job 对应的报告 HTML 文件候选列表。

    Migrated from ``midscene-upload.py:report_html_candidates_for_job``。
    """
    candidates: List[Path] = []
    run_dir = job.get("run_dir") or ""
    if run_dir:
        run_path = Path(run_dir)
        candidates.extend([
            run_path / "report.html",
            run_path / f"{job.get('job_id', '')}.html",
        ])
        try:
            candidates.extend(sorted(run_path.glob("**/*.html"), key=lambda item: item.stat().st_mtime, reverse=True)[:4])
        except Exception:
            pass
    job_id = job.get("job_id") or ""
    if job_id:
        candidates.append(Path(REPORT_DIR) / f"{job_id}.html")
    report_url = job.get("report_url") or ""
    if report_url:
        try:
            name = os.path.basename(urllib.parse.urlparse(report_url).path)
            if name:
                candidates.append(Path(REPORT_DIR) / urllib.parse.unquote(name))
        except Exception:
            pass
    local_report = job.get("local_report_path") or ""
    if local_report:
        try:
            candidates.append(Path(local_report))
        except Exception:
            pass
    unique: List[Path] = []
    seen: set = set()
    for path in candidates:
        try:
            key = str(path)
        except Exception:
            continue
        if key and key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def report_text_context(job: Dict[str, Any], max_chars: int = 12000) -> str:
    """提取 job 报告的文本上下文。

    Migrated from ``midscene-upload.py:report_text_context``。
    """
    parts: List[str] = []
    for path in report_html_candidates_for_job(job or {}):
        text = _read_text(path, "")
        if not text:
            continue
        plain = _html_text_to_plain(text, max_chars=max_chars)
        if plain:
            parts.append(f"[REPORT_TEXT:{Path(path).name}]\n{plain}")
    joined = "\n\n".join(parts)
    if len(joined) > max_chars:
        return joined[-max_chars:]
    return joined


class _MidsceneReportScriptParser(HTMLParser):
    """Collect Midscene's typed image store and execution dump scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.images: List[Tuple[str, str]] = []
        self.dumps: List[str] = []
        self._capture: Optional[Tuple[str, str]] = None
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "script" or self._capture is not None:
            return
        attributes = {
            str(key or "").strip().lower(): str(value or "").strip()
            for key, value in attrs
        }
        script_type = attributes.get("type", "").lower()
        if script_type == "midscene-image":
            image_id = attributes.get("data-id", "")
            if image_id:
                self._capture = (script_type, image_id)
                self._parts = []
        elif script_type == "midscene_web_dump":
            self._capture = (script_type, "")
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._capture is not None:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._capture is not None:
            self._parts.append(f"&#{name};")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or self._capture is None:
            return
        script_type, image_id = self._capture
        body = "".join(self._parts).strip()
        if script_type == "midscene-image":
            self.images.append((image_id, body))
        else:
            self.dumps.append(body)
        self._capture = None
        self._parts = []


def _midscene_screenshot_reference_ids(value: Any) -> List[str]:
    references: List[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("type") == "midscene_screenshot_ref":
                image_id = str(item.get("id") or "").strip()
                if image_id:
                    references.append(image_id)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return references


def _decode_report_image(data_url: str, name: str) -> Optional[Dict[str, Any]]:
    match = re.fullmatch(
        r"\s*data:(image/(?:png|jpe?g|webp));base64,([A-Za-z0-9+/=\r\n]+)\s*",
        str(data_url or ""),
        flags=re.I,
    )
    if not match:
        return None
    mime = match.group(1).lower().replace("image/jpg", "image/jpeg")
    encoded = re.sub(r"\s+", "", match.group(2))
    if len(encoded) < 1000:
        return None
    try:
        data = base64.b64decode(encoded, validate=False)
    except Exception:
        return None
    if not data or len(data) > 2 * 1024 * 1024:
        return None
    extension = "jpg" if mime == "image/jpeg" else mime.split("/", 1)[-1]
    return {
        "name": f"{name}.{extension}",
        "mime": mime,
        "base64": base64.b64encode(data).decode("ascii"),
    }


def _structured_midscene_report_images(text: str, report_name: str) -> Tuple[List[Dict[str, Any]], bool]:
    parser = _MidsceneReportScriptParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return [], bool(parser.images)
    if not parser.images:
        return [], False

    image_store = {image_id: data_url for image_id, data_url in parser.images}
    referenced_ids: List[str] = []
    for raw_dump in parser.dumps:
        try:
            dump = json.loads(html_lib.unescape(raw_dump))
        except Exception:
            continue
        referenced_ids.extend(_midscene_screenshot_reference_ids(dump))

    ordered_ids: List[str] = []
    for image_id in referenced_ids:
        if image_id in image_store and image_id not in ordered_ids:
            ordered_ids.append(image_id)
    if not ordered_ids:
        ordered_ids = list(dict.fromkeys(image_id for image_id, _data_url in parser.images))

    images = []
    for image_id in ordered_ids:
        decoded = _decode_report_image(
            image_store.get(image_id, ""),
            f"{report_name}-midscene-{image_id}",
        )
        if decoded:
            images.append(decoded)
    return images, True


def report_image_context(
    job: Dict[str, Any],
    limit: int = 4,
) -> List[Dict[str, Any]]:
    """提取 job 报告中的图片上下文（base64 编码）。

    Migrated from ``midscene-upload.py:report_image_context``。
    """
    collected: List[Dict[str, Any]] = []
    seen: set = set()
    legacy_data_url_re = re.compile(
        r"data:(image/(?:png|jpe?g|webp));base64,([A-Za-z0-9+/=\r\n]+)",
        flags=re.I,
    )
    for path in report_html_candidates_for_job(job or {}):
        text = _read_text(path, "")
        if not text:
            continue
        structured, has_midscene_image_store = _structured_midscene_report_images(
            text,
            Path(path).name,
        )
        candidates = structured
        if not has_midscene_image_store:
            candidates = []
            for idx, match in enumerate(legacy_data_url_re.finditer(text), start=1):
                decoded = _decode_report_image(
                    match.group(0),
                    f"{Path(path).name}-legacy-{idx}",
                )
                if decoded:
                    candidates.append(decoded)
        for image in candidates:
            encoded = str(image.get("base64") or "")
            if not encoded:
                continue
            key = hashlib.sha256(encoded.encode("ascii", errors="ignore")).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            collected.append(image)
    return collected[-limit:] if limit else collected


# ===================================================================
# ========== 报告清理（从 midscene-upload.py 迁移） ==========
# ===================================================================

def report_cleanup_policy() -> Dict[str, Any]:
    """Migrated from ``midscene-upload.py:report_cleanup_policy``."""
    return {
        "retention_days": REPORT_RETENTION_DAYS,
        "min_keep": REPORT_RETENTION_MIN_KEEP,
        "interval_seconds": REPORT_CLEANUP_INTERVAL_SECONDS,
        "cleanup_on_startup": REPORT_CLEANUP_ON_STARTUP,
        "report_dir": REPORT_DIR,
    }


def report_cleanup_candidates(
    retention_days: Optional[int] = None,
    min_keep: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Migrated from ``midscene-upload.py:report_cleanup_candidates``。

    Returns:
        ``(stale_html, chunk_files, stats)``
    """
    retention_days = max(1, safe_int(retention_days, REPORT_RETENTION_DAYS))
    min_keep = max(0, safe_int(min_keep, REPORT_RETENTION_MIN_KEEP))
    cutoff = time.time() - retention_days * 86400
    html_files: List[Dict[str, Any]] = []
    chunk_files: List[Dict[str, Any]] = []
    if not os.path.isdir(REPORT_DIR):
        return [], [], {"total_html": 0, "kept_by_min_keep": 0, "cutoff": cutoff}
    try:
        for item in Path(REPORT_DIR).iterdir():
            try:
                if item.is_file() and item.suffix.lower() in (".html", ".htm"):
                    stat = item.stat()
                    html_files.append({"path": item, "mtime": stat.st_mtime, "size": stat.st_size})
            except Exception:
                continue
    except Exception:
        return [], [], {"total_html": 0, "kept_by_min_keep": 0, "cutoff": cutoff}
    html_files.sort(key=lambda item: item["mtime"], reverse=True)
    protected = {str(item["path"]) for item in html_files[:min_keep]}
    stale_html = [
        item for item in html_files
        if item["mtime"] < cutoff and str(item["path"]) not in protected
    ]
    chunk_root = Path(REPORT_DIR) / ".chunks"
    if chunk_root.exists():
        try:
            for item in chunk_root.iterdir():
                try:
                    stat = item.stat()
                    if stat.st_mtime < time.time() - 86400:
                        chunk_files.append({
                            "path": item,
                            "mtime": stat.st_mtime,
                            "size": stat.st_size if item.is_file() else 0,
                        })
                except Exception:
                    continue
        except Exception:
            pass
    return stale_html, chunk_files, {
        "total_html": len(html_files),
        "kept_by_min_keep": min(len(html_files), min_keep),
        "cutoff": cutoff,
    }


def cleanup_midscene_reports(
    retention_days: Optional[int] = None,
    min_keep: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Migrated from ``midscene-upload.py:cleanup_midscene_reports``."""
    stale_html, chunk_files, stats = report_cleanup_candidates(retention_days, min_keep)
    deleted: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    reclaimed = 0
    for item in stale_html + chunk_files:
        path = item["path"]
        size = safe_int(item.get("size"), 0)
        record: Dict[str, Any] = {
            "path": str(path),
            "name": path.name,
            "size": size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item.get("mtime") or 0)),
        }
        if dry_run:
            deleted.append(record)
            reclaimed += size
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
            deleted.append(record)
            reclaimed += size
        except Exception as e:
            record["error"] = str(e)
            errors.append(record)
    return {
        "ok": not errors,
        "dry_run": _safe_bool(dry_run),
        "policy": {
            **report_cleanup_policy(),
            "retention_days": max(1, safe_int(retention_days, REPORT_RETENTION_DAYS)),
            "min_keep": max(0, safe_int(min_keep, REPORT_RETENTION_MIN_KEEP)),
        },
        "stats": stats,
        "deleted_count": len(deleted) if not dry_run else 0,
        "candidate_count": len(deleted),
        "reclaimed_bytes": reclaimed,
        "items": deleted[:200],
        "errors": errors[:50],
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def report_cleanup_scheduler() -> None:
    """Migrated from ``midscene-upload.py:report_cleanup_scheduler``."""
    if REPORT_CLEANUP_ON_STARTUP:
        try:
            cleanup_midscene_reports()
        except Exception as e:
            print(f"report cleanup startup failed: {e}")
    while True:
        time.sleep(REPORT_CLEANUP_INTERVAL_SECONDS)
        try:
            result = cleanup_midscene_reports()
            if result.get("deleted_count"):
                print(f"report cleanup deleted {result.get('deleted_count')} files, reclaimed {result.get('reclaimed_bytes')} bytes")
        except Exception as e:
            print(f"report cleanup failed: {e}")


def start_report_cleanup_scheduler() -> None:
    """Migrated from ``midscene-upload.py:start_report_cleanup_scheduler``."""
    thread = threading.Thread(target=report_cleanup_scheduler, name="report-cleanup", daemon=True)
    thread.start()


def _safe_bool(value: Any, default: bool = False) -> bool:
    """Local helper for safe_bool."""
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


# ===================================================================
# ========== 报告检查点（从 midscene-upload.py 迁移） ==========
# ===================================================================

def _normalize_text_list(value: Any) -> List[str]:
    """Migrated from ``midscene-upload.py:normalize_text_list``."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]
    return []


def _first_non_empty(*values: Any) -> str:
    """Migrated from ``midscene-upload.py:first_non_empty``."""
    for v in values:
        v = str(v or "").strip()
        if v:
            return v
    return ""


def build_report_checkpoints(summary: Dict[str, Any]) -> List[str]:
    """Migrated from ``midscene-upload.py:build_report_checkpoints``。

    从用例生成汇总中提取报告检查点。
    """
    analysis = summary.get("requirement_analysis") or {}
    goals = _normalize_text_list(analysis.get("business_goals"))
    requirement_points = _normalize_text_list(analysis.get("requirement_points"))
    risks = _normalize_text_list(analysis.get("risks"))
    missing_inputs = _normalize_text_list(analysis.get("missing_inputs"))
    questions = _normalize_text_list(analysis.get("questions"))
    blockers = _normalize_text_list(analysis.get("blockers"))
    visible_outcomes = _normalize_text_list(analysis.get("visible_outcomes"))
    cases = [item for item in (summary.get("cases") or []) if isinstance(item, dict)]
    manual_cases = [item for item in (summary.get("manual_cases") or []) if isinstance(item, dict)]
    scenarios = [item for item in (summary.get("scenarios") or []) if isinstance(item, dict)]

    def tail_note(items: Any, fallback: str) -> str:
        items = _normalize_text_list(items)
        if not items:
            return fallback
        return "；".join(items[:3])

    def case_weight(item: Dict[str, Any]) -> Tuple[int, int, int]:
        priority = str(item.get("priority") or "").upper()
        return (
            2 if item.get("smoke") else 0,
            {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(priority, 0),
            len(_normalize_text_list(item.get("assertions"))),
        )

    def case_brief(item: Dict[str, Any]) -> str:
        title = item.get("title") or item.get("case_id") or "未命名用例"
        target = _first_non_empty(
            item.get("coverage"),
            item.get("expected_result"),
            item.get("scenario"),
            item.get("business_path"),
            "",
        )
        return f"{title}（{target}）" if target else str(title)

    key_cases = sorted(cases, key=case_weight, reverse=True)
    positive_cases = [item for item in key_cases if not re.search(r"异常|失败|错误|弱网|超时|空态|边界|取消|返回|无|未", case_brief(item))]
    negative_cases = [item for item in key_cases if item not in positive_cases]
    risk_bits = risks + missing_inputs + questions + blockers
    manual_bits = [
        _first_non_empty(item.get("title"), item.get("reason"), item.get("suggested_setup"), "")
        for item in manual_cases
    ]
    scenario_titles = [
        _first_non_empty(item.get("name"), item.get("title"), item.get("scenario"), item.get("feature"), "")
        for item in scenarios
    ]

    checkpoints = [
        f"主流程验证：围绕「{summary.get('title') or '本次需求'}」确认核心业务目标是否达成，重点覆盖 {tail_note(requirement_points or goals, '需求主路径和验收目标')}。",
        f"页面与交互验证：确认关键 UI 可见结果、入口、按钮、弹窗、文案和状态流转符合预期，重点检查 {tail_note(visible_outcomes or scenario_titles, '页面可见结果、操作入口和状态提示')}。",
        f"关键用例验证：优先执行并记录 {tail_note([case_brief(item) for item in positive_cases[:3]] or [case_brief(item) for item in key_cases[:3]], 'P0/P1 和冒烟用例结果')}。",
        f"异常与边界验证：覆盖失败提示、空态/弱网/超时、返回/取消、重复操作和边界输入等风险路径，重点关注 {tail_note([case_brief(item) for item in negative_cases[:3]], tail_note(risk_bits, '需求中未明确的异常与边界场景'))}。",
        f"人工确认项：对自动化不稳定或需要造数/环境/后台/真实设备状态的内容单独记录结论，重点跟进 {tail_note(manual_bits or risk_bits, '当前暂无明确人工项，执行前仍需确认测试数据和环境稳定性')}。",
    ]
    return [re.sub(r"\s+", " ", item).strip() for item in checkpoints[:5]]
