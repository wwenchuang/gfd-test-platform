"""路由分发模块。全量路由注册，不再依赖 legacy fallback。

新路由通过装饰器注册到路由表；dispatch 函数先查路由表，
未命中则走静态文件服务或 404。
"""

import os
import re
import time
import json
import base64
import shutil
import urllib.parse
import threading
import traceback

from task_server.core.http_client import http_client
from task_server.execution import ExecutionFacade
from task_server.config import (
    TASK_DIR, REPORT_DIR, LEARNING_DIR, ASSET_DIR,
    CASE_DIR, GENERATE_JOB_DIR, KNOWLEDGE_DIR,
    TOKEN, SONIC_CALLBACK_TOKEN, ALLOW_QUERY_TOKEN,
    MAX_BODY_SIZE, MAX_UPLOAD_BODY_SIZE,
    TASK_SESSION_TTL_SECONDS,
    DEFAULT_APP_PACKAGE, DEFAULT_FIGMA_API_BASE,
    FIGMA_PARSE_LIMIT, AI_SKILLS_DIR,
    REPORT_RETENTION_DAYS, REPORT_RETENTION_MIN_KEEP,
    ENABLE_AUTOMATIC_BASELINE_REPAIR,
    APP_ENV, TASK_ENABLE_DEBUG_EXECUTION, ENV_FILE_LOAD_STATUS,
    safe_int, safe_bool, AGENT_RISK_KEYWORDS,
    JOB_LOCK, RUNNER_LOCK, AGENT_RUN_LOCK, SONIC_LOCK,
    PORT, SONIC_SUITE_COMPLETION_PATHS,
)
from task_server.storage import (
    safe_join, read_json_file, write_json_file, read_text_file,
    write_text_file, write_bytes_file, runtime_path_status,
    clean_filename, clean_asset_filename, clean_id, is_visible_yaml_filename,
)
from task_server.auth import (
    bearer_token, verify_session_token, is_user_authorized,
    is_runner_authorized, is_sonic_callback_authorized,
    is_authorized_with_query, REVOKED_SESSION_TOKENS,
)
from task_server.response import BodyTooLarge

from task_server.services.agent_service import (
    _start_agent_worker,
    advance_agent_run,
    cancel_agent_run,
    confirm_agent_step,
    create_agent_run,
    delete_agent_run,
    get_available_apps,
    get_agent_run,
    list_agent_runs,
    load_agent_runs,
)
from task_server.services.case_service import (
    automatic_baseline_repair_enabled,
    baseline_ref_key,
    find_task_case_asset,
    get_baseline_ref_page_ids,
    list_file_versions,
    load_baseline_refs,
    read_file_version,
    set_baseline_ref_page_ids,
    task_case_yaml,
)
from task_server.services.feishu_service import task_app_feishu_webhook
from task_server.services.job_service import (
    append_job_event,
    app_package_for_module,
    copy_or_move_task_file,
    create_job,
    create_pending_job,
    find_job,
    job_allows_auto_device,
    load_jobs,
    load_task_apps,
    load_task_meta,
    new_job_id,
    normalize_device_strategy,
    normalize_job_record,
    normalize_job_status,
    normalize_task_app,
    recover_timed_out_jobs,
    save_jobs,
    save_task_apps,
    task_app_map_by_package,
    update_task_meta,
)
from task_server.services.knowledge_service import (
    analyze_knowledge_screenshot,
    append_asset_files,
    asset_meta_path,
    import_figma_design,
    knowledge_app_dir,
    knowledge_meta_path,
    knowledge_page_dir,
    list_knowledge_app_details,
    list_knowledge_pages,
    load_asset_contents,
    parse_figma_design,
    save_asset_files,
    save_knowledge_page,
    update_asset_request_context,
)
from task_server.services.platform_service import (
    ai_skills_status,
    dashscope_api_key,
    dashscope_base_url,
    dashscope_text_model,
    dashscope_vl_model,
    pyyaml,
)
from task_server.services.ai_skill_service import call_dashscope_cases
from task_server.services.repair_service import (
    call_dashscope_failure_review,
    optimize_job_yaml_by_scope,
    repair_file_latest_result,
    repair_job_and_create_next,
    repair_task_latest_result,
    run_repair_job,
    safe_repair_artifact_dir,
    validate_midscene_yaml_executability,
)
from task_server.services.runner_service import (
    all_online_devices,
    annotate_job_queue_state,
    list_runners,
    load_runners,
    midscene_runtime_env,
    normalize_device_list,
    platform_preflight_dashboard,
    public_report_url,
    register_runner,
    runner_device_ids,
    runtime_env_preview,
    save_runners,
)
from task_server.services.sonic_service import (
    append_sonic_notify_log,
    attach_sonic_background_report,
    load_sonic_suite_results,
    load_sonic_sync,
    parse_sonic_suite_completion_payload,
    register_sonic_suite_completion,
    register_sonic_suite_result,
    resolve_task_app_sonic_binding,
    save_sonic_sync,
    sonic_api_prefix,
    sonic_auth_preview,
    sonic_base_url,
    sonic_list_projects,
    sonic_live_case_status,
    sonic_migrate_midscene_cases,
    sonic_notify_clean_text,
    sonic_notify_known_apps,
    sonic_probe_endpoint,
    sonic_probe_token,
    sonic_project_id_for_app,
    sonic_project_name_for_app,
    sonic_publish_batch,
    sonic_publish_precheck,
    sonic_publish_yaml,
    sonic_refresh_bridge_scripts,
    sonic_scan_midscene_cases,
    sonic_bridge_step_script,
    sonic_suite_app_info,
    sonic_suite_id_for_app,
    sonic_suite_name_for_app,
    sonic_token,
    sonic_token_fingerprint,
    sonic_token_source,
    start_sonic_result_post_actions,
    task_case_sonic_context,
    touch_sonic_suite_activity,
)
from task_server.services.yaml_baseline_cache import (
    get_yaml_baseline_cache,
    get_yaml_baseline_cache_status,
)
from task_server.services.yaml_executable_scorer import score_midscene_yaml_executable
from task_server.services.yaml_service import (
    build_generation_summary,
    case_ui_design_dir,
    cases_path,
    cases_to_midscene_yaml,
    cases_to_separate_midscene_yamls,
    changed_line_count,
    delete_case_ui_design_asset,
    delete_generate_job,
    dry_run_midscene_yaml,
    filtered_case_ui_design_assets_for_summary,
    find_figma_url_for_case_set,
    generate_job_id,
    generate_retry_request_from_job,
    generate_ui_yaml_from_request,
    generated_case_requirement_scope_review,
    generation_artifact_filename,
    clear_generation_mindmap_deleted,
    generation_mindmap_is_deleted,
    generation_mindmap_path,
    generation_summary_path,
    list_case_ui_design_assets,
    list_generate_jobs,
    list_generation_mindmaps,
    list_task_case_assets,
    load_generate_job,
    mark_generation_mindmap_deleted,
    mark_generation_mindmap_record_deleted,
    midscene_cli_dispatch_yaml_text,
    new_case_set_id,
    normalize_cases_payload,
    remove_generation_mindmap_file,
    resolve_app_package,
    restore_excluded_figma_node,
    run_figma_parse_job,
    run_generate_job,
    run_mindmap_only_job,
    sanitize_generate_job_for_client,
    save_case_ui_design_files,
    save_file_version,
    save_generate_job,
    slug_for_file,
    split_automation_ready_cases,
    update_generate_job,
    validate_midscene_yaml,
    write_generation_mindmap,
    write_generation_summary,
    yaml_diff_summary,
    yaml_with_single_task,
)



# ── 路由注册表 ─────────────────────────────────────────────────────

GET_ROUTES: dict = {}
POST_ROUTES: dict = {}
DELETE_ROUTES: dict = {}
HEAD_ROUTES: dict = {}

# 前缀匹配路由（按注册顺序匹配，首次命中即返回）
_GET_PREFIX_ROUTES: list = []
_POST_PREFIX_ROUTES: list = []
_DELETE_PREFIX_ROUTES: list = []

# 正则匹配路由（按注册顺序匹配）
_GET_REGEX_ROUTES: list = []
_POST_REGEX_ROUTES: list = []
_DELETE_REGEX_ROUTES: list = []


def route_get(path):
    """装饰器：注册 GET 路由"""
    def decorator(fn):
        GET_ROUTES[path] = fn
        return fn
    return decorator


def route_post(path):
    """装饰器：注册 POST 路由"""
    def decorator(fn):
        POST_ROUTES[path] = fn
        return fn
    return decorator


def route_delete(path):
    """装饰器：注册 DELETE 路由"""
    def decorator(fn):
        DELETE_ROUTES[path] = fn
        return fn
    return decorator


def route_get_prefix(prefix):
    """装饰器：注册 GET 前缀匹配路由"""
    def decorator(fn):
        _GET_PREFIX_ROUTES.append((prefix, fn))
        return fn
    return decorator


def route_post_prefix(prefix):
    """装饰器：注册 POST 前缀匹配路由"""
    def decorator(fn):
        _POST_PREFIX_ROUTES.append((prefix, fn))
        return fn
    return decorator


def route_delete_prefix(prefix):
    """装饰器：注册 DELETE 前缀匹配路由"""
    def decorator(fn):
        _DELETE_PREFIX_ROUTES.append((prefix, fn))
        return fn
    return decorator


def route_get_regex(pattern):
    """装饰器：注册 GET 正则匹配路由"""
    def decorator(fn):
        _GET_REGEX_ROUTES.append((re.compile(pattern), fn))
        return fn
    return decorator


def route_post_regex(pattern):
    """装饰器：注册 POST 正则匹配路由"""
    def decorator(fn):
        _POST_REGEX_ROUTES.append((re.compile(pattern), fn))
        return fn
    return decorator


def route_delete_regex(pattern):
    """装饰器：注册 DELETE 正则匹配路由"""
    def decorator(fn):
        _DELETE_REGEX_ROUTES.append((re.compile(pattern), fn))
        return fn
    return decorator


# ── Dispatch 函数 ───────────────────────────────────────────────────

def dispatch_get(handler):
    """分发 GET 请求：精确匹配 → 前缀匹配 → 正则匹配 → 静态文件 → 404"""
    qs, path = handler._qs()

    # 1. 精确匹配
    if path in GET_ROUTES:
        return GET_ROUTES[path](handler, qs)

    # 2. 前缀匹配
    for prefix, fn in _GET_PREFIX_ROUTES:
        if path.startswith(prefix):
            return fn(handler, qs, path)

    # 3. 正则匹配
    for pattern, fn in _GET_REGEX_ROUTES:
        m = pattern.match(path)
        if m:
            return fn(handler, qs, m)

    # 4. 静态文件服务
    from task_server.app import _serve_static
    if _serve_static(handler, path):
        return

    # 5. 404
    handler._text("Not Found", 404)


def dispatch_post(handler):
    """分发 POST 请求：精确匹配 → 前缀匹配 → 正则匹配 → 404"""
    qs, path = handler._qs()
    if not handler._body_size_allowed(path):
        return

    # 1. 精确匹配
    if path in POST_ROUTES:
        return POST_ROUTES[path](handler, qs)

    # 2. 前缀匹配
    for prefix, fn in _POST_PREFIX_ROUTES:
        if path.startswith(prefix):
            return fn(handler, qs, path)

    # 3. 正则匹配
    for pattern, fn in _POST_REGEX_ROUTES:
        m = pattern.match(path)
        if m:
            return fn(handler, qs, m)

    # 4. 404
    handler._text("Not Found", 404)


def dispatch_delete(handler):
    """分发 DELETE 请求：精确匹配 → 前缀匹配 → 正则匹配 → 404"""
    qs, path = handler._qs()

    # 1. 精确匹配
    if path in DELETE_ROUTES:
        return DELETE_ROUTES[path](handler, qs)

    # 2. 前缀匹配
    for prefix, fn in _DELETE_PREFIX_ROUTES:
        if path.startswith(prefix):
            return fn(handler, qs, path)

    # 3. 正则匹配
    for pattern, fn in _DELETE_REGEX_ROUTES:
        m = pattern.match(path)
        if m:
            return fn(handler, qs, m)

    # 4. 404
    handler._text("Not Found", 404)


def dispatch_head(handler):
    """分发 HEAD 请求"""
    qs, path = handler._qs()

    # 首页
    if path in ("/", "/task-manager.html", "/trace-viewer.html"):
        handler.send_response(200)
        handler._cors()
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.end_headers()
        return

    # API 路由
    if path.startswith("/api/"):
        handler.send_response(200)
        handler._cors()
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        return

    handler.send_response(404)
    handler._cors()
    handler.end_headers()


def _json_error(handler, error, status=500, **extra):
    payload = {"ok": False, "error": str(error)}
    payload.update(extra)
    handler._json(payload, status)
    return True


def _unauthorized(handler):
    _json_error(handler, "Unauthorized", 401)
    return True


def _require_user_auth(handler):
    if not handler._authorized():
        return _unauthorized(handler)
    return False


def _require_runner_auth(handler):
    if not handler._authorized_runner():
        return _unauthorized(handler)
    return False


def _require_sonic_or_user_auth(handler, qs):
    if not handler._authorized_with_qs(qs):
        return _unauthorized(handler)
    return False


def _bridge_groovy_auth_failure_reason(handler, qs):
    x_token = handler.headers.get("x-token", "")
    token_ok = bool(x_token and x_token in (TOKEN, SONIC_CALLBACK_TOKEN))
    session_ok = handler._authorized()
    if token_ok or session_ok:
        return ""

    case_id = qs.get("case_id") or qs.get("caseId") or ""
    has_token = bool(x_token)
    reason = "missing x-token or session unauthorized" if not has_token else "invalid x-token"
    try:
        append_sonic_notify_log("bridge_groovy_unauthorized", {
            "case_id": case_id,
            "remote": getattr(handler, "client_address", [""])[0],
            "has_x_token": has_token,
            "reason": reason,
        })
    except Exception:
        pass
    return reason


# ── 辅助函数 ────────────────────────────────────────────────────────

_MIME_MAP = {
    ".html": "text/html; charset=utf-8",
    ".htm":  "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
    ".webp": "image/webp",
    ".yaml": "text/yaml; charset=utf-8",
    ".yml":  "text/yaml; charset=utf-8",
    ".txt":  "text/plain; charset=utf-8",
    ".mm":   "application/x-freemind; charset=utf-8",
    ".apk":  "application/vnd.android.package-archive",
}


def guess_mime(filename):
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_MAP.get(ext, "application/octet-stream")


def send_attachment(handler, body_bytes, filename, content_type):
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Disposition", f'attachment; filename="{urllib.parse.quote(filename)}"')
    handler.send_header("Content-Length", str(len(body_bytes)))
    handler.end_headers()
    try:
        handler.wfile.write(body_bytes)
    except (BrokenPipeError, ConnectionResetError):
        pass


APP_INSTALL_JOB_TYPE = "apk_install"
APP_INSTALL_PACKAGE_DIR = safe_join(LEARNING_DIR, "apk-packages")


def is_app_install_job(job):
    job_type = str(job.get("job_type") or job.get("jobType") or job.get("type") or "").strip().lower()
    return job_type == APP_INSTALL_JOB_TYPE


def normalize_install_mode(value):
    raw = str(value or "").strip().lower()
    if raw in {"baseline", "baseline_regression", "production", "online"}:
        return "baseline_regression"
    return "test_validation"


def normalize_package_source(value):
    raw = str(value or "").strip().lower()
    aliases = {
        "file": "upload",
        "manual": "upload",
        "manual_upload": "upload",
        "apk_upload": "upload",
        "apk": "upload",
        "apk_url": "url",
        "direct_url": "url",
        "link": "url",
        "pgyer_url": "pgyer",
        "pgyer": "pgyer",
        "pgyer_short": "pgyer",
        "online": "production_url",
        "prod": "production_url",
        "production": "production_url",
        "production_url": "production_url",
    }
    return aliases.get(raw, raw if raw in {"upload", "url", "pgyer", "production_url"} else "upload")


def clean_apk_filename(name):
    filename = clean_asset_filename(name or "app.apk", "app.apk")
    base, ext = os.path.splitext(filename)
    if ext.lower() != ".apk":
        filename = (base or "app") + ".apk"
    return filename


def app_install_package_meta_path(package_id):
    return safe_join(APP_INSTALL_PACKAGE_DIR, clean_id(package_id, "apk"), "meta.json")


def app_install_package_url(package_id):
    return f"/api/app-install/package?id={urllib.parse.quote(clean_id(package_id, 'apk'))}"


def save_uploaded_apk_package(job_id, apk_name, content_base64):
    if not content_base64:
        raise ValueError("请先上传 APK 文件")
    content = str(content_base64 or "").strip()
    if "," in content and content.lower().startswith("data:"):
        content = content.split(",", 1)[1]
    try:
        data = base64.b64decode(content, validate=True)
    except Exception:
        raise ValueError("APK 文件内容解析失败，请重新上传")
    if not data:
        raise ValueError("上传的 APK 文件为空")
    if len(data) > MAX_UPLOAD_BODY_SIZE:
        raise ValueError("APK 文件超过平台上传上限")
    filename = clean_apk_filename(apk_name)
    package_dir = safe_join(APP_INSTALL_PACKAGE_DIR, clean_id(job_id, "apk"))
    os.makedirs(package_dir, exist_ok=True)
    apk_path = safe_join(package_dir, filename)
    write_bytes_file(apk_path, data)
    write_json_file(safe_join(package_dir, "meta.json"), {
        "package_id": job_id,
        "filename": filename,
        "size": len(data),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return {
        "apk_name": filename,
        "apk_size": len(data),
        "apk_path": apk_path,
        "apk_url": app_install_package_url(job_id),
    }


def save_apk_upload_chunk(upload_id, apk_name, index, total_chunks, total_size, content_base64):
    package_id = clean_id(upload_id, "apk")
    filename = clean_apk_filename(apk_name)
    index = safe_int(index, -1)
    total_chunks = safe_int(total_chunks, 0)
    total_size = safe_int(total_size, 0)
    if not package_id or index < 0 or total_chunks <= 0 or index >= total_chunks:
        raise ValueError("APK 分片参数不完整")
    if total_size <= 0:
        raise ValueError("APK 文件大小异常")
    if total_size > MAX_UPLOAD_BODY_SIZE:
        raise ValueError(f"APK 文件超过平台上传上限 {MAX_UPLOAD_BODY_SIZE // 1024 // 1024}MB")
    content = str(content_base64 or "").strip()
    if not content:
        raise ValueError("APK 分片内容为空")
    try:
        data = base64.b64decode(content, validate=True)
    except Exception:
        raise ValueError("APK 分片内容解析失败，请重新上传")
    if not data:
        raise ValueError("APK 分片内容为空")
    package_dir = safe_join(APP_INSTALL_PACKAGE_DIR, package_id)
    chunk_dir = safe_join(package_dir, ".chunks")
    os.makedirs(chunk_dir, exist_ok=True)
    write_bytes_file(safe_join(chunk_dir, f"{index:05d}.part"), data)
    write_json_file(safe_join(package_dir, "upload.json"), {
        "package_id": package_id,
        "filename": filename,
        "total_chunks": total_chunks,
        "total_size": total_size,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return {"package_id": package_id, "index": index, "total_chunks": total_chunks}


def finish_apk_upload_chunks(upload_id, apk_name, total_chunks, total_size):
    package_id = clean_id(upload_id, "apk")
    total_chunks = safe_int(total_chunks, 0)
    total_size = safe_int(total_size, 0)
    package_dir = safe_join(APP_INSTALL_PACKAGE_DIR, package_id)
    upload_meta = read_json_file(safe_join(package_dir, "upload.json"), default={}) or {}
    filename = clean_apk_filename(apk_name or upload_meta.get("filename") or "app.apk")
    expected_chunks = safe_int(upload_meta.get("total_chunks"), total_chunks)
    expected_size = safe_int(upload_meta.get("total_size"), total_size)
    if not package_id or expected_chunks <= 0:
        raise ValueError("APK 分片上传不存在")
    if expected_size <= 0 or expected_size > MAX_UPLOAD_BODY_SIZE:
        raise ValueError(f"APK 文件超过平台上传上限 {MAX_UPLOAD_BODY_SIZE // 1024 // 1024}MB")
    chunk_dir = safe_join(package_dir, ".chunks")
    parts = [safe_join(chunk_dir, f"{index:05d}.part") for index in range(expected_chunks)]
    for index, part in enumerate(parts):
        if not os.path.exists(part):
            raise ValueError(f"APK 上传缺少分片 {index + 1}/{expected_chunks}")
    received_size = sum(os.path.getsize(part) for part in parts)
    if received_size != expected_size:
        raise ValueError(f"APK 分片大小不一致：收到 {received_size} 字节，预期 {expected_size} 字节")
    final_path = safe_join(package_dir, filename)
    tmp_final = final_path + f".tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp_final, "wb") as out:
            for part in parts:
                with open(part, "rb") as f:
                    shutil.copyfileobj(f, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_final, final_path)
    finally:
        if os.path.exists(tmp_final):
            try:
                os.remove(tmp_final)
            except Exception:
                pass
    shutil.rmtree(chunk_dir, ignore_errors=True)
    meta = {
        "package_id": package_id,
        "filename": filename,
        "size": received_size,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "upload_mode": "chunked",
    }
    write_json_file(safe_join(package_dir, "meta.json"), meta)
    return {
        "package_id": package_id,
        "apk_name": filename,
        "apk_size": received_size,
        "apk_path": final_path,
        "apk_url": app_install_package_url(package_id),
    }


def uploaded_apk_package_from_url(apk_url):
    parsed = urllib.parse.urlparse(str(apk_url or "").strip())
    if parsed.path != "/api/app-install/package":
        return None
    package_id = clean_id((urllib.parse.parse_qs(parsed.query).get("id") or [""])[0], "apk")
    meta = read_json_file(app_install_package_meta_path(package_id), default={}) or {}
    filename = clean_apk_filename(meta.get("filename") or "app.apk")
    apk_path = safe_join(APP_INSTALL_PACKAGE_DIR, package_id, filename)
    if not os.path.exists(apk_path):
        raise ValueError("已上传的 APK 文件不存在，请重新上传")
    size = os.path.getsize(apk_path)
    return {
        "package_id": package_id,
        "apk_name": filename,
        "apk_size": size,
        "apk_path": apk_path,
        "apk_url": app_install_package_url(package_id),
    }


def validate_install_package_request(install_mode, package_source, apk_url):
    if install_mode == "baseline_regression" and package_source != "production_url":
        raise ValueError("基线回归只能安装线上包来源；测试包、上传包和蒲公英链接请用于“测试环境验证”。")
    if package_source in {"url", "pgyer", "production_url"}:
        parsed = urllib.parse.urlparse(apk_url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("请填写有效的安装包下载地址")


# ══════════════════════════════════════════════════════════════════════
#  GET 路由注册
# ══════════════════════════════════════════════════════════════════════

# ── 健康检查 ────────────────────────────────────────────────────────

@route_get("/api/health")
def _get_health(handler, qs):
    handler._json({
        "ok": True,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "port": PORT,
        "paths": {
            "tasks": runtime_path_status(TASK_DIR),
            "reports": runtime_path_status(REPORT_DIR),
            "learning": runtime_path_status(LEARNING_DIR),
            "generate_jobs": runtime_path_status(GENERATE_JOB_DIR),
            "knowledge": runtime_path_status(KNOWLEDGE_DIR),
            "ai_skills": ai_skills_status(),
        },
        "models": {
            "dashscope_key": bool(dashscope_api_key(required=False)),
            "text_model": dashscope_text_model(),
            "vl_model": dashscope_vl_model(),
            "base_url": dashscope_base_url(),
        },
        "figma": {
            "token": bool(os.getenv("FIGMA_TOKEN")),
            "api_base": os.getenv("FIGMA_API_BASE", DEFAULT_FIGMA_API_BASE),
        },
        "dependencies": {
            "pyyaml": bool(pyyaml),
            "yaml_parser": "PyYAML" if pyyaml else "text_fallback",
        }
    })


# ── 认证 ────────────────────────────────────────────────────────────

@route_get("/api/models")
def _get_models(handler, qs):
    """Return available AI models from system configuration."""
    models = []
    # 1. DashScope 默认模型
    text_model = dashscope_text_model()
    vl_model = dashscope_vl_model()
    models.append({"id": text_model, "name": text_model, "group": "DashScope", "default": True})
    if vl_model != text_model:
        models.append({"id": vl_model, "name": vl_model, "group": "DashScope"})
    # 2. AI Gateway providers
    try:
        import os as _os
        base_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        ai_gw_dir = _os.path.join(base_dir, "ai-gateway")
        providers_file = _os.path.join(ai_gw_dir, "config", "providers.json")
        from task_server.storage import read_json_file
        providers = read_json_file(providers_file, default={})
        router_file = _os.path.join(ai_gw_dir, "config", "model-router.json")
        router = read_json_file(router_file, default={})
        prov_dict = providers.get("providers", {}) if isinstance(providers, dict) else {}
        if isinstance(prov_dict, dict):
            seen = {text_model, vl_model}
            for pid, pconf in prov_dict.items():
                m = pconf.get("model", "") if isinstance(pconf, dict) else ""
                if m and m not in seen:
                    seen.add(m)
                    name = pconf.get("name", m)
                    api_key_env = str(pconf.get("apiKeyEnv", "") or "")
                    api_key_value = _os.getenv(api_key_env, "") if api_key_env else ""
                    roles = []
                    if isinstance(router, dict):
                        for action, item in router.items():
                            provider_id = item.get("providerId") if isinstance(item, dict) else item
                            if provider_id == pid:
                                roles.append(action)
                    models.append({
                        "id": m,
                        "name": name,
                        "group": "AI Gateway",
                        "providerId": pid,
                        "model": m,
                        "configured": bool(api_key_value and not api_key_value.lower().startswith("your_")),
                        "routerRoles": roles,
                    })
    except Exception:
        pass
    handler._json({"ok": True, "models": models})


@route_get("/api/auth/me")
def _get_auth_me(handler, qs):
    payload = verify_session_token(bearer_token(handler.headers))
    if not payload:
        _unauthorized(handler)
        return
    handler._json({"ok": True, "user": payload.get("user"), "expires_at": payload.get("exp")})


# ── 应用列表 ────────────────────────────────────────────────────────

@route_get("/api/apps")
def _get_apps(handler, qs):
    handler._json(get_available_apps())


# ── 模块列表 ────────────────────────────────────────────────────────

@route_get("/api/modules")
def _get_modules(handler, qs):
    result = {}
    if os.path.exists(TASK_DIR):
        for mod in sorted(os.listdir(TASK_DIR)):
            mp = safe_join(TASK_DIR, mod)
            if os.path.isdir(mp):
                result[mod] = sorted([
                    f for f in os.listdir(mp)
                    if is_visible_yaml_filename(f)
                ])
    handler._json(result)


# ── YAML 统计 ────────────────────────────────────────────────────────

@route_get("/api/yaml-stats")
def _get_yaml_stats(handler, qs):
    from task_server.services.yaml_service import yaml_priority_stats
    module_filter = (qs.get("module") or "").strip()
    result = {}
    if os.path.exists(TASK_DIR):
        module_names = [module_filter] if module_filter else sorted(os.listdir(TASK_DIR))
        for mod in module_names:
            mp = safe_join(TASK_DIR, mod)
            if not os.path.isdir(mp):
                continue
            module_stats = {}
            for file in sorted(os.listdir(mp)):
                if not is_visible_yaml_filename(file):
                    continue
                fpath = safe_join(mp, file)
                try:
                    text = read_text_file(fpath)
                    module_stats[file] = yaml_priority_stats(text)
                except Exception as e:
                    module_stats[file] = {
                        "total": 0, "p0": 0, "p1": 0, "p2": 0, "p3": 0, "smoke": 0,
                        "loaded": True, "error": str(e)
                    }
            result[mod] = module_stats
    handler._json({"ok": True, "stats": result})


@route_get("/api/yaml/baseline-cache/status")
def _get_yaml_baseline_cache_status(handler, qs):
    if _require_user_auth(handler):
        return
    try:
        force = safe_bool(qs.get("force"), False)
        handler._json(get_yaml_baseline_cache_status(force=force))
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)


# ── Task Meta ────────────────────────────────────────────────────────

@route_get("/api/task-meta")
def _get_task_meta(handler, qs):
    if _require_user_auth(handler):
        return
    handler._json({"ok": True, "meta": load_task_meta()})


# ── Task Apps ────────────────────────────────────────────────────────

@route_get("/api/task-apps")
def _get_task_apps(handler, qs):
    if _require_user_auth(handler):
        return
    handler._json({"ok": True, "apps": sonic_notify_known_apps()})


# ── Sonic 配置 ──────────────────────────────────────────────────────

@route_get("/api/sonic/config")
def _get_sonic_config(handler, qs):
    if _require_user_auth(handler):
        return
    handler._json({
        "ok": True,
        "base_url": sonic_base_url(),
        "api_prefix": sonic_api_prefix(),
        "token_configured": bool(sonic_token()),
        "token_source": sonic_token_source(),
        "token_fingerprint": sonic_token_fingerprint(),
        "public_task_url": os.getenv("MIDSCENE_PUBLIC_BASE_URL") or os.getenv("TASK_PUBLIC_BASE_URL") or "http://101.34.197.12:8088"
    })


# ── Sonic 运行环境 ──────────────────────────────────────────────────

@route_get("/api/sonic/runtime-env")
def _get_sonic_runtime_env(handler, qs):
    if _require_user_auth(handler):
        return
    env = midscene_runtime_env()
    handler._json({
        "ok": True,
        "env": env,
        "preview": runtime_env_preview(env),
        "env_file": ENV_FILE_LOAD_STATUS,
    })


# ── Preflight Dashboard ─────────────────────────────────────────────

@route_get("/api/preflight/dashboard")
def _get_preflight_dashboard(handler, qs):
    if _require_user_auth(handler):
        return
    live = safe_bool(qs.get("live") or qs.get("sonic") or qs.get("includeSonic"))
    try:
        handler._json({"ok": True, **platform_preflight_dashboard(include_sonic_scan=live)})
    except Exception as e:
        _json_error(handler, e, 500)


# ── 报告清理（GET）──────────────────────────────────────────────────

@route_get("/api/reports/cleanup")
def _get_reports_cleanup(handler, qs):
    if _require_user_auth(handler):
        return
    from task_server.services.report_service import cleanup_midscene_reports, report_cleanup_policy
    try:
        dry_run = str(qs.get("dry_run") or qs.get("dryRun") or "1").lower() not in ("0", "false", "no")
        days = safe_int(qs.get("days") or qs.get("retention_days") or qs.get("retentionDays"), REPORT_RETENTION_DAYS)
        min_keep = safe_int(qs.get("min_keep") or qs.get("minKeep"), REPORT_RETENTION_MIN_KEEP)
        handler._json(cleanup_midscene_reports(days, min_keep, dry_run=dry_run))
    except Exception as e:
        _json_error(handler, e, 500, policy=report_cleanup_policy())


# ── 修复草稿列表 ────────────────────────────────────────────────────

@route_get("/api/repair-drafts")
def _get_repair_drafts(handler, qs):
    if _require_user_auth(handler):
        return
    from task_server.services.repair_service import load_repair_drafts
    drafts = load_repair_drafts()
    job_id = qs.get("job_id") or qs.get("jobId")
    include_all = safe_bool(qs.get("include_all") or qs.get("includeAll"))
    if job_id:
        drafts = [draft for draft in drafts if draft.get("jobId") == job_id or draft.get("job_id") == job_id]
    if not include_all:
        drafts = [draft for draft in drafts if draft.get("status") in ("DRAFTED", "WAIT_CONFIRM")]
    handler._json({"ok": True, "drafts": drafts})


# ── Sonic 用例列表 ──────────────────────────────────────────────────

@route_get("/api/sonic/cases")
def _get_sonic_cases(handler, qs):
    rows = list_task_case_assets(qs.get("module", ""), qs.get("file", ""))
    handler._json({"ok": True, "cases": rows, "sync": load_sonic_sync().get("cases", {})})


# ── Sonic 状态 ──────────────────────────────────────────────────────

@route_get("/api/sonic/status")
def _get_sonic_status(handler, qs):
    try:
        rows = list_task_case_assets(qs.get("module", ""), qs.get("file", ""))
        status_rows = [sonic_live_case_status(row) for row in rows if not row.get("error")]
        summary = {
            "total": len(status_rows),
            "bridge": len([row for row in status_rows if row.get("step_state") == "bridge"]),
            "legacy": len([row for row in status_rows if row.get("step_state") == "legacy"]),
            "mixed": len([row for row in status_rows if row.get("step_state") == "mixed"]),
            "missing": len([row for row in status_rows if row.get("step_state") in ("missing", "not_published")]),
            "project_missing": len([row for row in status_rows if row.get("step_state") == "project_missing"]),
        }
        handler._json({"ok": True, "summary": summary, "cases": status_rows})
    except Exception as e:
        _json_error(handler, e, 500)


# ── Sonic 套件结果 ──────────────────────────────────────────────────

@route_get("/api/sonic/suite-results")
def _get_sonic_suite_results(handler, qs):
    data = load_sonic_suite_results()
    suites = list((data.get("suites") or {}).values())
    suites.sort(key=lambda item: safe_int(item.get("last_update_ts") or item.get("created_ts"), 0), reverse=True)
    limit = max(1, min(100, safe_int(qs.get("limit"), 30)))
    handler._json({"ok": True, "suites": suites[:limit], "active": data.get("active") or {}})


# ── Sonic 单条用例 ──────────────────────────────────────────────────

@route_get("/api/sonic/case")
def _get_sonic_case(handler, qs):
    try:
        case = find_task_case_asset(qs.get("case_id") or qs.get("caseId"))
        public_case = dict(case)
        public_case.pop("_root", None)
        handler._json({
            "ok": True,
            "case": public_case,
            "context": task_case_sonic_context(case),
            "yaml": task_case_yaml(case)
        })
    except FileNotFoundError as e:
        # 提供更详细的错误信息,帮助排查问题
        case_id = qs.get("case_id") or qs.get("caseId") or ""
        error_msg = str(e)
        hint = (
            f"\n\n排查建议:\n"
            f"1. 检查服务器上 TASK_DIR({TASK_DIR}) 是否存在对应的 YAML 文件\n"
            f"2. 检查 YAML 文件是否包含 'baseline.case_id: {case_id}' 注释\n"
            f"3. 确认服务使用 python -m task_server 启动\n"
            f"4. 如果文件存在,尝试重新从 Task 平台「同步到 Sonic」生成桥接脚本"
        )
        _json_error(handler, error_msg + hint, 404)
    except Exception as e:
        _json_error(handler, e, 400)


# ── Sonic 用例 YAML ─────────────────────────────────────────────────

@route_get("/api/sonic/case-yaml")
def _get_sonic_case_yaml(handler, qs):
    try:
        case = find_task_case_asset(qs.get("case_id") or qs.get("caseId"))
        handler._text(task_case_yaml(case))
    except FileNotFoundError as e:
        handler._text(str(e), 404)
    except Exception as e:
        handler._text(str(e), 400)


# ── Sonic Bridge Groovy ─────────────────────────────────────────────

@route_get("/api/sonic/bridge-groovy")
def _get_sonic_bridge_groovy(handler, qs):
    reason = _bridge_groovy_auth_failure_reason(handler, qs)
    if reason:
        handler._json({
            "ok": False,
            "error": reason,
            "suggestion": "请刷新 Sonic 桥接脚本，并确认 MIDSCENE_RUNNER_TOKEN 与 Sonic 用例脚本中的 runnerToken 一致。",
        }, 401)
        return
    bridge_path = os.getenv("SONIC_BRIDGE_GROOVY_PATH", "/opt/sonic-midscene-task-runner.groovy")
    bridge = read_text_file(bridge_path, "")
    if not bridge:
        bridge = read_text_file(os.path.join(os.getcwd(), "sonic-midscene-task-runner.groovy"), "")
    if not bridge:
        handler._text("sonic bridge groovy not found; set SONIC_BRIDGE_GROOVY_PATH", 500)
        return
    handler._text(bridge)


@route_get("/api/sonic/bridge-diagnose")
def _get_sonic_bridge_diagnose(handler, qs):
    case_id = qs.get("case_id") or qs.get("caseId") or ""
    case_found = False
    case_error = ""
    try:
        find_task_case_asset(case_id)
        case_found = True
    except Exception as exc:
        case_error = str(exc)
    bridge_path = os.getenv("SONIC_BRIDGE_GROOVY_PATH", "/opt/sonic-midscene-task-runner.groovy")
    bridge = read_text_file(bridge_path, "") or read_text_file(os.path.join(os.getcwd(), "sonic-midscene-task-runner.groovy"), "")
    step_script = sonic_bridge_step_script(case_id) if case_id else ""
    runner_token_configured = bool(TOKEN)
    callback_token_configured = bool(SONIC_CALLBACK_TOKEN)
    public_base = os.getenv("MIDSCENE_PUBLIC_BASE_URL") or os.getenv("TASK_PUBLIC_BASE_URL") or ""
    contains_x_token = 'setRequestProperty("x-token"' in step_script
    ok = bool(case_found and bridge and runner_token_configured and contains_x_token)
    possible_reasons = []
    if not case_found:
        possible_reasons.append("未找到 case_id 对应的 YAML 或 baseline.case_id")
    if not bridge:
        possible_reasons.append("服务端桥接 Groovy 文件不存在")
    if not runner_token_configured:
        possible_reasons.append("MIDSCENE_RUNNER_TOKEN 未配置")
    if not contains_x_token:
        possible_reasons.append("Sonic 用例中的 bridge 脚本可能是旧版本，未携带 x-token")
    if not public_base:
        possible_reasons.append("MIDSCENE_PUBLIC_BASE_URL/TASK_PUBLIC_BASE_URL 未配置")
    if not possible_reasons:
        possible_reasons.append("若 Sonic 仍返回 401，通常是 Sonic 用例脚本里的 runnerToken 仍是旧值")
    handler._json({
        "ok": ok,
        "caseId": case_id,
        "caseFound": case_found,
        "caseError": case_error,
        "bridgeGroovyReachable": bool(bridge),
        "runnerTokenConfigured": runner_token_configured,
        "callbackTokenConfigured": callback_token_configured,
        "publicBaseUrl": public_base,
        "bridgeScriptContainsXToken": contains_x_token,
        "possibleReasons": possible_reasons,
        "nextActions": [
            "重新同步 Sonic 用例",
            "刷新 Sonic 桥接脚本",
            "检查 /opt/midscene.env 中 MIDSCENE_RUNNER_TOKEN",
            "检查 MIDSCENE_PUBLIC_BASE_URL",
        ],
    }, 200 if ok else 400)


@route_get("/api/sonic/callback-diagnose")
def _get_sonic_callback_diagnose(handler, qs):
    """诊断 Runner/Sonic 回传 HTTP 000。

    HTTP 000 通常不是业务返回码，而是客户端没拿到 HTTP 响应：
    公网地址不可达、Nginx 代理缺失、服务端未启动、TLS/网络中断都会表现成 000。
    """
    public_base = (
        os.getenv("MIDSCENE_PUBLIC_BASE_URL")
        or os.getenv("TASK_PUBLIC_BASE_URL")
        or ""
    ).rstrip("/")
    local_base = f"http://127.0.0.1:{PORT}"
    base = public_base or local_base
    health_url = f"{base}/api/health"
    local_health_url = f"{local_base}/api/health"

    def probe(url):
        try:
            resp = http_client.get(url, headers={"User-Agent": "midscene-callback-diagnose/1.0"}, timeout=5)
            return {
                "ok": 200 <= int(resp.status) < 500,
                "status": int(resp.status),
                "error": "",
            }
        except Exception as exc:
            return {"ok": False, "status": 0, "error": str(exc)[:300]}

    public_probe = probe(health_url)
    local_probe = probe(local_health_url)
    runner_token_configured = bool(TOKEN)
    callback_token_configured = bool(SONIC_CALLBACK_TOKEN)
    suggestions = []
    if not public_base:
        suggestions.append("配置 MIDSCENE_PUBLIC_BASE_URL，例如 http://101.34.197.12:8088")
    if not public_probe.get("ok"):
        suggestions.append("确认公网/Nginx/Docker 代理能访问 /api/health，Runner 回传必须使用外部可达地址")
    if local_probe.get("ok") and not public_probe.get("ok"):
        suggestions.append("本机服务可用但公网不可达，重点检查 8088 反代、容器 nginx 和安全组")
    if not runner_token_configured:
        suggestions.append("配置 MIDSCENE_RUNNER_TOKEN，并同步到 Windows/Mac Runner")
    if not callback_token_configured:
        suggestions.append("配置 SONIC_CALLBACK_TOKEN，供 Sonic 回调独立鉴权")
    if not suggestions:
        suggestions.append("服务端回调地址基础检查通过；若仍 HTTP 000，请检查 Runner 所在机器网络和防火墙")

    handler._json({
        "ok": bool(public_probe.get("ok") and runner_token_configured),
        "publicBaseUrl": public_base,
        "healthUrl": health_url,
        "localHealthUrl": local_health_url,
        "healthReachableFromServer": bool(public_probe.get("ok")),
        "publicHealthStatus": public_probe.get("status", 0),
        "publicHealthError": public_probe.get("error", ""),
        "localHealthReachable": bool(local_probe.get("ok")),
        "localHealthStatus": local_probe.get("status", 0),
        "runnerTokenConfigured": runner_token_configured,
        "callbackTokenConfigured": callback_token_configured,
        "suggestions": suggestions,
    })


# ── 文件内容 ────────────────────────────────────────────────────────

@route_get("/api/file")
def _get_file(handler, qs):
    try:
        fpath = safe_join(TASK_DIR, qs.get("module", ""), qs.get("file", ""))
    except ValueError:
        handler._text("非法路径", 400)
        return
    if os.path.exists(fpath):
        with open(fpath, encoding="utf-8") as f:
            handler._text(f.read())
    else:
        handler._text("不存在", 404)


# ── 文件版本历史 ────────────────────────────────────────────────────

@route_get("/api/file/history")
def _get_file_history(handler, qs):
    mod = qs.get("module", "")
    file = qs.get("file", "")
    if not mod or not file:
        handler._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
        return
    handler._json({"ok": True, "versions": list_file_versions(mod, file)})


# ── 文件版本内容 ────────────────────────────────────────────────────

@route_get("/api/file/version")
def _get_file_version(handler, qs):
    mod = qs.get("module", "")
    file = qs.get("file", "")
    version_id = qs.get("version") or qs.get("id")
    if not mod or not file or not version_id:
        handler._json({"ok": False, "error": "module、file 和 version 不能为空"}, 400)
        return
    try:
        meta, content = read_file_version(mod, file, version_id)
    except FileNotFoundError:
        handler._json({"ok": False, "error": "版本不存在"}, 404)
        return
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({"ok": True, "version": meta, "content": content})


# ── 修复结果 ────────────────────────────────────────────────────────

@route_get("/api/repair/result")
def _get_repair_result(handler, qs):
    repair_dir = qs.get("repair_dir") or qs.get("dir") or ""
    try:
        rdir = safe_repair_artifact_dir(repair_dir)
        result = read_json_file(safe_join(rdir, "repair.json"), default=None)
        if not result:
            handler._json({"ok": False, "error": "修复结果不存在"}, 404)
            return
        before = read_text_file(safe_join(rdir, "before.yaml"))
        after = read_text_file(safe_join(rdir, "after.yaml"))
        if "diff_summary" not in result:
            result["diff_summary"] = yaml_diff_summary(before, after)
        if "changed_line_count" not in result:
            result["changed_line_count"] = changed_line_count(before, after)
        handler._json({"ok": True, "result": result})
    except ValueError as e:
        handler._json({"ok": False, "error": str(e)}, 400)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)


# ── 生成任务状态 ────────────────────────────────────────────────────

@route_get("/api/ui/generate-status")
def _get_generate_status(handler, qs):
    job_id = qs.get("job_id") or qs.get("id")
    if not job_id:
        handler._json({"ok": False, "error": "job_id 不能为空"}, 400)
        return
    job = load_generate_job(job_id)
    if not job:
        handler._json({"ok": False, "error": "生成任务不存在"}, 404)
        return
    handler._json({"ok": True, "job": sanitize_generate_job_for_client(job)})


# ── 用例汇总 ────────────────────────────────────────────────────────

@route_get("/api/cases/summary")
def _get_cases_summary(handler, qs):
    case_set_id = qs.get("case_set_id") or qs.get("id")
    if not case_set_id:
        handler._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
        return
    summary = read_json_file(generation_summary_path(case_set_id), default=None)
    if not summary:
        handler._json({"ok": False, "error": "生成汇总不存在"}, 404)
        return
    ui_design_meta = filtered_case_ui_design_assets_for_summary(case_set_id, summary)
    if ui_design_meta.get("designs"):
        summary["ui_design_assets"] = ui_design_meta.get("designs") or []
    elif summary.get("ui_design_assets"):
        summary["ui_design_assets"] = []
    if ui_design_meta.get("hidden_designs"):
        summary["hidden_ui_design_assets"] = ui_design_meta.get("hidden_designs") or []
    if ui_design_meta.get("excluded_figma_nodes"):
        summary["excluded_figma_nodes"] = ui_design_meta.get("excluded_figma_nodes") or []
    mindmap_deleted = generation_mindmap_is_deleted(case_set_id)
    mindmap_exists = os.path.exists(generation_mindmap_path(case_set_id))
    handler._json({
        "ok": True,
        "summary": summary,
        "artifacts": {
            "mindmap_exists": mindmap_exists,
            "mindmap_deleted": mindmap_deleted,
            "mindmap_downloadable": mindmap_exists and not mindmap_deleted
        }
    })


def _summary_yaml_file_list(summary):
    raw_files = (
        (summary or {}).get("yaml_files")
        or (summary or {}).get("yamlFiles")
        or []
    )
    if isinstance(raw_files, str):
        raw_files = [raw_files]
    files = []
    for item in raw_files:
        if isinstance(item, dict):
            file_name = item.get("file") or item.get("path") or item.get("name")
        else:
            file_name = str(item or "")
        file_name = file_name.strip()
        if file_name:
            files.append(file_name)
    single_file = str((summary or {}).get("yaml_file") or "").strip()
    if single_file and single_file not in files:
        files.append(single_file)
    return files


def _case_is_smoke(row):
    if not isinstance(row, dict):
        return False
    if row.get("smoke") is True or row.get("is_smoke") is True or row.get("isSmoke") is True:
        return True
    text_parts = []
    for key in ("flag", "flags", "tags"):
        value = row.get(key)
        if isinstance(value, list):
            text_parts.extend(str(item) for item in value)
        elif value:
            text_parts.append(str(value))
    return "冒烟" in " ".join(text_parts)


def _case_priority(row):
    if not isinstance(row, dict):
        return ""
    return str(row.get("priority") or row.get("level") or "").strip().upper()


def generation_smoke_yaml_refs(summary):
    """Return executable smoke YAML refs for a generated case set."""
    if not isinstance(summary, dict):
        return []
    cases = [case for case in (summary.get("cases") or []) if isinstance(case, dict)]
    cases_by_id = {
        str(case.get("case_id") or case.get("id") or "").strip(): case
        for case in cases
        if str(case.get("case_id") or case.get("id") or "").strip()
    }
    yaml_files = _summary_yaml_file_list(summary)
    groups = summary.get("generatedCaseGroups") or {}
    executable_rows = (
        groups.get("executable_cases")
        or summary.get("executable_cases")
        or []
    )
    refs = []
    seen = set()

    def add_ref(file_name, row=None, target_task_name=""):
        file_name = str(file_name or "").strip()
        if not file_name:
            return
        key = (file_name, str(target_task_name or ""))
        if key in seen:
            return
        seen.add(key)
        refs.append({
            "file": file_name,
            "name": (row or {}).get("name") or (row or {}).get("title") or target_task_name or file_name,
            "case_id": (row or {}).get("case_id") or (row or {}).get("id") or "",
            "priority": _case_priority(row),
            "score": (row or {}).get("score") or 0,
            "target_task_name": target_task_name or "",
        })

    for row in executable_rows:
        if not isinstance(row, dict):
            continue
        case = cases_by_id.get(str(row.get("case_id") or "").strip()) or {}
        merged = {**case, **row}
        if not _case_is_smoke(merged):
            continue
        add_ref(row.get("file"), merged)

    if refs:
        return refs

    # Backward compatibility for old summaries that were written before
    # generatedCaseGroups/yamlExecutableScores were persisted.
    one_file = len(yaml_files) == 1
    for index, case in enumerate(cases):
        if not _case_is_smoke(case):
            continue
        if index < len(yaml_files):
            add_ref(yaml_files[index], case)
        elif one_file:
            add_ref(yaml_files[0], case, target_task_name=case.get("title") or case.get("name") or "")
    return refs


def generation_executable_yaml_refs(summary, *, include_smoke=True):
    """Return executable generated YAML refs, optionally excluding smoke cases."""
    if not isinstance(summary, dict):
        return []
    cases = [case for case in (summary.get("cases") or []) if isinstance(case, dict)]
    cases_by_id = {
        str(case.get("case_id") or case.get("id") or "").strip(): case
        for case in cases
        if str(case.get("case_id") or case.get("id") or "").strip()
    }
    groups = summary.get("generatedCaseGroups") or {}
    executable_rows = (
        groups.get("executable_cases")
        or summary.get("executable_cases")
        or []
    )
    refs = []
    seen = set()
    for row in executable_rows:
        if not isinstance(row, dict):
            continue
        file_name = str(row.get("file") or "").strip()
        if not file_name:
            continue
        case = cases_by_id.get(str(row.get("case_id") or "").strip()) or {}
        merged = {**case, **row}
        if not include_smoke and _case_is_smoke(merged):
            continue
        key = (file_name, str(merged.get("target_task_name") or ""))
        if key in seen:
            continue
        seen.add(key)
        refs.append({
            "file": file_name,
            "name": merged.get("name") or merged.get("title") or merged.get("case_name") or file_name,
            "case_id": merged.get("case_id") or merged.get("id") or "",
            "priority": _case_priority(merged),
            "score": merged.get("score") or 0,
            "target_task_name": merged.get("target_task_name") or "",
        })
    return refs


def _summary_case_map(summary):
    cases = [case for case in (summary.get("cases") or []) if isinstance(case, dict)] if isinstance(summary, dict) else []
    return {
        str(case.get("case_id") or case.get("caseId") or case.get("id") or "").strip(): case
        for case in cases
        if str(case.get("case_id") or case.get("caseId") or case.get("id") or "").strip()
    }


def _summary_requirement_analysis(summary):
    merged = {}
    if not isinstance(summary, dict):
        return merged
    for source in (
        summary.get("analysis"),
        summary.get("requirement_analysis"),
        summary.get("requirementAnalysis"),
        (summary.get("review") or {}).get("analysis") if isinstance(summary.get("review"), dict) else None,
    ):
        if isinstance(source, dict):
            merged.update(source)
    return merged


def _summary_generated_rows(summary):
    if not isinstance(summary, dict):
        return []
    groups = summary.get("generatedCaseGroups") or {}
    rows = []
    seen = set()
    for bucket in ("executable_cases", "needs_review_cases", "draft_cases", "manual_cases"):
        for row in (groups.get(bucket) or summary.get(bucket) or []):
            if not isinstance(row, dict):
                continue
            file_name = str(row.get("file") or "").strip()
            target_task_name = str(row.get("target_task_name") or "").strip()
            if not file_name:
                continue
            key = (file_name, target_task_name)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    for file_name in _summary_yaml_file_list(summary):
        key = (file_name, "")
        if key not in seen:
            seen.add(key)
            rows.append({"file": file_name})
    return rows


def _summary_has_generated_buckets(summary):
    if not isinstance(summary, dict):
        return False
    groups = summary.get("generatedCaseGroups") or {}
    for bucket in ("executable_cases", "needs_review_cases", "draft_cases", "manual_cases"):
        if groups.get(bucket) or summary.get(bucket):
            return True
    return False


def generation_current_executable_yaml_refs(summary, module, *, include_smoke=True, require_smoke=False):
    """Re-score the YAML files currently saved on disk and return runnable refs.

    Generated case groups are snapshots. Users can edit the generated YAML after
    review, so rerun actions must score the current file content instead of
    trusting the old generation-time bucket.
    """
    if not isinstance(summary, dict) or not module:
        return []
    cases_by_id = _summary_case_map(summary)
    requirement_analysis = _summary_requirement_analysis(summary)
    refs = []
    seen = set()
    for row in _summary_generated_rows(summary):
        file_name = str(row.get("file") or "").strip()
        if not file_name:
            continue
        source_case = cases_by_id.get(str(row.get("case_id") or row.get("caseId") or "").strip()) or {}
        merged = {**source_case, **row}
        is_smoke = _case_is_smoke(merged)
        if require_smoke and not is_smoke:
            continue
        if not include_smoke and is_smoke:
            continue
        target_task_name = str(merged.get("target_task_name") or "").strip()
        key = (file_name, target_task_name)
        if key in seen:
            continue
        seen.add(key)
        try:
            yaml_path = safe_join(TASK_DIR, module, file_name)
        except ValueError:
            continue
        if not os.path.exists(yaml_path):
            continue
        try:
            yaml_content = read_text_file(yaml_path)
            yaml_to_score = yaml_content
            if target_task_name:
                app_package = resolve_app_package(module, file_name, yaml_content)
                yaml_to_score = yaml_with_single_task(yaml_content, target_task_name, app_package=app_package)
            score = score_midscene_yaml_executable(yaml_to_score, generated=True)
        except Exception:
            continue
        if score.get("executionLevel") != "executable":
            continue
        task_scores = [item for item in (score.get("taskScores") or []) if isinstance(item, dict)]
        task_names = [str(item.get("name") or "").strip() for item in task_scores if str(item.get("name") or "").strip()]
        scope_case = {
            "title": " / ".join(task_names) or target_task_name or file_name,
            "priority": merged.get("priority") or "",
            "tags": merged.get("tags") or merged.get("flags") or merged.get("flag") or [],
        }
        scope_review = generated_case_requirement_scope_review(scope_case, requirement_analysis, yaml_to_score)
        if not scope_review.get("ok"):
            continue
        refs.append({
            "file": file_name,
            "name": task_names[0] if task_names else (merged.get("name") or merged.get("title") or file_name),
            "case_id": merged.get("case_id") or merged.get("caseId") or merged.get("id") or "",
            "priority": _case_priority(merged),
            "score": score.get("score") or 0,
            "target_task_name": target_task_name,
            "executionLevel": score.get("executionLevel") or "executable",
            "currentYamlScored": True,
        })
    return refs


def generation_smoke_rerun_default_limit(summary=None):
    upper = max(1, min(10, safe_int(os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_SMOKE_LIMIT"), 3)))
    first_upper = max(1, min(3, safe_int(os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT"), 3)))
    if not isinstance(summary, dict):
        return first_upper
    review = summary.get("review") if isinstance(summary.get("review"), dict) else {}
    coverage_audit = review.get("coverage_audit") if isinstance(review.get("coverage_audit"), dict) else {}
    candidates = [
        review.get("generation_targets"),
        review.get("generationTargets"),
        coverage_audit.get("generation_targets"),
        coverage_audit.get("generationTargets"),
        summary.get("generation_targets"),
        summary.get("generationTargets"),
    ]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        smoke_limit = safe_int(item.get("smoke_cases") or item.get("smokeCases"), 0)
        smoke_max = safe_int(item.get("smoke_max_cases") or item.get("smokeMaxCases"), upper)
        if smoke_limit > 0:
            return max(1, min(first_upper, upper, smoke_max or upper, smoke_limit))
    return first_upper


# ── 脑图列表 ────────────────────────────────────────────────────────

@route_get("/api/cases/mindmaps")
def _get_cases_mindmaps(handler, qs):
    limit = safe_int(qs.get("limit"), 100)
    handler._json({"ok": True, "mindmaps": list_generation_mindmaps(limit)})


# ── 脑图下载 ────────────────────────────────────────────────────────

@route_get("/api/cases/mindmap")
def _get_cases_mindmap(handler, qs):
    case_set_id = qs.get("case_set_id") or qs.get("id")
    if not case_set_id:
        handler._text("case_set_id 不能为空", 400)
        return
    summary = read_json_file(generation_summary_path(case_set_id), default=None)
    if not summary:
        handler._text("生成汇总不存在", 404)
        return
    mm_path = generation_mindmap_path(case_set_id)
    if generation_mindmap_is_deleted(case_set_id):
        handler._text("脑图文件已删除；请点击刷新脑图文件", 410)
        return
    if not os.path.exists(mm_path):
        write_generation_mindmap(case_set_id, summary)
    try:
        body = read_text_file(mm_path).encode("utf-8")
    except Exception:
        handler._text("思维导图不存在", 404)
        return
    filename = generation_artifact_filename(summary, case_set_id, "测试用例.mm")
    send_attachment(handler, body, filename, "application/x-freemind; charset=utf-8")


# ── UI 设计稿列表 ──────────────────────────────────────────────────

@route_get("/api/cases/ui-designs")
def _get_cases_ui_designs(handler, qs):
    case_set_id = qs.get("case_set_id") or qs.get("id")
    if not case_set_id:
        handler._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
        return
    handler._json({"ok": True, **list_case_ui_design_assets(case_set_id)})


# ── UI 设计稿图片 ──────────────────────────────────────────────────

@route_get("/api/cases/ui-design-image")
def _get_cases_ui_design_image(handler, qs):
    case_set_id = qs.get("case_set_id") or qs.get("id")
    asset_id = qs.get("asset_id") or qs.get("assetId") or ""
    filename = clean_asset_filename(qs.get("filename") or "")
    if not case_set_id or not (asset_id or filename):
        handler._text("case_set_id 和 asset_id 不能为空", 400)
        return
    meta = list_case_ui_design_assets(case_set_id)
    match = None
    for item in meta.get("designs") or []:
        if (asset_id and item.get("asset_id") == asset_id) or (filename and item.get("filename") == filename):
            match = item
            break
    if not match:
        handler._text("UI 设计稿不存在", 404)
        return
    image_path = safe_join(case_ui_design_dir(case_set_id), match.get("filename") or "")
    if not os.path.exists(image_path):
        handler._text("UI 设计稿文件不存在", 404)
        return
    with open(image_path, "rb") as f:
        body = f.read()
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", match.get("mime") or guess_mime(match.get("filename") or "image.png"))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


# ── 知识库应用列表 ──────────────────────────────────────────────────

@route_get("/api/knowledge/apps")
def _get_knowledge_apps(handler, qs):
    details = list_knowledge_app_details()
    handler._json({
        "ok": True,
        "apps": [item["package"] for item in details],
        "appDetails": details
    })


# ── 知识库页面列表 ──────────────────────────────────────────────────

@route_get("/api/knowledge/pages")
def _get_knowledge_pages(handler, qs):
    app_package = qs.get("app_package") or qs.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    tier = qs.get("tier") or qs.get("library") or "all"
    app_info = task_app_map_by_package().get(app_package) or {}
    handler._json({
        "ok": True,
        "app_package": app_package,
        "app_name": app_info.get("name") or app_package,
        "modules": app_info.get("modules") or [],
        "tier": tier,
        "pages": list_knowledge_pages(app_package, tier=tier)
    })


# ── 知识库截图 ──────────────────────────────────────────────────────

@route_get("/api/knowledge/screenshot")
def _get_knowledge_screenshot(handler, qs):
    app_package = qs.get("app_package") or qs.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    page_id = qs.get("page_id") or qs.get("pageId")
    if not page_id:
        handler._text("page_id 不能为空", 400)
        return
    meta = read_json_file(knowledge_meta_path(app_package, page_id), default=None)
    if not meta or not meta.get("screenshot"):
        handler._text("截图不存在", 404)
        return
    try:
        image_path = safe_join(knowledge_page_dir(app_package, page_id), meta["screenshot"])
        with open(image_path, "rb") as f:
            body = f.read()
    except Exception:
        handler._text("截图不存在", 404)
        return
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", guess_mime(meta["screenshot"]))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


# ── 知识库失败模式匹配 ──────────────────────────────────────────────

@route_get("/api/knowledge/failures")
def _get_knowledge_failures(handler, qs):
    from task_server.services.knowledge_service import match_failure_pattern
    log_text = str(qs.get("log") or "").strip()
    if not log_text:
        handler._json({"ok": False, "error": "缺少 log 参数"}, 400)
        return
    try:
        top_k = int(qs.get("topK") or 3)
    except (TypeError, ValueError):
        top_k = 3
    matches = match_failure_pattern(log_text, top_k=top_k)
    handler._json({"ok": True, "matches": matches})


# ── 知识库用例历史 ──────────────────────────────────────────────────

@route_get("/api/knowledge/cases")
def _get_knowledge_cases(handler, qs):
    from task_server.services.knowledge_service import get_case_history
    yaml_file = str(qs.get("file") or "").strip()
    if not yaml_file:
        handler._json({"ok": False, "error": "缺少 file 参数"}, 400)
        return
    try:
        limit = int(qs.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    history = get_case_history(yaml_file, limit=limit)
    handler._json({"ok": True, **history})


# ── 知识库统计 ──────────────────────────────────────────────────────

@route_get("/api/knowledge/stats")
def _get_knowledge_stats(handler, qs):
    from task_server.services.knowledge_service import get_knowledge_stats
    stats = get_knowledge_stats()
    handler._json({"ok": True, **stats})


# ── 基线页面引用 ────────────────────────────────────────────────────

@route_get("/api/baseline/page-refs")
def _get_baseline_page_refs(handler, qs):
    mod = qs.get("module", "")
    file = clean_filename(qs.get("file", ""))
    app_package = qs.get("app_package") or qs.get("appPackage") or app_package_for_module(mod) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    task_name = qs.get("taskName") or qs.get("task_name") or ""
    if not mod or not file:
        handler._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
        return
    refs = load_baseline_refs()
    file_ref = refs.get(baseline_ref_key(app_package, mod, file, "")) or {}
    task_ref = refs.get(baseline_ref_key(app_package, mod, file, task_name)) or {}
    file_page_ids = file_ref.get("page_ids") or []
    task_page_ids = task_ref.get("page_ids") or []
    merged = get_baseline_ref_page_ids(app_package, mod, file, task_name)
    pages = list_knowledge_pages(app_package, tier="baseline")
    selected_pages = []
    for page in pages:
        page_id = page.get("page_id")
        if page_id not in merged:
            continue
        item = dict(page)
        in_file = page_id in file_page_ids
        in_task = page_id in task_page_ids
        item["ref_source"] = "both" if in_file and in_task else ("task" if in_task else "file")
        selected_pages.append(item)
    handler._json({
        "ok": True,
        "app_package": app_package,
        "module": mod,
        "file": file,
        "task_name": task_name,
        "file_page_ids": file_page_ids,
        "task_page_ids": task_page_ids,
        "merged_page_ids": merged,
        "selected_pages": selected_pages,
        "pages": pages
    })


# ── Jobs 列表 ───────────────────────────────────────────────────────

@route_get("/api/jobs")
def _get_jobs(handler, qs):
    if _require_user_auth(handler):
        return
    recover_timed_out_jobs()
    with JOB_LOCK:
        jobs = load_jobs()
    with RUNNER_LOCK:
        runners = load_runners()
    active_jobs = [job for job in jobs if job.get("status") in ("pending", "running")]
    active_ids = {job.get("job_id") for job in active_jobs}
    recent_done = [job for job in jobs if job.get("job_id") not in active_ids][-100:]
    result_jobs = [normalize_job_record(annotate_job_queue_state(job, runners)) for job in (active_jobs + recent_done)]
    background_jobs = [normalize_job_record(job) for job in list_generate_jobs(80)]
    handler._json({"ok": True, "jobs": result_jobs, "background_jobs": background_jobs})


# ── Runners 列表 ────────────────────────────────────────────────────

@route_get("/api/runners")
def _get_runners(handler, qs):
    if _require_user_auth(handler):
        return
    devices = all_online_devices()
    runners = list_runners()
    handler._json({"ok": True, "runners": runners, "devices": devices})


# ── 安装包更新任务 ──────────────────────────────────────────────────

@route_get("/api/app-install/package")
def _get_app_install_package(handler, qs):
    if _require_runner_auth(handler):
        return
    package_id = clean_id(qs.get("id") or "", "apk")
    meta_path = app_install_package_meta_path(package_id)
    meta = read_json_file(meta_path, default={})
    filename = clean_apk_filename(meta.get("filename") or "app.apk")
    apk_path = safe_join(APP_INSTALL_PACKAGE_DIR, package_id, filename)
    if not os.path.exists(apk_path):
        handler._json({"ok": False, "error": "安装包不存在或已被清理"}, 404)
        return
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", "application/vnd.android.package-archive")
    handler.send_header("Content-Disposition", f'attachment; filename="{urllib.parse.quote(filename)}"')
    handler.send_header("Content-Length", str(os.path.getsize(apk_path)))
    handler.end_headers()
    try:
        with open(apk_path, "rb") as f:
            shutil.copyfileobj(f, handler.wfile)
    except (BrokenPipeError, ConnectionResetError):
        pass


@route_post("/api/app-install/upload-chunk")
def _post_app_install_upload_chunk(handler, qs):
    if _require_user_auth(handler):
        return
    try:
        d = handler._body()
        result = save_apk_upload_chunk(
            d.get("upload_id") or d.get("uploadId") or "",
            d.get("filename") or d.get("apk_name") or d.get("apkName") or "app.apk",
            d.get("index"),
            d.get("total_chunks") or d.get("totalChunks") or d.get("total"),
            d.get("total_size") or d.get("totalSize") or d.get("size"),
            d.get("chunk_base64") or d.get("chunkBase64") or d.get("contentBase64") or "",
        )
        handler._json({"ok": True, **result})
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)


@route_post("/api/app-install/upload-finish")
def _post_app_install_upload_finish(handler, qs):
    if _require_user_auth(handler):
        return
    try:
        d = handler._body()
        result = finish_apk_upload_chunks(
            d.get("upload_id") or d.get("uploadId") or "",
            d.get("filename") or d.get("apk_name") or d.get("apkName") or "app.apk",
            d.get("total_chunks") or d.get("totalChunks") or d.get("total"),
            d.get("total_size") or d.get("totalSize") or d.get("size"),
        )
        handler._json({"ok": True, **result})
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)


@route_post("/api/app-install/request")
def _post_app_install_request(handler, qs):
    if _require_user_auth(handler):
        return
    d = handler._body()
    install_mode = normalize_install_mode(d.get("install_mode") or d.get("installMode") or d.get("mode"))
    package_source = normalize_package_source(d.get("source_type") or d.get("sourceType") or d.get("package_source") or d.get("packageSource"))
    runner_id = d.get("runner_id") or d.get("runnerId") or ""
    device_id = d.get("device_id") or d.get("deviceId") or ""
    device_strategy = normalize_device_strategy(
        d.get("device_strategy") or d.get("deviceStrategy"),
        device_id=device_id,
        runner_id=runner_id,
    )
    if device_strategy != "auto" and not device_id and not runner_id:
        handler._json({
            "ok": False,
            "error": "请选择安装设备；如确实需要平台分配，请明确选择“自动选择在线设备”。"
        }, 400)
        return
    job_id = new_job_id()
    apk_name = clean_apk_filename(d.get("apk_name") or d.get("apkName") or "app.apk")
    apk_url = (d.get("apk_url") or d.get("apkUrl") or d.get("pgyer_url") or d.get("pgyerUrl") or "").strip()
    apk_size = 0
    try:
        if package_source == "upload":
            content_base64 = d.get("contentBase64") or d.get("apkBase64") or d.get("fileBase64") or ""
            if content_base64:
                saved = save_uploaded_apk_package(job_id, apk_name, content_base64)
            else:
                saved = uploaded_apk_package_from_url(apk_url)
                if not saved:
                    raise ValueError("上传包缺少分片上传结果，请重新选择 APK 上传")
            apk_name = saved["apk_name"]
            apk_url = saved["apk_url"]
            apk_size = saved["apk_size"]
        validate_install_package_request(install_mode, package_source, apk_url)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)
        return

    mode_label = "基线回归" if install_mode == "baseline_regression" else "测试环境验证"
    source_label = {
        "upload": "上传 APK",
        "url": "APK 直链",
        "pgyer": "蒲公英链接",
        "production_url": "线上包地址",
    }.get(package_source, package_source)
    job = create_job({
        "job_id": job_id,
        "job_type": APP_INSTALL_JOB_TYPE,
        "type": APP_INSTALL_JOB_TYPE,
        "module": "安装包更新",
        "file": apk_name,
        "status": "pending",
        "run_mode": "baseline" if install_mode == "baseline_regression" else "test",
        "target_runner_id": runner_id,
        "device_id": device_id,
        "device_strategy": device_strategy,
        "target_task_name": f"{mode_label}安装包更新",
        "current_task_name": "等待 Runner 下载并安装 APK",
        "task_names": ["下载安装包", "ADB 安装", "安装结果校验"],
        "total_task_count": 3,
        "progress": 0,
        "install_mode": install_mode,
        "package_source": package_source,
        "package_source_label": source_label,
        "apk_name": apk_name,
        "apk_url": apk_url,
        "apk_size": apk_size,
        "app_package": (d.get("app_package") or d.get("appPackage") or "").strip(),
    })
    handler._json({"ok": True, "job": job})


# ── Runner 下一个任务 ───────────────────────────────────────────────

@route_get("/api/runner/jobs/next")
def _get_runner_jobs_next(handler, qs):
    recover_timed_out_jobs()
    if _require_runner_auth(handler):
        return
    runner_id = qs.get("runner_id", "runner")
    runner_device_ids_qs = set(filter(None, (qs.get("devices") or "").split(",")))
    with RUNNER_LOCK:
        runners = load_runners()
        runner_info = runners.get(runner_id, {})
    available_devices = runner_device_ids(runner_info) | runner_device_ids_qs
    with JOB_LOCK:
        jobs = load_jobs()
        selected = None
        for job in jobs:
            if job.get("status") != "pending":
                continue
            target_runner = job.get("target_runner_id") or ""
            target_device = job.get("device_id") or ""
            auto_device = job_allows_auto_device(job)
            if target_runner and target_runner != runner_id:
                continue
            if target_device and target_device not in available_devices:
                continue
            if not target_device and not auto_device:
                continue
            if not target_device and auto_device and not available_devices:
                continue
            if job.get("status") == "pending":
                selected = job
                break
        if selected:
            selected["status"] = "running"
            selected["runner_id"] = runner_id
            if not selected.get("device_id") and job_allows_auto_device(selected) and available_devices:
                selected["device_id"] = sorted(available_devices)[0]
            selected["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_jobs(jobs)
            selected_is_yaml_dry_run = str(selected.get("job_type") or selected.get("type") or "").strip().lower() == "yaml_dry_run"
            if not is_app_install_job(selected) and not selected_is_yaml_dry_run and selected.get("module") and selected.get("file"):
                update_task_meta(selected["module"], selected["file"], {
                    "last_job_id": selected["job_id"],
                    "last_status": "running",
                    "last_target_task_name": selected.get("target_task_name", ""),
                    "last_run_at": selected["started_at"]
                })

    if not selected:
        handler._json({"ok": True, "job": None})
        return
    selected_is_yaml_dry_run = str(selected.get("job_type") or selected.get("type") or "").strip().lower() == "yaml_dry_run"

    if is_app_install_job(selected):
        job_payload = dict(selected)
        job_payload["job_type"] = APP_INSTALL_JOB_TYPE
        job_payload["type"] = APP_INSTALL_JOB_TYPE
        handler._json({"ok": True, "job": job_payload})
        return

    try:
        yaml_path = safe_join(TASK_DIR, selected["module"], selected["file"])
        with open(yaml_path, encoding="utf-8") as f:
            yaml_content = f.read()
        target_task_name = selected.get("target_task_name", "")
        if target_task_name:
            app_package = resolve_app_package(selected["module"], selected["file"], yaml_content)
            yaml_content = yaml_with_single_task(yaml_content, target_task_name, app_package=app_package)
        yaml_content = midscene_cli_dispatch_yaml_text(yaml_content, device_id=selected.get("device_id", ""))
    except Exception as e:
        with JOB_LOCK:
            jobs = load_jobs()
            for job in jobs:
                if job.get("job_id") == selected.get("job_id"):
                    job["status"] = "failed"
                    job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    job["stderr_tail"] = f"任务下发失败：{e}"
                    job["progress"] = job.get("progress") or 0
                    break
            save_jobs(jobs)
        handler._json({"ok": False, "error": str(e)}, 500)
        return

    handler._json({
        "ok": True,
        "job": {
            "job_id": selected["job_id"],
            "module": selected["module"],
            "file": selected["file"],
            "target_task_name": selected.get("target_task_name", ""),
            "device_id": selected.get("device_id", ""),
            "runner_id": selected.get("runner_id", ""),
            "target_runner_id": selected.get("target_runner_id", ""),
            "device_strategy": selected.get("device_strategy") or selected.get("deviceStrategy") or "",
            "job_type": selected.get("job_type") or selected.get("type") or "",
            "type": selected.get("type") or selected.get("job_type") or "",
            "run_mode": selected.get("run_mode") or "",
            "yaml_content": yaml_content
        }
    })


# ── Agent Runs 列表 ─────────────────────────────────────────────────

@route_get("/api/agent-runs")
def _get_agent_runs(handler, qs):
    limit = safe_int((qs or {}).get("limit", ["20"])[0] if isinstance((qs or {}).get("limit"), list) else (qs or {}).get("limit"), 20)
    handler._json({"ok": True, "runs": list_agent_runs(limit or 20)})


# ── Agent Run 详情（正则匹配）──────────────────────────────────────

@route_get_regex(r"^/api/agent-runs/([^/]+)$")
def _get_agent_run_detail(handler, qs, match):
    run_id = urllib.parse.unquote(match.group(1))
    run = get_agent_run(run_id)
    if not run:
        handler._json({"ok": False, "error": "Agent Run 不存在"}, 404)
        return
    handler._json({"ok": True, "run": run})


# ── Agent 工具列表 ──────────────────────────────────────────────────

@route_get("/api/agent-tools")
def _get_agent_tools(handler, qs):
    from task_server.services.agent_service import list_agent_tools
    tools = list_agent_tools()
    handler._json({"ok": True, "tools": tools})


# ── 报告列表 ────────────────────────────────────────────────────────

@route_get("/api/reports")
def _get_reports(handler, qs):
    from task_server.services.report_service import list_reports
    try:
        limit = int(qs.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    status = qs.get("status") or None
    reports = list_reports(limit=limit, status=status)
    handler._json({"ok": True, "reports": reports, "total": len(reports)})


# ── Trace / DAG Debugger ─────────────────────────────────────────────

def _debug_auth_required(handler):
    return _require_user_auth(handler)


def _execution_facade():
    return ExecutionFacade()


@route_get("/api/debug/traces")
def _get_debug_traces(handler, qs):
    if _debug_auth_required(handler):
        return
    trace_id = qs.get("id") or qs.get("trace_id") or qs.get("traceId") or ""
    facade = _execution_facade()
    if trace_id:
        trace = facade.get_trace(trace_id)
        if not trace:
            handler._json({"ok": False, "error": "Trace 不存在"}, 404)
            return
        handler._json({"ok": True, "trace": trace})
        return
    try:
        limit = int(qs.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    handler._json(facade.list_traces(limit=limit))


@route_get("/api/debug/trace-view")
def _get_debug_trace_view(handler, qs):
    if _debug_auth_required(handler):
        return
    trace_id = qs.get("id") or qs.get("trace_id") or qs.get("traceId") or ""
    handler._html(_execution_facade().render_trace_view(trace_id))


@route_get("/api/debug/snapshots")
def _get_debug_snapshots(handler, qs):
    if _debug_auth_required(handler):
        return
    snapshot_id = qs.get("id") or qs.get("snapshotId") or ""
    facade = _execution_facade()
    if snapshot_id:
        snapshot = facade.get_snapshot(snapshot_id)
        if not snapshot:
            handler._json({"ok": False, "error": "快照不存在"}, 404)
            return
        handler._json({"ok": True, "snapshot": snapshot})
        return
    try:
        limit = int(qs.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    handler._json(facade.list_snapshots(limit=limit))


@route_post("/api/debug/snapshots")
def _post_debug_snapshots(handler, qs):
    if _debug_auth_required(handler):
        return
    d = handler._body()
    trace_id = str(d.get("traceId") or d.get("trace_id") or d.get("id") or "").strip()
    trace = d.get("trace") if isinstance(d.get("trace"), dict) else None
    if not trace and not trace_id:
        handler._json({"ok": False, "error": "traceId 不能为空"}, 400)
        return
    result = _execution_facade().save_snapshot_from_trace(
        trace_id,
        trace=trace,
        context=d.get("context") if isinstance(d.get("context"), dict) else {},
    )
    status = int(result.pop("status", 200) or 200)
    handler._json(result, status)


def _debug_execution_enabled(handler):
    if APP_ENV == "prod" and not TASK_ENABLE_DEBUG_EXECUTION:
        handler._json({
            "ok": False,
            "error": "生产环境未开启 Debug 执行接口",
            "detail": "如确需在生产环境调用 debug 执行，请设置 TASK_ENABLE_DEBUG_EXECUTION=1 后重启服务。",
        }, 403)
        return False
    return True


@route_post("/api/debug/replay")
def _post_debug_replay(handler, qs):
    if _debug_auth_required(handler):
        return
    d = handler._body()
    snapshot_id = str(d.get("snapshotId") or d.get("snapshot_id") or d.get("id") or "").strip()
    dry_run = safe_bool(d.get("dryRun", d.get("dry_run", True)))
    if not dry_run and not _debug_execution_enabled(handler):
        return
    result = _execution_facade().replay_snapshot(snapshot_id, dry_run=dry_run)
    status = int(result.pop("status", 200) or 200)
    handler._json(result, status)


@route_post("/api/debug/diff")
def _post_debug_diff(handler, qs):
    if _debug_auth_required(handler):
        return
    d = handler._body()
    a_id = str(d.get("a") or d.get("snapshotA") or "").strip()
    b_id = str(d.get("b") or d.get("snapshotB") or "").strip()
    result = _execution_facade().diff_snapshots(a_id, b_id)
    status = int(result.pop("status", 200) or 200)
    handler._json(result, status)


@route_post("/api/debug/dag/run")
def _post_debug_dag_run(handler, qs):
    if _debug_auth_required(handler):
        return
    if not _debug_execution_enabled(handler):
        return
    d = handler._body()
    ctx = d.get("context") if isinstance(d.get("context"), dict) else dict(d)
    result = _execution_facade().run_dag(d, ctx)
    handler._json({"ok": True, "result": result})


@route_post("/api/debug/dag/parallel")
def _post_debug_dag_parallel(handler, qs):
    if _debug_auth_required(handler):
        return
    if not _debug_execution_enabled(handler):
        return
    d = handler._body()
    ctx = d.get("context") if isinstance(d.get("context"), dict) else dict(d)
    result = _execution_facade().run_parallel(d, ctx)
    handler._json({"ok": True, "result": result})


@route_get("/api/debug/execution/modes")
def _get_debug_execution_modes(handler, qs):
    if _debug_auth_required(handler):
        return
    handler._json(_execution_facade().available_modes())


@route_post("/api/debug/execution/run")
def _post_debug_execution_run(handler, qs):
    if _debug_auth_required(handler):
        return
    if not _debug_execution_enabled(handler):
        return
    d = handler._body()
    mode = str(d.get("mode") or d.get("executionCoreMode") or "local").strip()
    ctx = d.get("context") if isinstance(d.get("context"), dict) else dict(d)
    result = _execution_facade().run(ctx, mode=mode)
    handler._json({"ok": bool(result.get("ok", True)), "result": result})


@route_post("/api/debug/execution/shadow-compare")
def _post_debug_execution_shadow_compare(handler, qs):
    if _debug_auth_required(handler):
        return
    d = handler._body()
    ctx = d.get("context") if isinstance(d.get("context"), dict) else dict(d)
    modes = d.get("shadowModes") or d.get("shadow_modes") or ["dag", "parallel"]
    if not isinstance(modes, list):
        modes = ["dag", "parallel"]
    dry_run = safe_bool(d.get("dryRun", d.get("dry_run", True)), True)
    if not dry_run and not _debug_execution_enabled(handler):
        return
    result = _execution_facade().shadow_compare(ctx, shadow_modes=modes, dry_run=dry_run)
    handler._json({"ok": True, "result": result})


# ── Assets 前缀匹配 ─────────────────────────────────────────────────

@route_get_prefix("/api/assets/")
def _get_assets_by_id(handler, qs, path):
    case_set_id = path.split("/")[-1]
    try:
        meta = read_json_file(asset_meta_path(case_set_id), default=None)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    if not meta:
        handler._json({"ok": False, "error": "资产不存在"}, 404)
        return
    handler._json({"ok": True, "asset": meta})


# ── Cases 前缀匹配（GET）────────────────────────────────────────────

@route_get_prefix("/api/cases/")
def _get_cases_by_id(handler, qs, path):
    case_set_id = path.split("/")[-1]
    try:
        payload = read_json_file(cases_path(case_set_id), default=None)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    if not payload:
        handler._json({"ok": False, "error": "用例集不存在"}, 404)
        return
    handler._json({"ok": True, "case_set_id": case_set_id, "cases": payload})


# ── Cases 路由（精确匹配，覆盖旧注释路由）──────────────────────────

@route_get("/api/cases")
def _get_cases_list(handler, qs):
    from task_server.services.case_service import list_cases_by_module, list_all_cases
    module = str(qs.get('module') or '').strip()
    keyword = str(qs.get('q') or qs.get('keyword') or '').strip().lower()
    page = max(1, safe_int(qs.get('page'), 1))
    page_size = max(1, min(200, safe_int(qs.get('pageSize') or qs.get('page_size'), 50)))
    return_all = safe_bool(qs.get('all') or qs.get('includeAll'))
    cases = list_cases_by_module(module) if module else list_all_cases()
    if keyword:
        def match_case(case):
            if not isinstance(case, dict):
                return False
            text = " ".join(str(case.get(key) or "") for key in (
                "caseId", "appName", "module", "file", "taskName", "requirement", "yamlPath", "riskLevel"
            )).lower()
            return keyword in text
        cases = [case for case in cases if match_case(case)]
    total = len(cases)
    if return_all:
        page_cases = cases
        page = 1
        page_size = total or page_size
    else:
        start = (page - 1) * page_size
        page_cases = cases[start:start + page_size]
    handler._json({
        'ok': True,
        'cases': page_cases,
        'total': total,
        'page': page,
        'pageSize': page_size,
        'hasMore': (page * page_size) < total if not return_all else False,
    })


# ── 平台状态 ────────────────────────────────────────────────────────

@route_get("/api/platform/status")
def _get_platform_status(handler, qs):
    from task_server.services.platform_service import get_platform_status
    status = get_platform_status()
    handler._json(status)


# ── Tasks 路由 ──────────────────────────────────────────────────────

@route_get("/api/tasks")
def _get_tasks(handler, qs):
    from task_server.services.task_center_service import list_tasks
    task_type = str(qs.get('type') or '').strip() or None
    status = str(qs.get('status') or '').strip() or None
    try:
        limit = int(qs.get('limit') or 50)
    except (TypeError, ValueError):
        limit = 50
    tasks = list_tasks(task_type=task_type, status=status, limit=limit)
    handler._json({'ok': True, 'tasks': tasks, 'total': len(tasks)})


# ── API Testing ─────────────────────────────────────────────────────

@route_get("/api/api-testing/overview")
def _get_api_testing_overview(handler, qs):
    from task_server.services import api_asset_service, api_report_service, api_source_service, api_test_plan_service, metersphere_service
    snapshots = api_asset_service.list_api_snapshots(limit=5)
    assets = api_asset_service.list_api_assets(limit=5)
    sources = api_source_service.list_api_sources()
    latest_snapshot_id = snapshots[0].get("snapshot_id") if snapshots else ""
    endpoints = api_asset_service.list_api_endpoints(latest_snapshot_id) if latest_snapshot_id else []
    plans = api_test_plan_service.list_api_test_plans(limit=5)
    reports = api_report_service.list_api_reports(limit=5)
    handler._json({
        "ok": True,
        "summary": {
            "snapshot_count": len(snapshots),
            "endpoint_count": len(endpoints),
            "plan_count": len(plans),
            "report_count": len(reports),
            "source_count": len(sources),
            "asset_count": len(assets),
        },
        "snapshots": snapshots,
        "latest_snapshot_id": latest_snapshot_id,
        "endpoints": endpoints[:20],
        "plans": plans,
        "reports": reports,
        "sources": sources,
        "assets": assets,
        "metersphere": metersphere_service.metersphere_config(masked=True),
    })


@route_get("/api/api-testing/assets")
def _get_api_testing_assets(handler, qs):
    from task_server.services import api_asset_service, api_module_service
    snapshots = api_asset_service.list_api_snapshots(limit=safe_int(qs.get("limit"), 20) or 20)
    assets = api_asset_service.list_api_assets(limit=safe_int(qs.get("limit"), 20) or 20)
    requested_snapshot_id = str(qs.get("snapshot_id") or qs.get("snapshotId") or "").strip()
    requested_asset_id = str(qs.get("asset_id") or qs.get("assetId") or "").strip()
    requested_source_id = str(qs.get("source_id") or qs.get("sourceId") or "").strip()
    snapshot = api_asset_service.get_api_snapshot(requested_snapshot_id) if requested_snapshot_id else {}
    if requested_snapshot_id and not snapshot:
        handler._json({"ok": False, "error": "API revision 不存在"}, 404)
        return
    requested_asset = api_asset_service.get_api_asset(requested_asset_id) if requested_asset_id else {}
    if requested_asset_id and not requested_asset:
        handler._json({"ok": False, "error": "API asset 不存在"}, 404)
        return
    asset = {}
    if snapshot.get("asset_id"):
        asset = api_asset_service.get_api_asset(str(snapshot.get("asset_id") or ""))
        snapshot_source_id = str(snapshot.get("source_id") or "").strip()
        snapshot_asset_id = str(snapshot.get("asset_id") or "").strip()
        asset_source_id = str(asset.get("source_id") or "").strip()
        if (
            not asset
            or not snapshot_source_id
            or asset_source_id != snapshot_source_id
            or (requested_asset_id and requested_asset_id != snapshot_asset_id)
            or (requested_source_id and requested_source_id != snapshot_source_id)
        ):
            handler._json({"ok": False, "error": "API asset、revision 与 source 不匹配"}, 404)
            return
    elif requested_asset_id:
        asset = requested_asset
    elif requested_source_id:
        asset = next(
            (item for item in assets if str(item.get("source_id") or "") == requested_source_id),
            {},
        )
        if asset:
            asset = api_asset_service.get_api_asset(str(asset.get("asset_id") or ""))
    elif not requested_snapshot_id and assets:
        asset = api_asset_service.get_api_asset(str(assets[0].get("asset_id") or ""))
    if requested_source_id and asset and str(asset.get("source_id") or "").strip() != requested_source_id:
        handler._json({"ok": False, "error": "API asset 不属于当前 source"}, 404)
        return
    snapshot_id = requested_snapshot_id
    if not snapshot_id and asset:
        snapshot_id = str(asset.get("active_revision_id") or "").strip()
    if not snapshot_id and snapshots and not (requested_asset_id or requested_source_id):
        snapshot_id = str(snapshots[0].get("snapshot_id") or "").strip()
    if not snapshot and snapshot_id:
        snapshot = api_asset_service.get_api_snapshot(snapshot_id)
    if not asset and snapshot.get("asset_id"):
        asset = api_asset_service.get_api_asset(str(snapshot.get("asset_id") or ""))
    if snapshot:
        snapshot_source_id = str(snapshot.get("source_id") or "").strip()
        snapshot_asset_id = str(snapshot.get("asset_id") or "").strip()
        if snapshot_source_id or snapshot_asset_id:
            asset_source_id = str(asset.get("source_id") or "").strip()
            if (
                not asset
                or not snapshot_source_id
                or not snapshot_asset_id
                or str(asset.get("asset_id") or "").strip() != snapshot_asset_id
                or asset_source_id != snapshot_source_id
                or (requested_source_id and requested_source_id != snapshot_source_id)
                or (requested_asset_id and requested_asset_id != snapshot_asset_id)
            ):
                handler._json({"ok": False, "error": "API asset、revision 与 source 不匹配"}, 404)
                return
        elif requested_source_id or requested_asset_id:
            handler._json({"ok": False, "error": "API asset、revision 与 source 不匹配"}, 404)
            return
    elif requested_source_id or requested_asset_id:
        handler._json({"ok": False, "error": "API asset 没有可读取的活动 revision"}, 404)
        return
    endpoints = api_asset_service.list_api_endpoints(snapshot_id) if snapshot_id else []
    asset_id = str(asset.get("asset_id") or "")
    revisions = api_asset_service.list_api_revisions(asset_id, limit=50) if asset_id else []
    handler._json({
        "ok": True,
        "snapshots": snapshots,
        "snapshot": snapshot,
        "endpoints": endpoints,
        "assets": assets,
        "asset": asset,
        "revisions": revisions,
        "source_id": str((asset or snapshot).get("source_id") or requested_source_id),
        "module_summary": api_module_service.module_summary(endpoints),
        "business_lines": api_module_service.business_line_summary(endpoints),
    })


@route_get("/api/api-testing/sources")
def _get_api_testing_sources(handler, qs):
    if _require_user_auth(handler):
        return
    from task_server.services import api_source_service, api_sync_service
    sources = api_source_service.list_api_sources()
    handler._json({
        "ok": True,
        "sources": sources,
        "syncs": api_sync_service.list_api_syncs(
            limit=safe_int(qs.get("limit"), 20) or 20,
            source_id=str(qs.get("source_id") or qs.get("sourceId") or "").strip(),
        ),
    })


@route_get_regex(r"^/api/api-testing/syncs/([^/]+)$")
def _get_api_testing_sync(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_sync_service
    sync_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    sync = api_sync_service.get_api_sync(sync_id)
    if not sync:
        handler._json({"ok": False, "error": "API sync 不存在"}, 404)
        return
    handler._json({"ok": True, "sync": sync})


@route_get_regex(r"^/api/api-testing/sources/([^/]+)/execution-binding$")
def _get_api_testing_source_execution_binding(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_source_service, api_workspace_service, metersphere_service
    source_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    if not api_source_service.get_api_source(source_id, masked=True):
        handler._json({"ok": False, "error": "API source 不存在"}, 404)
        return
    requested_project_id = str(
        qs.get("project_id") or qs.get("projectId") or ""
    ).strip()
    if requested_project_id:
        try:
            options = metersphere_service.metersphere_project_options(
                source_id,
                requested_project_id,
                force=safe_bool(qs.get("force") or qs.get("refresh"), False),
            )
        except (
            metersphere_service.MeterSphereV365ContractError,
            ValueError,
        ) as exc:
            handler._json({"ok": False, "error": str(exc)}, 400)
            return
        handler._json({
            "ok": True,
            "source_id": source_id,
            "binding": api_workspace_service.get_api_workspace_binding(
                source_id,
                allow_legacy=True,
            ),
            **options,
            "selected_project_id": requested_project_id,
        })
        return
    context = metersphere_service.metersphere_execution_context(
        force=safe_bool(qs.get("force") or qs.get("refresh"), False),
        source_id=source_id,
    )
    handler._json({
        "ok": True,
        "source_id": source_id,
        "binding": api_workspace_service.get_api_workspace_binding(source_id, allow_legacy=True),
        "context": context,
    })


@route_get_regex(r"^/api/api-testing/assets/([^/]+)/revisions$")
def _get_api_testing_asset_revisions(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_asset_service
    asset_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    asset = api_asset_service.get_api_asset(asset_id)
    if not asset:
        handler._json({"ok": False, "error": "API asset 不存在"}, 404)
        return
    handler._json({
        "ok": True,
        "asset": asset,
        "revisions": api_asset_service.list_api_revisions(asset_id, limit=safe_int(qs.get("limit"), 50) or 50),
    })


@route_get_regex(r"^/api/api-testing/assets/([^/]+)/diff$")
def _get_api_testing_asset_diff(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_schema_diff_service
    asset_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    try:
        diff = api_schema_diff_service.get_asset_revision_diff(
            asset_id,
            from_revision_id=str(qs.get("from") or qs.get("from_revision_id") or "").strip(),
            to_revision_id=str(qs.get("to") or qs.get("to_revision_id") or "").strip(),
        )
        handler._json({"ok": True, "diff": diff})
    except ValueError as exc:
        handler._json({"ok": False, "error": str(exc)}, 404)


@route_get_regex(r"^/api/api-testing/assets/([^/]+)/impact$")
def _get_api_testing_asset_impact(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_schema_diff_service
    asset_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    try:
        diff = api_schema_diff_service.get_asset_revision_diff(
            asset_id,
            from_revision_id=str(qs.get("from") or qs.get("from_revision_id") or "").strip(),
            to_revision_id=str(qs.get("revision_id") or qs.get("revisionId") or qs.get("to") or "").strip(),
        )
        handler._json({"ok": True, "asset_id": asset_id, "impact": diff.get("impact") or {}, "summary": diff.get("summary") or {}})
    except ValueError as exc:
        handler._json({"ok": False, "error": str(exc)}, 404)


@route_get("/api/api-testing/plans")
def _get_api_testing_plans(handler, qs):
    from task_server.services import api_test_plan_service
    handler._json({
        "ok": True,
        "plans": api_test_plan_service.list_api_test_plans(
            limit=safe_int(qs.get("limit"), 20) or 20,
            source_id=str(qs.get("source_id") or qs.get("sourceId") or "").strip(),
        ),
    })


@route_get_regex(r"^/api/api-testing/plans/([^/]+)$")
def _get_api_testing_plan_detail(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_test_plan_service
    plan_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    plan = api_test_plan_service.get_api_test_plan(
        plan_id,
        source_id=str(qs.get("source_id") or qs.get("sourceId") or "").strip(),
    )
    if not plan:
        handler._json({"ok": False, "error": "API 测试计划不存在"}, 404)
        return
    handler._json({"ok": True, "plan": plan})


@route_get_regex(r"^/api/api-testing/plan-generations/([^/]+)$")
def _get_api_testing_plan_generation(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_plan_generation_service
    generation_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    generation = api_plan_generation_service.get_api_plan_generation(generation_id)
    selected_source_id = str(qs.get("source_id") or qs.get("sourceId") or "").strip()
    if (
        not generation
        or (
            selected_source_id
            and str(generation.get("source_id") or "") != selected_source_id
        )
    ):
        handler._json({"ok": False, "error": "API plan generation 不存在"}, 404)
        return
    handler._json({"ok": True, "generation": generation})


@route_get("/api/api-testing/metersphere/config")
def _get_api_testing_metersphere_config(handler, qs):
    from task_server.services import metersphere_service
    handler._json({"ok": True, "config": metersphere_service.metersphere_config(masked=True)})


@route_get("/api/api-testing/metersphere/execution-context")
def _get_api_testing_metersphere_execution_context(handler, qs):
    if _require_user_auth(handler):
        return
    from task_server.services import metersphere_service
    context = metersphere_service.metersphere_execution_context(
        force=safe_bool(qs.get("force") or qs.get("refresh"), False),
        source_id=str(qs.get("source_id") or qs.get("sourceId") or "").strip(),
    )
    handler._json(context)


@route_get_regex(r"^/api/api-testing/metersphere/executions/([^/]+)$")
def _get_api_testing_metersphere_execution(handler, qs, match):
    from task_server.services import metersphere_service
    execution_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    try:
        execution = metersphere_service.get_metersphere_execution(
            execution_id,
            refresh=not safe_bool(qs.get("cached"), False),
        )
        handler._json({"ok": True, "execution": execution})
    except metersphere_service.MeterSphereExecutionNotFound as exc:
        handler._json({"ok": False, "error": str(exc)}, 404)


@route_get("/api/api-testing/reports")
def _get_api_testing_reports(handler, qs):
    from task_server.services import api_report_service
    handler._json({
        "ok": True,
        "reports": api_report_service.list_api_reports(
            limit=safe_int(qs.get("limit"), 20) or 20,
            source_id=str(qs.get("source_id") or qs.get("sourceId") or "").strip(),
            business_line=str(
                qs.get("business_line") or qs.get("businessLine") or ""
            ).strip(),
        ),
    })


# ══════════════════════════════════════════════════════════════════════
#  POST 路由注册
# ══════════════════════════════════════════════════════════════════════

# ── 登录 ────────────────────────────────────────────────────────────

@route_post("/api/auth/login")
def _post_auth_login(handler, qs):
    from task_server.auth import login
    try:
        d = handler._body()
    except Exception:
        d = {}
    username = str(d.get("username") or "").strip()
    password = str(d.get("password") or "")
    ok, result = login(username, password)
    if not ok:
        handler._json({"ok": False, "error": f"账号或密码错误"}, 401)
        return
    handler._json({"ok": True, "user": username, "token": result, "expires_in": max(300, TASK_SESSION_TTL_SECONDS)})


# ── 登出 ────────────────────────────────────────────────────────────

@route_post("/api/auth/logout")
def _post_auth_logout(handler, qs):
    token = bearer_token(handler.headers)
    if token:
        REVOKED_SESSION_TOKENS.add(token)
    handler._json({"ok": True})


# ── API Testing ─────────────────────────────────────────────────────

@route_post("/api/api-testing/sources")
def _post_api_testing_sources(handler, qs):
    if _require_user_auth(handler):
        return
    from task_server.services import api_source_service, api_sync_service
    try:
        data = handler._body()
        source_id = str(
            data.get("source_id") or data.get("sourceId") or ""
        ).strip()
        previous = (
            api_source_service.get_api_source(source_id, masked=False)
            if source_id
            else {}
        )
        previous_fingerprint = (
            api_source_service.source_config_fingerprint(previous)
            if previous
            else ""
        )
        source = api_source_service.save_api_source(data)
        current = api_source_service.get_api_source(
            str(source.get("source_id") or ""),
            masked=False,
        )
        current_fingerprint = api_source_service.source_config_fingerprint(current)
        response = {"ok": True, "source": source}
        status = 200
        should_sync = bool(
            source.get("source_type") == "apifox"
            and source.get("configured")
            and source.get("sync_enabled")
            and current_fingerprint != previous_fingerprint
        )
        if should_sync:
            try:
                sync = api_sync_service.start_api_source_sync(
                    str(source.get("source_id") or ""),
                    spawn=True,
                    trigger="configuration",
                )
                response["sync"] = sync
                status = 202 if sync.get("created") else 200
            except ValueError as exc:
                response["sync_error"] = str(exc)
            except Exception:
                response["sync_error"] = "接口配置已保存，自动同步排队失败，请点击重试"
        handler._json(response, status)
    except ValueError as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)


@route_post_regex(r"^/api/api-testing/sources/([^/]+)/sync$")
def _post_api_testing_source_sync(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_source_service, api_sync_service
    source_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    if not api_source_service.get_api_source(source_id, masked=True):
        handler._json({"ok": False, "error": "API source 不存在"}, 404)
        return
    try:
        sync = api_sync_service.start_api_source_sync(source_id, spawn=True, trigger="manual")
        handler._json({"ok": True, "sync": sync}, 202 if sync.get("created") else 200)
    except ValueError as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)


@route_post_regex(r"^/api/api-testing/sources/([^/]+)/execution-binding$")
def _post_api_testing_source_execution_binding(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_source_service, api_workspace_service, metersphere_service
    source_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    if not api_source_service.get_api_source(source_id, masked=True):
        handler._json({"ok": False, "error": "API source 不存在"}, 404)
        return
    data = handler._body()
    project_id = str(data.get("project_id") or data.get("projectId") or "").strip()
    environment_id = str(data.get("environment_id") or data.get("environmentId") or "").strip()
    expected_binding_fingerprint = (
        data.get("expected_binding_fingerprint")
        if "expected_binding_fingerprint" in data
        else data.get("expectedBindingFingerprint")
        if "expectedBindingFingerprint" in data
        else None
    )
    client_session_id = str(
        data.get("client_session_id") or data.get("clientSessionId") or ""
    ).strip()
    client_intent_id = (
        data.get("client_intent_id")
        if "client_intent_id" in data
        else data.get("clientIntentId")
    )
    cfg = metersphere_service._load_raw_config()
    cfg["project_id"] = project_id
    cfg["environment_id"] = environment_id
    adapter, probe, supported = metersphere_service._v365_adapter_probe(cfg)
    if not supported:
        handler._json({"ok": False, "error": "MeterSphere v3.6.5 实时校验不可用"}, 400)
        return
    try:
        projects = adapter.list_projects()
        project = next((item for item in projects if item.get("id") == project_id), {})
        environments = adapter.list_environments(project_id)
        environment = next((item for item in environments if item.get("id") == environment_id), {})
    except (metersphere_service.MeterSphereV365ContractError, ValueError) as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)
        return
    if not project or not environment:
        handler._json({"ok": False, "error": "MeterSphere 项目或环境不存在、已停用或不匹配"}, 400)
        return
    try:
        binding = api_workspace_service.save_api_workspace_binding(
            source_id,
            project_id,
            environment_id,
            project_name=str(project.get("name") or ""),
            environment_name=str(environment.get("name") or ""),
            connection_identity=metersphere_service._api_auth_connection_identity(cfg),
            expected_binding_fingerprint=expected_binding_fingerprint,
            client_session_id=client_session_id,
            client_intent_id=client_intent_id,
        )
    except api_workspace_service.ApiWorkspaceBindingConflict as exc:
        handler._json({"ok": False, "error": str(exc)}, 409)
        return
    except ValueError as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)
        return
    handler._json({"ok": True, "binding": binding, "version": probe.get("version") or ""})


@route_post_regex(r"^/api/api-testing/sources/([^/]+)/auth-binding$")
def _post_api_testing_source_auth_binding(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_source_service, metersphere_service
    source_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    if not api_source_service.get_api_source(source_id, masked=True):
        handler._json({"ok": False, "error": "API source 不存在"}, 404)
        return
    try:
        data = handler._body()
        binding = metersphere_service.save_api_auth_binding(
            source_id,
            str(data.get("auth_type") or data.get("authType") or "").strip(),
            str(data.get("header_name") or data.get("headerName") or "").strip(),
            str(data.get("secret") or ""),
            expected_project_id=str(
                data.get("expected_project_id") or data.get("expectedProjectId") or ""
            ).strip(),
            expected_environment_id=str(
                data.get("expected_environment_id")
                or data.get("expectedEnvironmentId")
                or ""
            ).strip(),
            expected_binding_version=(
                data.get("expected_binding_version")
                if "expected_binding_version" in data
                else data.get("expectedBindingVersion")
                if "expectedBindingVersion" in data
                else None
            ),
            expected_profile_version=(
                data.get("expected_profile_version")
                if "expected_profile_version" in data
                else data.get("expectedProfileVersion")
                if "expectedProfileVersion" in data
                else None
            ),
        )
        handler._json({"ok": True, "binding": binding})
    except metersphere_service.MeterSphereAuthConflict as exc:
        handler._json({"ok": False, "error": str(exc)}, 409)
    except ValueError as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)


@route_delete_regex(r"^/api/api-testing/sources/([^/]+)/auth-binding$")
def _delete_api_testing_source_auth_binding(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_source_service, metersphere_service
    source_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    if not api_source_service.get_api_source(source_id, masked=True):
        handler._json({"ok": False, "error": "API source 不存在"}, 404)
        return
    try:
        data = handler._body()
        binding = metersphere_service.clear_api_auth_binding(
            source_id,
            expected_project_id=str(
                data.get("expected_project_id") or data.get("expectedProjectId") or ""
            ).strip(),
            expected_environment_id=str(
                data.get("expected_environment_id")
                or data.get("expectedEnvironmentId")
                or ""
            ).strip(),
            expected_binding_version=(
                data.get("expected_binding_version")
                if "expected_binding_version" in data
                else data.get("expectedBindingVersion")
                if "expectedBindingVersion" in data
                else None
            ),
            expected_profile_version=(
                data.get("expected_profile_version")
                if "expected_profile_version" in data
                else data.get("expectedProfileVersion")
                if "expectedProfileVersion" in data
                else None
            ),
        )
        handler._json({"ok": True, "binding": binding})
    except metersphere_service.MeterSphereAuthConflict as exc:
        handler._json({"ok": False, "error": str(exc)}, 409)
    except ValueError as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)

@route_post("/api/api-testing/openapi/import")
def _post_api_testing_openapi_import(handler, qs):
    from task_server.services import api_asset_service
    try:
        d = handler._body()
        document = d.get("document") or d.get("openapi") or d.get("content") or d.get("raw") or {}
        snapshot = api_asset_service.import_openapi_document(
            str(d.get("name") or "").strip(),
            document,
            str(d.get("filename") or "").strip(),
        )
        handler._json({"ok": True, "snapshot": snapshot, "endpoints": snapshot.get("endpoints") or []})
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)


@route_post("/api/api-testing/plans/generate")
def _post_api_testing_plans_generate(handler, qs):
    from task_server.services import api_test_plan_service
    try:
        d = handler._body()
        plan = api_test_plan_service.generate_api_test_plan(
            str(d.get("snapshot_id") or d.get("snapshotId") or "").strip(),
            d.get("endpoint_ids") or d.get("endpointIds") or [],
            model_config=d.get("model_config") or d.get("modelConfig") or None,
            use_ai=d.get("use_ai") if "use_ai" in d else d.get("useAi"),
        )
        handler._json({"ok": True, "plan": plan})
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)


@route_post("/api/api-testing/plan-generations")
def _post_api_testing_plan_generations(handler, qs):
    if _require_user_auth(handler):
        return
    from task_server.services import api_plan_generation_service
    try:
        data = handler._body()
        generation = api_plan_generation_service.start_api_plan_generation(
            str(data.get("source_id") or data.get("sourceId") or "").strip(),
            str(
                data.get("revision_id")
                or data.get("revisionId")
                or data.get("snapshot_id")
                or data.get("snapshotId")
                or ""
            ).strip(),
            data.get("endpoint_ids") or data.get("endpointIds") or [],
            data.get("module_paths") or data.get("modulePaths") or [],
            model_config=data.get("model_config") or data.get("modelConfig") or None,
            spawn=True,
        )
        handler._json({"ok": True, "generation": generation}, 202)
    except ValueError as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)


@route_post_regex(r"^/api/api-testing/plan-generations/([^/]+)/retry$")
def _post_api_testing_plan_generation_retry(handler, qs, match):
    if _require_user_auth(handler):
        return
    from task_server.services import api_plan_generation_service
    generation_id = urllib.parse.unquote(str(match.group(1) or "")).strip()
    try:
        generation = api_plan_generation_service.retry_api_plan_generation(
            generation_id,
            spawn=True,
        )
        handler._json({"ok": True, "generation": generation}, 202)
    except ValueError as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)


@route_post("/api/api-testing/plans/confirm")
def _post_api_testing_plans_confirm(handler, qs):
    from task_server.services import api_test_plan_service
    try:
        d = handler._body()
        plan = api_test_plan_service.confirm_api_test_plan(str(d.get("plan_id") or d.get("planId") or "").strip())
        handler._json({"ok": True, "plan": plan})
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)


@route_post("/api/api-testing/metersphere/config")
def _post_api_testing_metersphere_config(handler, qs):
    from task_server.services import metersphere_service
    try:
        config = metersphere_service.save_metersphere_config(handler._body())
        handler._json({"ok": True, "config": config})
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)


@route_post("/api/api-testing/metersphere/health")
def _post_api_testing_metersphere_health(handler, qs):
    from task_server.services import metersphere_service
    result = metersphere_service.metersphere_health()
    handler._json({"ok": bool(result.get("ok")), "result": result}, 200 if result.get("ok") else 400)


@route_post("/api/api-testing/metersphere/push")
def _post_api_testing_metersphere_push(handler, qs):
    from task_server.services import metersphere_service
    d = handler._body()
    result = metersphere_service.push_plan_to_metersphere(str(d.get("plan_id") or d.get("planId") or "").strip())
    handler._json({"ok": bool(result.get("ok")), "result": result}, 200 if result.get("ok") else 400)


@route_post("/api/api-testing/metersphere/run")
def _post_api_testing_metersphere_run(handler, qs):
    from task_server.services import metersphere_service
    d = handler._body()
    result = metersphere_service.create_metersphere_run(
        str(d.get("plan_id") or d.get("planId") or "").strip(),
        str(d.get("test_plan_id") or d.get("testPlanId") or "").strip(),
    )
    handler._json({"ok": bool(result.get("ok")), "result": result}, 200 if result.get("ok") else 400)


@route_post("/api/api-testing/metersphere/executions")
def _post_api_testing_metersphere_executions(handler, qs):
    from task_server.services import metersphere_service
    d = handler._body()
    try:
        execution = metersphere_service.start_metersphere_execution(
            str(d.get("plan_id") or d.get("planId") or "").strip(),
            str(d.get("test_plan_id") or d.get("testPlanId") or "").strip(),
        )
        handler._json({"ok": True, "execution": execution}, 202)
    except metersphere_service.MeterSphereExecutionConflict as exc:
        handler._json({"ok": False, "error": str(exc)}, 409)
    except metersphere_service.MeterSphereExecutionValidationError as exc:
        handler._json({"ok": False, "error": str(exc)}, 400)


@route_post("/api/api-testing/reports/pull")
def _post_api_testing_reports_pull(handler, qs):
    from task_server.services import metersphere_service
    d = handler._body()
    result = metersphere_service.pull_metersphere_report(
        str(d.get("run_id") or d.get("runId") or "").strip(),
        raw_report=d.get("raw_report") or d.get("rawReport") or None,
        execution_id=str(d.get("execution_id") or d.get("executionId") or "").strip(),
    )
    handler._json({"ok": bool(result.get("ok")), "result": result, "report": result.get("report")}, 200 if result.get("ok") else 400)


# ── 修复草稿保存 ────────────────────────────────────────────────────

@route_post("/api/repair-drafts")
def _post_repair_drafts(handler, qs):
    from task_server.services.repair_service import upsert_repair_draft
    try:
        d = handler._body()
    except Exception:
        d = {}
    try:
        draft = upsert_repair_draft(d)
        handler._json({"ok": True, "draft": draft})
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)


# ── 修复草稿拒绝 ────────────────────────────────────────────────────

@route_post("/api/repair-drafts/reject")
def _post_repair_drafts_reject(handler, qs):
    from task_server.services.repair_service import get_repair_draft, upsert_repair_draft
    try:
        d = handler._body()
    except Exception:
        d = {}
    draft_id = d.get("draftId") or d.get("draft_id")
    draft = get_repair_draft(draft_id)
    if not draft:
        handler._json({"ok": False, "error": "修复草稿不存在"}, 404)
        return
    draft["status"] = "REJECTED"
    draft["rejectReason"] = d.get("reason") or d.get("rejectReason") or ""
    draft["reject_reason"] = draft["rejectReason"]
    draft["rejectedAt"] = draft["rejected_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    draft = upsert_repair_draft(draft)
    handler._json({"ok": True, "draft": draft})


# ── 修复草稿应用 ────────────────────────────────────────────────────

@route_post("/api/repair-drafts/apply")
def _post_repair_drafts_apply(handler, qs):
    from task_server.services.repair_service import get_repair_draft, upsert_repair_draft
    try:
        d = handler._body()
    except Exception:
        d = {}
    draft_id = d.get("draftId") or d.get("draft_id")
    draft = get_repair_draft(draft_id)
    if not draft:
        handler._json({"ok": False, "error": "修复草稿不存在"}, 404)
        return
    if draft.get("status") not in ("DRAFTED", "WAIT_CONFIRM"):
        handler._json({"ok": False, "error": f"当前草稿状态不可应用：{draft.get('status')}"}, 400)
        return
    if not safe_bool(d.get("confirmApply") or d.get("confirm_apply")):
        handler._json({"ok": False, "error": "必须人工确认 confirmApply=true 后才能应用修复草稿"}, 400)
        return
    risk_hits = draft.get("riskHits") or draft.get("risk_hits") or []
    if risk_hits and not safe_bool(d.get("confirmRisk") or d.get("confirm_risk")):
        handler._json({"ok": False, "error": "修复草稿包含高风险动作，必须 confirmRisk=true"}, 400)
        return
    module = draft.get("module") or d.get("module") or ""
    file = clean_filename(draft.get("file") or d.get("file") or "")
    fixed_yaml = draft.get("fixedYaml") or draft.get("fixed_yaml") or ""
    if not module or not file:
        handler._json({"ok": False, "error": "草稿缺少 module/file，不能应用"}, 400)
        return
    if not str(fixed_yaml or "").strip():
        handler._json({"ok": False, "error": "草稿缺少 fixedYaml，不能应用"}, 400)
        return
    yaml_check = validate_midscene_yaml(fixed_yaml)
    yaml_executability = validate_midscene_yaml_executability(fixed_yaml)
    if not yaml_check.get("ok"):
        handler._json({"ok": False, "error": "YAML 校验未通过，不能应用", "yaml_check": yaml_check, "yaml_executability": yaml_executability}, 400)
        return
    try:
        fpath = safe_join(TASK_DIR, module, file)
        backup = save_file_version(module, file, reason="before_repair_draft_apply")
        write_text_file(fpath, fixed_yaml)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)
        return
    draft["status"] = "APPLIED"
    draft["appliedAt"] = draft["applied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    draft["backup"] = backup or {}
    draft["yaml_check"] = yaml_check
    draft["yaml_executability"] = yaml_executability
    draft = upsert_repair_draft(draft)
    handler._json({"ok": True, "applied": True, "draft": draft, "backup": backup, "yaml_check": yaml_check, "yaml_executability": yaml_executability})


# ── 报告清理（POST）─────────────────────────────────────────────────

@route_post("/api/reports/cleanup")
def _post_reports_cleanup(handler, qs):
    from task_server.services.report_service import cleanup_midscene_reports, report_cleanup_policy
    try:
        d = handler._body()
    except Exception:
        d = {}
    try:
        dry_run = safe_bool(d.get("dry_run") if "dry_run" in d else d.get("dryRun"), False)
        days = safe_int(d.get("days") or d.get("retention_days") or d.get("retentionDays"), REPORT_RETENTION_DAYS)
        min_keep = safe_int(d.get("min_keep") or d.get("minKeep"), REPORT_RETENTION_MIN_KEEP)
        handler._json(cleanup_midscene_reports(days, min_keep, dry_run=dry_run))
    except Exception as e:
        handler._json({"ok": False, "error": str(e), "policy": report_cleanup_policy()}, 500)


# ── 报告重建索引 ────────────────────────────────────────────────────

@route_post("/api/reports/rebuild-index")
def _post_reports_rebuild_index(handler, qs):
    from task_server.services.report_service import rebuild_index
    result = rebuild_index()
    handler._json(result)


# ── 报告上传（原始）─────────────────────────────────────────────────

@route_post("/report")
def _post_report(handler, qs):
    if handler.headers.get("x-token", "") != TOKEN:
        handler._text("Unauthorized", 401)
        return
    filename = urllib.parse.unquote(handler.headers.get("x-filename", "report.html"))
    filename = filename.replace("/", "_").replace("\\", "_")
    os.makedirs(REPORT_DIR, exist_ok=True)
    write_bytes_file(safe_join(REPORT_DIR, filename), handler._raw_body())
    handler._text(public_report_url(filename))


# ── 报告分片上传 ────────────────────────────────────────────────────

@route_post("/api/report/chunk")
def _post_report_chunk(handler, qs):
    if _require_runner_auth(handler):
        return
    try:
        d = handler._body()
        upload_id = clean_id(d.get("upload_id") or d.get("uploadId") or "", "report")
        filename = urllib.parse.unquote(d.get("filename") or "report.html").replace("/", "_").replace("\\", "_")
        index = safe_int(d.get("index"), -1)
        total = safe_int(d.get("total"), 0)
        content = d.get("contentBase64") or ""
        if not upload_id or index < 0 or total <= 0 or not content:
            handler._json({"ok": False, "error": "分片参数不完整"}, 400)
            return
        chunk_dir = safe_join(REPORT_DIR, ".chunks", upload_id)
        os.makedirs(chunk_dir, exist_ok=True)
        write_bytes_file(safe_join(chunk_dir, f"{index:05d}.part"), base64.b64decode(content))
        write_text_file(safe_join(chunk_dir, "filename.txt"), filename)
        handler._json({"ok": True, "upload_id": upload_id, "index": index, "total": total})
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)


# ── 报告分片完成 ────────────────────────────────────────────────────

@route_post("/api/report/chunk-finish")
def _post_report_chunk_finish(handler, qs):
    if _require_runner_auth(handler):
        return
    try:
        d = handler._body()
        upload_id = clean_id(d.get("upload_id") or d.get("uploadId") or "", "report")
        total = safe_int(d.get("total"), 0)
        chunk_dir = safe_join(REPORT_DIR, ".chunks", upload_id)
        filename_path = safe_join(chunk_dir, "filename.txt")
        if not upload_id or total <= 0 or not os.path.exists(filename_path):
            handler._json({"ok": False, "error": "分片上传不存在"}, 404)
            return
        filename = open(filename_path, encoding="utf-8").read().strip() or "report.html"
        final_path = safe_join(REPORT_DIR, filename)
        parts = [safe_join(chunk_dir, f"{index:05d}.part") for index in range(total)]
        for index, part in enumerate(parts):
            if not os.path.exists(part):
                handler._json({"ok": False, "error": f"缺少分片 {index}"}, 400)
                return
        tmp_final = final_path + f".tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp_final, "wb") as out:
                for part in parts:
                    with open(part, "rb") as f:
                        shutil.copyfileobj(f, out)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp_final, final_path)
        finally:
            if os.path.exists(tmp_final):
                try:
                    os.remove(tmp_final)
                except Exception:
                    pass
        shutil.rmtree(chunk_dir, ignore_errors=True)
        handler._json({"ok": True, "url": public_report_url(filename)})
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)


# ── Sonic 套件完成回调 ─────────────────────────────────────────────

@route_post("/api/sonic/suite-complete")
def _post_sonic_suite_complete(handler, qs):
    if _require_sonic_or_user_auth(handler, qs):
        return
    try:
        event = parse_sonic_suite_completion_payload(
            handler._raw_body(),
            handler.headers.get("Content-Type", "")
        )
        if not event.get("suite_name") and not event.get("result_id") and not event.get("total"):
            handler._json({"ok": False, "error": "未识别到 Sonic 测试套结束信息"}, 400)
            return
        result = register_sonic_suite_completion(event)
        handler._json({
            "ok": True,
            "suite_key": result.get("suite_key"),
            "duplicate": result.get("duplicate", False),
            "status": event.get("status"),
            "total": event.get("total"),
            "message": "已接收 Sonic 测试套结束事件，Task 平台将发送整套汇总通知"
        })
    except Exception as e:
        append_sonic_notify_log("sonic_suite_completion_error", {}, error=str(e))
        handler._json({"ok": False, "error": str(e)}, 500)


# ── Sonic 套件报告回调 ─────────────────────────────────────────────

@route_post("/api/sonic/suite-report")
def _post_sonic_suite_report(handler, qs):
    # 复用 suite-complete 逻辑
    return _post_sonic_suite_complete(handler, qs)


# ── Sonic 自定义机器人回调 ──────────────────────────────────────────

@route_post("/api/sonic/custom-robot")
def _post_sonic_custom_robot(handler, qs):
    return _post_sonic_suite_complete(handler, qs)


# ── 通用 POST 认证守卫 ──────────────────────────────────────────────
# 以下路由都需要认证（除了上面的 login/logout/sonic 回调等）

def _require_post_auth(handler, qs):
    """POST 请求通用认证检查，未通过返回 True。"""
    qs, path = handler._qs()
    if path.startswith("/api/") and path not in SONIC_SUITE_COMPLETION_PATHS:
        return _require_user_auth(handler)
    return False


# ── 转换用例 JSON / 生成 YAML ──────────────────────────────────────

@route_post("/api/convert-cases-json")
def _post_convert_cases_json(handler, qs):
    if _require_post_auth(handler, qs):
        return
    try:
        d = handler._body()
    except Exception as e:
        handler._json({"ok": False, "error": f"JSON 解析失败：{e}"}, 400)
        return
    _handle_convert_or_generate(handler, d)


@route_post("/api/generate-yaml")
def _post_generate_yaml(handler, qs):
    if _require_post_auth(handler, qs):
        return
    try:
        d = handler._body()
    except Exception as e:
        handler._json({"ok": False, "error": f"JSON 解析失败：{e}"}, 400)
        return
    _handle_convert_or_generate(handler, d)


@route_post("/api/yaml/dry-run")
def _post_yaml_dry_run(handler, qs):
    if _require_post_auth(handler, qs):
        return
    try:
        d = handler._body()
    except Exception as e:
        handler._json({"ok": False, "error": f"JSON 解析失败：{e}"}, 400)
        return
    try:
        result = dry_run_midscene_yaml(
            d.get("content") or d.get("yaml") or d.get("yamlText") or "",
            module=d.get("module") or "",
            file=d.get("file") or "",
            app_package=d.get("app_package") or d.get("appPackage") or "",
        )
        handler._json(result, 200 if result.get("ok") else 400)
    except FileNotFoundError as e:
        handler._json({"ok": False, "error": str(e)}, 404)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)


@route_post("/api/yaml/baseline-cache/refresh")
def _post_yaml_baseline_cache_refresh(handler, qs):
    if _require_post_auth(handler, qs):
        return
    try:
        cache = get_yaml_baseline_cache(force=True)
        handler._json({
            "ok": True,
            "fileCount": cache.get("fileCount", 0),
            "caseCount": cache.get("caseCount", 0),
            "generatedAt": cache.get("generatedAt"),
            "generatedAtText": cache.get("generatedAtText"),
            "fingerprint": cache.get("fingerprint"),
            "status": get_yaml_baseline_cache_status(force=False),
        })
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)


def _handle_convert_or_generate(handler, d):
    """公共逻辑：convert-cases-json 和 generate-yaml 共用。"""
    mod = d.get("module", "AI测试")
    raw_content = d.get("content") or d.get("casesJson") or ""
    if not raw_content:
        handler._json({"ok": False, "error": "测试用例 JSON 不能为空"}, 400)
        return
    try:
        payload = normalize_cases_payload(raw_content)
        converted_payload = split_automation_ready_cases(payload)
        app_package = d.get("app_package") or d.get("appPackage") or app_package_for_module(mod) or ""
        requested_file = clean_filename(d.get("file") or "")
        title, yaml_items = cases_to_separate_midscene_yamls(
            converted_payload,
            app_package=app_package,
            base_file=requested_file or f"task-{slug_for_file(payload.get('title') or 'case')}.yaml",
        )
        filename = yaml_items[0]["file"]
        yaml = yaml_items[0]["content"]
        yaml_files = [item["file"] for item in yaml_items]
        module_dir = safe_join(TASK_DIR, mod)
        os.makedirs(module_dir, exist_ok=True)
        for item in yaml_items:
            write_text_file(safe_join(module_dir, item["file"]), item["content"])
        case_set_id = d.get("case_set_id") or d.get("caseSetId") or new_case_set_id()
        converted_payload["id"] = case_set_id
        converted_payload["module"] = mod
        write_json_file(cases_path(case_set_id), converted_payload)
        yaml_checks = [{"file": item["file"], **validate_midscene_yaml(item["content"])} for item in yaml_items]
        yaml_exec_checks = [{"file": item["file"], **validate_midscene_yaml_executability(item["content"])} for item in yaml_items]
        yaml_static_checks = [
            {
                "file": item["file"],
                **(dry_run_midscene_yaml(item["content"], app_package=app_package).get("yamlStaticValidation") or {}),
            }
            for item in yaml_items
        ]
        yaml_check = {"ok": all(item.get("ok") for item in yaml_checks), "mode": "split_by_case", "file_count": len(yaml_items), "files": yaml_checks}
        yaml_executability = {
            "ok": all(item.get("ok") for item in yaml_exec_checks),
            "mode": "split_by_case",
            "file_count": len(yaml_items),
            "files": yaml_exec_checks,
            "taskCount": sum(int(item.get("taskCount") or 0) for item in yaml_exec_checks),
        }
        yaml_static_validation = {
            "ok": all(item.get("ok") for item in yaml_static_checks),
            "mode": "split_by_case",
            "file_count": len(yaml_items),
            "files": yaml_static_checks,
            "errorCount": sum(len(item.get("errors") or []) for item in yaml_static_checks),
            "warningCount": sum(len(item.get("warnings") or []) for item in yaml_static_checks),
            "executionLevelCounts": {},
        }
        for item in yaml_static_checks:
            level = item.get("executionLevel") or "draft"
            yaml_static_validation["executionLevelCounts"][level] = yaml_static_validation["executionLevelCounts"].get(level, 0) + 1
        summary = build_generation_summary(
            case_set_id, title, mod, filename, converted_payload,
            yaml_check=yaml_check, yaml_executability=yaml_executability
        )
        summary["yaml_files"] = yaml_files
        summary["yaml_file_count"] = len(yaml_files)
        summary["yaml_static_validation"] = yaml_static_validation
        summary_files = write_generation_summary(case_set_id, summary)
        for item in yaml_items:
            static_check = next((row for row in yaml_static_checks if row.get("file") == item["file"]), {})
            update_task_meta(mod, item["file"], {
                "last_case_set_id": case_set_id,
                "last_case_set_title": title,
                "last_generated_at": summary.get("generated_at"),
                "last_case_count": 1,
                "last_manual_case_count": len(converted_payload.get("manual_cases", [])),
                "execution_level": static_check.get("executionLevel") or "draft",
                "yaml_static_ok": bool(static_check.get("ok")),
                "yaml_static_errors": list(static_check.get("errors") or [])[:8],
                "yaml_static_warnings": list(static_check.get("warnings") or [])[:8],
            })
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)
        return
    handler._json({
        "ok": True,
        "case_set_id": case_set_id,
        "module": mod,
        "file": filename,
        "yamlFiles": yaml_files,
        "yamlFileCount": len(yaml_files),
        "content": yaml,
        "files": [{"file": item["file"], "content": item["content"], "title": item["title"]} for item in yaml_items],
        "caseCount": len(converted_payload["cases"]),
        "manualCaseCount": len(converted_payload.get("manual_cases", [])),
        "scenarioCount": len(converted_payload.get("scenarios", [])),
        "manual_cases": converted_payload.get("manual_cases", []),
        "summary": summary,
        "summaryFiles": summary_files,
        "yamlCheck": yaml_check,
        "yamlExecutability": yaml_executability,
        "yamlStaticValidation": yaml_static_validation
    })


# ── 资产上传 ────────────────────────────────────────────────────────

@route_post("/api/assets/upload")
def _post_assets_upload(handler, qs):
    d = handler._body()
    title = d.get("title") or "测试资产"
    module = d.get("module") or "AI测试"
    case_set_id = d.get("case_set_id") or new_case_set_id()
    files = d.get("files") or []
    try:
        meta = save_asset_files(case_set_id, title, module, files)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)
        return
    handler._json({"ok": True, "asset": meta})


# ── 知识库页面保存 ──────────────────────────────────────────────────

@route_post("/api/knowledge/page")
def _post_knowledge_page(handler, qs):
    d = handler._body()
    try:
        meta = save_knowledge_page(d)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)
        return
    handler._json({"ok": True, "page": meta})


# ── 知识库截图分析 ──────────────────────────────────────────────────

@route_post("/api/knowledge/analyze")
def _post_knowledge_analyze(handler, qs):
    d = handler._body()
    try:
        draft = analyze_knowledge_screenshot(d)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)
        return
    handler._json({"ok": True, "draft": draft})


# ── Figma 解析 ──────────────────────────────────────────────────────

@route_post("/api/figma/parse")
def _post_figma_parse(handler, qs):
    d = handler._body()
    try:
        result = parse_figma_design(d)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)
        return
    handler._json({"ok": True, **result})


@route_post("/api/figma/parse-async")
def _post_figma_parse_async(handler, qs):
    d = handler._body()
    job_id = generate_job_id()
    job = {
        "ok": True, "job_id": job_id, "type": "figma_parse",
        "status": "pending", "progress": 0, "step": "排队中",
        "message": "Figma 解析任务已创建",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_generate_job(job)
    worker = threading.Thread(target=run_figma_parse_job, args=(job_id, d), daemon=True)
    worker.start()
    handler._json({"ok": True, "job_id": job_id, "job": job})


@route_post("/api/figma/import")
def _post_figma_import(handler, qs):
    d = handler._body()
    try:
        result = import_figma_design(d)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)
        return
    handler._json({"ok": True, **result})


# ── 修复（异步）─────────────────────────────────────────────────────

@route_post("/api/file/repair-latest-async")
def _post_file_repair_latest_async(handler, qs):
    d = handler._body()
    job_id = generate_job_id()
    scope = "file"
    request_data = dict(d)
    request_data["scope"] = scope
    job = {
        "ok": True, "job_id": job_id, "type": "repair", "scope": scope,
        "status": "pending", "progress": 0, "step": "排队中",
        "message": "修复任务已创建",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_generate_job(job)
    worker = threading.Thread(target=run_repair_job, args=(job_id, request_data), daemon=True)
    worker.start()
    handler._json({"ok": True, "job_id": job_id, "job": job})


@route_post("/api/file/repair-task-latest-async")
def _post_file_repair_task_latest_async(handler, qs):
    d = handler._body()
    job_id = generate_job_id()
    scope = "task"
    request_data = dict(d)
    request_data["scope"] = scope
    job = {
        "ok": True, "job_id": job_id, "type": "repair", "scope": scope,
        "status": "pending", "progress": 0, "step": "排队中",
        "message": "修复任务已创建",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_generate_job(job)
    worker = threading.Thread(target=run_repair_job, args=(job_id, request_data), daemon=True)
    worker.start()
    handler._json({"ok": True, "job_id": job_id, "job": job})


# ── 用例生成 ────────────────────────────────────────────────────────

@route_post("/api/cases/generate")
def _post_cases_generate(handler, qs):
    d = handler._body()
    case_set_id = d.get("case_set_id")
    if not case_set_id:
        handler._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
        return
    meta = read_json_file(asset_meta_path(case_set_id), default=None)
    if not meta:
        handler._json({"ok": False, "error": "资产不存在"}, 404)
        return
    title = d.get("title") or meta.get("title") or "测试用例"
    module = d.get("module") or meta.get("module") or "AI测试"
    text_assets, image_assets = load_asset_contents(case_set_id, meta)
    if not text_assets and not image_assets:
        handler._json({"ok": False, "error": "没有可用于生成的文本或图片资产"}, 400)
        return
    try:
        payload = call_dashscope_cases(title, module, text_assets, image_assets)
        payload["id"] = case_set_id
        payload["module"] = module
        write_json_file(cases_path(case_set_id), payload)
    except Exception as e:
        handler._json({"ok": False, "error": f"生成用例失败：{e}"}, 500)
        return
    handler._json({"ok": True, "case_set_id": case_set_id, "cases": payload})


# ── UI YAML 生成 ────────────────────────────────────────────────────

@route_post("/api/ui/generate-yaml")
def _post_ui_generate_yaml(handler, qs):
    d = handler._body()
    try:
        result = generate_ui_yaml_from_request(d)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)
        return
    handler._json(result)


@route_post("/api/ui/generate-yaml-async")
def _post_ui_generate_yaml_async(handler, qs):
    d = handler._body()
    create_job = safe_bool(d.get("createJob") or d.get("create_job"))
    device_id = d.get("device_id") or d.get("deviceId") or ""
    runner_id = d.get("runner_id") or d.get("runnerId") or ""
    device_strategy = normalize_device_strategy(
        d.get("device_strategy") or d.get("deviceStrategy"),
        device_id=device_id,
        runner_id=runner_id,
    )
    if create_job and device_strategy != "auto" and not device_id and not runner_id:
        handler._json({
            "ok": False,
            "error": "生成后创建执行任务需要先选择执行设备；如确实需要平台分配，请明确选择“自动选择在线设备”。"
        }, 400)
        return
    job_id = generate_job_id()
    job = {
        "ok": True, "job_id": job_id, "type": "generate",
        "status": "pending", "progress": 0, "step": "排队中",
        "message": "生成任务已创建", "request_data": d,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_generate_job(job)
    worker = threading.Thread(target=run_generate_job, args=(job_id, d), daemon=True)
    worker.start()
    handler._json({"ok": True, "job_id": job_id, "job": sanitize_generate_job_for_client(job)})


# ── 脑图生成 ────────────────────────────────────────────────────────

@route_post("/api/cases/mindmap")
def _post_cases_mindmap(handler, qs):
    try:
        d = handler._body()
    except Exception:
        d = {}
    case_set_id = qs.get("case_set_id") or qs.get("id") or d.get("case_set_id") or d.get("id")
    if not case_set_id:
        handler._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
        return
    summary = read_json_file(generation_summary_path(case_set_id), default=None)
    if not summary:
        handler._json({"ok": False, "error": "生成汇总不存在"}, 404)
        return
    try:
        clear_generation_mindmap_deleted(case_set_id)
        mindmap_mode = str(d.get("mindmap_mode") or d.get("mindmapMode") or "full").strip().lower() or "full"
        writable_summary = dict(summary)
        writable_summary["mindmap_mode"] = mindmap_mode
        review = writable_summary.get("review") if isinstance(writable_summary.get("review"), dict) else {}
        review = dict(review)
        review["mindmap_mode"] = mindmap_mode
        writable_summary["review"] = review
        mm_path = write_generation_mindmap(case_set_id, writable_summary)
        stat = os.stat(mm_path)
        updated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
        writable_summary["mindmap_updated_at"] = updated_at
        writable_summary["mindmap_size"] = stat.st_size
        review["mindmap_refreshed_at"] = updated_at
        writable_summary["review"] = review
        write_json_file(generation_summary_path(case_set_id), writable_summary)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({
        "ok": True, "case_set_id": case_set_id, "mindmap": mm_path,
        "mindmap_exists": True, "mindmap_deleted": False,
        "mindmap_size": stat.st_size,
        "mindmap_updated_at": updated_at,
        "message": "已按现有生成分析刷新完整脑图文件；不会重新调用 AI，不会改 YAML 或用例"
    })


@route_post("/api/cases/mindmap-only-async")
def _post_cases_mindmap_only_async(handler, qs):
    d = handler._body()
    job_id = generate_job_id()
    job = {
        "ok": True, "job_id": job_id, "type": "mindmap_only",
        "status": "pending", "progress": 0, "step": "排队中",
        "message": "只生成脑图任务已创建", "request_data": d,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_generate_job(job)
    worker = threading.Thread(target=run_mindmap_only_job, args=(job_id, d), daemon=True)
    worker.start()
    handler._json({"ok": True, "job_id": job_id, "job": sanitize_generate_job_for_client(job)})


# ── UI 设计稿上传 ──────────────────────────────────────────────────

@route_post("/api/cases/ui-designs")
def _post_cases_ui_designs(handler, qs):
    try:
        d = handler._body()
    except Exception:
        d = {}
    case_set_id = d.get("case_set_id") or d.get("caseSetId") or qs.get("case_set_id") or qs.get("id")
    if not case_set_id:
        handler._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
        return
    files = d.get("files") or []
    try:
        summary = read_json_file(generation_summary_path(case_set_id), default={}) or {}
        saved, meta = save_case_ui_design_files(
            case_set_id, files,
            source=d.get("source") or "manual",
            title=d.get("title") or summary.get("title") or "",
            module=d.get("module") or summary.get("module") or "",
            extra={
                "description": d.get("description") or "人工补充的 UI 设计稿/截图",
                "route": d.get("route") or "",
                "page_name": d.get("page_name") or d.get("pageName") or ""
            }
        )
        if summary:
            summary["ui_design_assets"] = meta.get("designs") or []
            write_generation_summary(case_set_id, summary)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)
        return
    handler._json({"ok": True, "case_set_id": case_set_id, "saved": saved, "ui_designs": meta})


# ── UI 设计稿排除恢复 ──────────────────────────────────────────────

@route_post("/api/cases/ui-design-exclusion")
def _post_cases_ui_design_exclusion(handler, qs):
    try:
        d = handler._body()
    except Exception:
        d = {}
    case_set_id = d.get("case_set_id") or d.get("caseSetId") or qs.get("case_set_id") or qs.get("id")
    node_id = d.get("node_id") or d.get("nodeId") or qs.get("node_id") or qs.get("nodeId")
    if not case_set_id or not node_id:
        handler._json({"ok": False, "error": "case_set_id 和 node_id 不能为空"}, 400)
        return
    try:
        restored, meta = restore_excluded_figma_node(case_set_id, node_id=node_id)
        summary = read_json_file(generation_summary_path(case_set_id), default=None)
        if summary:
            ui_design_meta = filtered_case_ui_design_assets_for_summary(case_set_id, summary)
            summary["ui_design_assets"] = ui_design_meta.get("designs") or []
            summary["hidden_ui_design_assets"] = ui_design_meta.get("hidden_designs") or []
            summary["excluded_figma_nodes"] = ui_design_meta.get("excluded_figma_nodes") or []
            write_generation_summary(case_set_id, summary)
    except ValueError as e:
        handler._json({"ok": False, "error": str(e)}, 400)
        return
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)
        return
    handler._json({"ok": True, "restored": restored, "ui_designs": meta})


# ── 重新生成 YAML ──────────────────────────────────────────────────

@route_post("/api/ui/regenerate-yaml-async")
def _post_ui_regenerate_yaml_async(handler, qs):
    d = handler._body()
    case_set_id = d.get("case_set_id") or d.get("caseSetId") or d.get("id")
    if not case_set_id:
        handler._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
        return
    summary = read_json_file(generation_summary_path(case_set_id), default=None)
    meta = read_json_file(asset_meta_path(case_set_id), default=None)
    if not summary:
        handler._json({"ok": False, "error": "生成汇总不存在，无法重新生成"}, 404)
        return
    if not meta or not meta.get("files"):
        handler._json({"ok": False, "error": "这个生成批次没有可复用的需求资料，请重新上传需求后生成"}, 400)
        return
    supplement_files = d.get("files") or []
    supplement_text = (d.get("supplement") or d.get("supplement_text") or d.get("confirmation") or "").strip()
    if supplement_text:
        supplement_files = [{
            "name": f"manual-confirmation-{time.strftime('%Y%m%d-%H%M%S')}.txt",
            "content": supplement_text
        }] + list(supplement_files)
    if supplement_files:
        meta = append_asset_files(
            case_set_id,
            d.get("title") or summary.get("title") or meta.get("title") or "UI自动化用例",
            d.get("module") or summary.get("module") or meta.get("module") or "AI测试",
            supplement_files
        )
    figma_url = (
        (d.get("figma_url") or d.get("figmaUrl") or "").strip()
        or find_figma_url_for_case_set(case_set_id, summary=summary, meta=meta)
    )
    figma_mode = d.get("figma_mode") or d.get("figmaMode") or meta.get("figma_mode") or meta.get("figmaMode") or "smart"
    figma_limit = d.get("figma_limit") or d.get("figmaLimit") or meta.get("figma_limit") or meta.get("figmaLimit") or FIGMA_PARSE_LIMIT
    knowledge_page_ids = (
        d.get("knowledge_page_ids") or d.get("knowledgePageIds")
        or meta.get("knowledge_page_ids") or meta.get("knowledgePageIds") or []
    )
    knowledge_tier = d.get("knowledge_tier") or d.get("knowledgeTier") or meta.get("knowledge_tier") or meta.get("knowledgeTier") or "all"
    request_data = {
        "case_set_id": case_set_id,
        "title": d.get("title") or summary.get("title") or meta.get("title") or "UI自动化用例",
        "module": d.get("module") or summary.get("module") or meta.get("module") or "AI测试",
        "file": d.get("file") or summary.get("yaml_file") or f"task-{slug_for_file(summary.get('title') or meta.get('title') or 'UI自动化用例')}.yaml",
        "app_package": d.get("app_package") or d.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE),
        "knowledge_page_ids": knowledge_page_ids,
        "knowledge_tier": knowledge_tier,
        "figma_url": figma_url,
        "figma_mode": figma_mode,
        "figma_limit": figma_limit,
        "createJob": safe_bool(d.get("createJob") or d.get("create_job")),
        "autoOptimize": safe_bool(d.get("autoOptimize") or d.get("auto_optimize")),
        "run_mode": d.get("run_mode") or d.get("runMode") or "test",
        "reuse_assets": True,
        "regenerate": True
    }
    update_asset_request_context(case_set_id, request_data)
    job_id = generate_job_id()
    job = {
        "ok": True, "job_id": job_id, "type": "generate",
        "status": "pending", "progress": 0, "step": "排队中",
        "message": "重新生成用例任务已创建，将按最新策略覆盖生成 YAML 和脑图文件",
        "case_set_id": case_set_id, "request_data": request_data,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_generate_job(job)
    worker = threading.Thread(target=run_generate_job, args=(job_id, request_data), daemon=True)
    worker.start()
    handler._json({"ok": True, "job_id": job_id, "job": sanitize_generate_job_for_client(job)})


# ── 生成任务重试/取消（前缀匹配）────────────────────────────────────

@route_post_prefix("/api/ui/generate-jobs/")
def _post_generate_jobs_action(handler, qs, path):
    prefix = "/api/ui/generate-jobs/"
    tail = path[len(prefix):] if path.startswith(prefix) else ""
    parts = [part for part in tail.split("/") if part]
    job_id = parts[0] if parts else ""
    action = parts[1] if len(parts) > 1 else ""
    if job_id and action == "retry":
        _handle_generate_job_retry(handler, None, job_id)
        return
    if job_id and action == "cancel":
        d = handler._body()
        _handle_generate_job_cancel(handler, None, job_id, d)
        return
    handler._json({"ok": False, "error": "未知生成任务操作", "path": path}, 404)


@route_delete_prefix("/api/ui/generate-jobs/")
def _delete_generate_jobs_action(handler, qs, path):
    prefix = "/api/ui/generate-jobs/"
    tail = path[len(prefix):] if path.startswith(prefix) else ""
    parts = [part for part in tail.split("/") if part]
    job_id = parts[0] if parts else ""
    if not job_id:
        handler._json({"ok": False, "error": "job_id 不能为空"}, 400)
        return
    result = delete_generate_job(job_id)
    if not result.get("ok"):
        handler._json(result, 400 if result.get("error") else 404)
        return
    handler._json(result)


def _handle_generate_job_retry(handler, m, job_id):
    old_job = load_generate_job(job_id)
    if not old_job:
        handler._json({"ok": False, "error": "生成任务不存在"}, 404)
        return
    old_type = old_job.get("type")
    if old_type not in ("generate", "mindmap_only"):
        handler._json({"ok": False, "error": "只有 AI 生成任务或脑图生成任务支持重试"}, 400)
        return
    request_data = generate_retry_request_from_job(old_job)
    if not request_data:
        handler._json({"ok": False, "error": "这个生成任务没有可复用的原始请求，请回到生成分析或重新上传需求后生成"}, 400)
        return
    next_job_id = generate_job_id()
    next_job = {
        "ok": True, "job_id": next_job_id, "type": old_type,
        "status": "pending", "progress": 0, "step": "排队中",
        "message": f"已从失败任务 {job_id} 创建重试",
        "case_set_id": request_data.get("case_set_id") or old_job.get("case_set_id") or "",
        "retry_from_job_id": job_id, "request_data": request_data,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_generate_job(next_job)
    worker_target = run_mindmap_only_job if old_type == "mindmap_only" else run_generate_job
    worker = threading.Thread(target=worker_target, args=(next_job_id, request_data), daemon=True)
    worker.start()
    handler._json({"ok": True, "job_id": next_job_id, "job": sanitize_generate_job_for_client(next_job)})


def _handle_generate_job_cancel(handler, m, job_id, d):
    job = load_generate_job(job_id)
    if not job:
        handler._json({"ok": False, "error": "生成任务不存在"}, 404)
        return
    if job.get("status") not in ("pending", "running"):
        handler._json({"ok": False, "error": "只有排队中或执行中的生成任务可以取消"}, 400)
        return
    job = update_generate_job(
        job_id,
        status="cancelled",
        progress=safe_int(job.get("progress"), 0),
        step="已取消",
        message="用户已取消后台生成任务",
        cancel_reason=d.get("reason") or "manual",
        finished_at=time.strftime("%Y-%m-%d %H:%M:%S")
    )
    handler._json({"ok": True, "job": sanitize_generate_job_for_client(job)})


# ── Runner 心跳 ─────────────────────────────────────────────────────

@route_post("/api/runner/heartbeat")
def _post_runner_heartbeat(handler, qs):
    if _require_user_auth(handler):
        return
    d = handler._body()
    record = register_runner(d)
    handler._json({
        "ok": True,
        "runner_id": record.get("runner_id"),
        "devices": record.get("devices") or [],
        "capabilities": record.get("capabilities") or {},
        "runner_version": record.get("runner_version") or record.get("version") or "",
        "started_at": record.get("started_at") or "",
        "last_seen": record.get("last_seen") or "",
    })


# ── 生成批次冒烟重跑 ───────────────────────────────────────────────

@route_post("/api/cases/rerun-smoke")
def _post_cases_rerun_smoke(handler, qs):
    d = handler._body()
    case_set_id = d.get("case_set_id") or d.get("caseSetId") or d.get("id")
    if not case_set_id:
        handler._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
        return
    summary = read_json_file(generation_summary_path(case_set_id), default=None)
    if not summary:
        handler._json({"ok": False, "error": "生成汇总不存在，无法重跑冒烟用例"}, 404)
        return
    module = d.get("module") or d.get("mod") or summary.get("module") or ""
    if not module:
        handler._json({"ok": False, "error": "生成汇总缺少模块信息，无法创建 Runner 任务"}, 400)
        return

    runner_id = d.get("runner_id") or d.get("runnerId") or ""
    device_id = d.get("device_id") or d.get("deviceId") or ""
    device_strategy = normalize_device_strategy(
        d.get("device_strategy") or d.get("deviceStrategy") or "auto",
        device_id=device_id,
        runner_id=runner_id,
    )
    run_mode = d.get("run_mode") or d.get("runMode") or "test"
    run_all = safe_bool(d.get("run_all") or d.get("runAll") or d.get("all"))
    rerun_scope = str(d.get("scope") or d.get("rerunScope") or d.get("run_scope") or "smoke").strip().lower()
    raw_limit = d.get("limit") if d.get("limit") is not None else d.get("max")
    default_limit = generation_smoke_rerun_default_limit(summary)
    limit = 0 if run_all else safe_int(raw_limit, default_limit)
    if not run_all and limit <= 0:
        limit = default_limit
    if rerun_scope in ("remaining", "remaining_executable", "non_smoke", "rest"):
        all_refs = generation_current_executable_yaml_refs(summary, module, include_smoke=False)
        scope_label = "剩余可执行 YAML"
    elif rerun_scope in ("executable", "all_executable"):
        all_refs = generation_current_executable_yaml_refs(summary, module, include_smoke=True)
        scope_label = "全部可执行 YAML"
    else:
        rerun_scope = "smoke"
        all_refs = generation_current_executable_yaml_refs(summary, module, require_smoke=True)
        if not all_refs and not _summary_has_generated_buckets(summary):
            all_refs = generation_smoke_yaml_refs(summary)
        scope_label = "冒烟 YAML"
    refs = all_refs if limit <= 0 else all_refs[:limit]
    if not refs:
        handler._json({
            "ok": False,
            "error": f"这个生成批次没有可直接重跑的{scope_label}；请先在评审页打开并修正 YAML，保存后再重新执行。"
        }, 400)
        return

    created = []
    skipped = []
    for ref in refs:
        file_name = ref.get("file") or ""
        target_task_name = ref.get("target_task_name") or ""
        try:
            yaml_path = safe_join(TASK_DIR, module, file_name)
        except ValueError:
            skipped.append({**ref, "reason": "非法 YAML 路径"})
            continue
        if not os.path.exists(yaml_path):
            skipped.append({**ref, "reason": "YAML 文件不存在"})
            continue
        if target_task_name:
            try:
                yaml_content = read_text_file(yaml_path)
                app_package = resolve_app_package(module, file_name, yaml_content)
                yaml_with_single_task(yaml_content, target_task_name, app_package=app_package)
            except Exception as e:
                skipped.append({**ref, "reason": str(e)})
                continue
        job = create_pending_job(
            module,
            file_name,
            auto_optimize=False,
            device_id=device_id,
            runner_id=runner_id,
            device_strategy=device_strategy,
            run_mode=run_mode,
            target_task_name=target_task_name,
        )
        created.append({**ref, "job_id": job.get("job_id"), "job": job})

    if not created:
        handler._json({
            "ok": False,
            "error": f"没有成功创建{scope_label}重跑任务",
            "case_set_id": case_set_id,
            "selectedCount": len(refs),
            "totalSmokeCount": len(all_refs),
            "totalSelectableCount": len(all_refs),
            "limit": limit,
            "runAll": run_all,
            "scope": rerun_scope,
            "skipped": skipped,
        }, 400)
        return

    handler._json({
        "ok": True,
        "case_set_id": case_set_id,
        "module": module,
        "device_strategy": device_strategy,
        "runner_id": runner_id,
        "device_id": device_id,
        "selectedCount": len(refs),
        "totalSmokeCount": len(all_refs),
        "totalSelectableCount": len(all_refs),
        "limit": limit,
        "runAll": run_all,
        "scope": rerun_scope,
        "createdCount": len(created),
        "skippedCount": len(skipped),
        "created": created,
        "skipped": skipped,
    })


# ── Cases 前缀匹配（POST）───────────────────────────────────────────

@route_post_prefix("/api/cases/")
def _post_cases_by_id(handler, qs, path):
    case_set_id = path.split("/")[-1]
    d = handler._body()
    payload = d.get("cases") or d.get("content") or d
    try:
        normalized = normalize_cases_payload(payload)
        normalized["id"] = case_set_id
        normalized["module"] = d.get("module") or normalized.get("module") or "AI测试"
        write_json_file(cases_path(case_set_id), normalized)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)
        return
    handler._json({"ok": True, "case_set_id": case_set_id, "cases": normalized})


# ── 基线页面引用（POST）─────────────────────────────────────────────

@route_post("/api/baseline/page-refs")
def _post_baseline_page_refs(handler, qs):
    d = handler._body()
    mod = d.get("module", "")
    file = d.get("file", "")
    app_package = d.get("app_package") or d.get("appPackage") or app_package_for_module(mod) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    task_name = d.get("taskName") or d.get("task_name") or ""
    page_ids = d.get("page_ids") or d.get("pageIds") or []
    if not mod or not file:
        handler._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
        return
    row = set_baseline_ref_page_ids(app_package, mod, file, task_name, page_ids)
    handler._json({"ok": True, "ref": row})


# ── 运行请求 ────────────────────────────────────────────────────────

@route_post("/api/run-request")
def _post_run_request(handler, qs):
    d = handler._body()
    mod = d.get("module", "")
    file = d.get("file", "")
    auto_optimize = automatic_baseline_repair_enabled(d.get("autoOptimize", d.get("auto_optimize")))
    run_mode = d.get("run_mode") or d.get("runMode") or ("baseline" if auto_optimize else "test")
    device_id = d.get("device_id") or d.get("deviceId") or ""
    runner_id = d.get("runner_id") or d.get("runnerId") or ""
    device_strategy = normalize_device_strategy(
        d.get("device_strategy") or d.get("deviceStrategy"),
        device_id=device_id,
        runner_id=runner_id,
    )
    target_task_name = d.get("target_task_name") or d.get("targetTaskName") or ""
    if not mod or not file:
        handler._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
        return
    if device_strategy != "auto" and not device_id and not runner_id:
        handler._json({
            "ok": False,
            "error": "请选择执行设备；如确实需要平台分配，请明确选择“自动选择在线设备”。"
        }, 400)
        return
    try:
        yaml_path = safe_join(TASK_DIR, mod, file)
        if not os.path.exists(yaml_path):
            handler._json({"ok": False, "error": "YAML 文件不存在"}, 404)
            return
        if target_task_name:
            with open(yaml_path, encoding="utf-8") as f:
                yaml_content = f.read()
            app_package = resolve_app_package(mod, file, yaml_content)
            yaml_with_single_task(yaml_content, target_task_name, app_package=app_package)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)
        return
    job = create_pending_job(
        mod,
        file,
        auto_optimize=auto_optimize,
        device_id=device_id,
        runner_id=runner_id,
        device_strategy=device_strategy,
        run_mode=run_mode,
        target_task_name=target_task_name,
    )
    handler._json({"ok": True, "job": job})


# ── Sonic 报告就绪 ──────────────────────────────────────────────────

@route_post("/api/sonic/report-ready")
def _post_sonic_report_ready(handler, qs):
    if _require_user_auth(handler):
        return
    d = handler._body()
    try:
        job = attach_sonic_background_report(
            d.get("job_id") or d.get("jobId") or "",
            d.get("report_url") or d.get("reportUrl") or "",
            d.get("local_report_path") or d.get("localReportPath") or "",
            d.get("report_upload_error") or d.get("reportUploadError") or ""
        )
    except ValueError as e:
        handler._json({"ok": False, "error": str(e)}, 404)
        return
    handler._json({"ok": True, "job_id": job.get("job_id"), "report_url": job.get("report_url", "")})


# ── Sonic 结果上报 ──────────────────────────────────────────────────

@route_post("/api/sonic/result")
def _post_sonic_result(handler, qs):
    if _require_user_auth(handler):
        return
    d = handler._body()
    mod = d.get("module") or d.get("taskModule") or ""
    file = clean_filename(d.get("file") or d.get("taskName") or "")
    job_id = d.get("job_id") or d.get("jobId") or new_job_id()
    status = normalize_job_status(d.get("status") or ("success" if safe_int(d.get("exitCode"), 1) == 0 else "failed"))
    target_task_name = d.get("target_task_name") or d.get("targetTaskName") or ""
    stdout = sonic_notify_clean_text(d.get("stdout") or d.get("output") or "", fallback="")
    stderr = sonic_notify_clean_text(d.get("stderr") or d.get("error") or "", fallback="日志编码异常，请查看报告")
    report_url = d.get("report_url") or d.get("reportUrl") or ""
    sonic_report_url = d.get("sonic_report_url") or d.get("sonicReportUrl") or d.get("sonic_url") or d.get("sonicUrl") or ""
    local_report_path = sonic_notify_clean_text(d.get("local_report_path") or d.get("localReportPath") or "", fallback="")
    report_upload_error = sonic_notify_clean_text(d.get("report_upload_error") or d.get("reportUploadError") or "", fallback="报告上传错误信息编码异常")
    report_upload_pending = safe_bool(d.get("report_upload_pending") or d.get("reportUploadPending"))
    report_missing_reason = sonic_notify_clean_text(d.get("report_missing_reason") or d.get("reportMissingReason") or "", fallback="")
    upload_warning = sonic_notify_clean_text(d.get("upload_warning") or d.get("uploadWarning") or "", fallback="")
    app_package = (d.get("app_package") or d.get("appPackage") or "").strip()
    app_name = (d.get("app_name") or d.get("appName") or "").strip()
    suite_run_id = (d.get("suite_run_id") or d.get("suiteRunId") or "").strip()
    sonic_suite_id = (d.get("sonic_suite_id") or d.get("sonicSuiteId") or d.get("suite_id") or d.get("suiteId") or "").strip()
    sonic_suite_name = (d.get("sonic_suite_name") or d.get("sonicSuiteName") or d.get("suite_name") or d.get("suiteName") or "").strip()
    suite_started_at = sonic_notify_clean_text(d.get("suite_started_at") or d.get("suiteStartedAt") or d.get("suite_start_time") or d.get("suiteStartTime") or "", fallback="")
    suite_expected_total = safe_int(d.get("suite_expected_total") or d.get("suiteExpectedTotal") or d.get("suite_total") or d.get("suiteTotal"), 0)
    screenshots = d.get("screenshots") if isinstance(d.get("screenshots"), list) else []
    if not mod or not file:
        handler._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
        return
    if not app_package:
        try:
            yaml_text_for_app = read_text_file(safe_join(TASK_DIR, mod, file), "")
            app_package = resolve_app_package(mod, file, yaml_text_for_app, allow_default=False)
        except Exception:
            app_package = ""
    app_info = sonic_suite_app_info(app_package, mod)
    if not app_name:
        app_name = app_info.get("name") or app_package
    if not sonic_suite_id:
        sonic_suite_id = app_info.get("sonic_suite_id") or app_info.get("sonicSuiteId") or ""
    if not sonic_suite_name:
        sonic_suite_name = app_info.get("sonic_suite_name") or app_info.get("sonicSuiteName") or ""
    run_dir = safe_join(LEARNING_DIR, "runs", job_id)
    os.makedirs(run_dir, exist_ok=True)
    write_text_file(safe_join(run_dir, "stdout.log"), stdout)
    write_text_file(safe_join(run_dir, "stderr.log"), stderr)
    saved_screenshots = []
    if screenshots:
        screenshot_dir = safe_join(run_dir, "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)
        for idx, shot in enumerate(screenshots[:4], start=1):
            if not isinstance(shot, dict) or not shot.get("contentBase64"):
                continue
            raw_name = clean_asset_filename(shot.get("name") or f"midscene-{idx}.png")
            try:
                data = base64.b64decode(shot["contentBase64"])
            except Exception:
                continue
            if not data or len(data) > 2 * 1024 * 1024:
                continue
            name = f"{idx:02d}-{raw_name}"
            write_bytes_file(safe_join(screenshot_dir, name), data)
            saved_screenshots.append({
                "name": name,
                "mime": shot.get("mime") or guess_mime(name),
                "local_path": shot.get("local_path") or shot.get("localPath") or ""
            })
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    job = {
        "job_id": job_id, "case_id": d.get("case_id") or d.get("caseId") or "",
        "module": mod, "file": file, "target_task_name": target_task_name,
        "status": status, "run_mode": d.get("run_mode") or d.get("runMode") or "test",
        "auto_optimize": automatic_baseline_repair_enabled(d.get("autoOptimize", d.get("auto_optimize"))),
        "attempt": safe_int(d.get("attempt"), 1),
        "max_attempt": safe_int(d.get("max_attempt") or d.get("maxAttempt"), 2),
        "parent_job_id": d.get("parent_job_id") or d.get("parentJobId") or "",
        "runner_id": d.get("runner_id") or d.get("runnerId") or "sonic",
        "device_id": d.get("device_id") or d.get("deviceId") or "",
        "created_at": d.get("created_at") or now, "started_at": d.get("started_at") or now,
        "report_url": report_url, "sonic_report_url": sonic_report_url,
        "local_report_path": local_report_path,
        "report_upload_error": report_upload_error,
        "report_upload_pending": report_upload_pending,
        "report_missing_reason": report_missing_reason,
        "upload_warning": upload_warning,
        "app_package": app_package, "app_name": app_name,
        "suite_run_id": suite_run_id, "sonic_suite_id": sonic_suite_id,
        "sonic_suite_name": sonic_suite_name,
        "suite_started_at": suite_started_at,
        "suite_expected_total": suite_expected_total,
        "execution_screenshots": saved_screenshots, "run_dir": run_dir,
        "stdout_tail": stdout[-2000:], "stderr_tail": stderr[-2000:],
        "error": stderr,
        "progress": safe_int(d.get("progress"), 100 if status == "success" else 0),
        "current_task_name": d.get("current_task_name") or d.get("currentTaskName") or d.get("caseName") or target_task_name,
        "current_task_index": safe_int(d.get("current_task_index") or d.get("currentTaskIndex"), 0),
        "completed_task_count": safe_int(d.get("completed_task_count") or d.get("completedTaskCount"), 0),
        "total_task_count": safe_int(d.get("total_task_count") or d.get("totalTaskCount"), 0),
        "progress_message": sonic_notify_clean_text(d.get("message") or "", fallback=""),
        "source": "sonic"
    }
    if status not in ("pending", "running"):
        job["finished_at"] = now
    with JOB_LOCK:
        jobs = load_jobs()
        replaced = False
        for idx, item in enumerate(jobs):
            if item.get("job_id") == job_id:
                jobs[idx].update(job)
                replaced = True
                break
        if not replaced:
            jobs.append(job)
        save_jobs(jobs)
    update_task_meta(mod, file, {
        "last_job_id": job_id, "last_status": status,
        "last_target_task_name": target_task_name,
        "last_run_at": now,
        "last_report_url": report_url,
        "last_sonic_report_url": sonic_report_url
    })
    if status in ("pending", "running"):
        suite_key = touch_sonic_suite_activity(job)
        if suite_key:
            job["sonic_suite_key"] = suite_key
            with JOB_LOCK:
                target, jobs = find_job(job_id)
                if target:
                    target["sonic_suite_key"] = suite_key
                    save_jobs(jobs)
        handler._json({"ok": True, "job": job, "failure_review": None, "optimize": None})
        return
    suite_key = register_sonic_suite_result(job)
    if suite_key:
        job["sonic_suite_key"] = suite_key
        with JOB_LOCK:
            target, jobs = find_job(job_id)
            if target:
                target["sonic_suite_key"] = suite_key
                save_jobs(jobs)
    post_processing = start_sonic_result_post_actions(job, stdout, stderr)
    handler._json({"ok": True, "job": job, "failure_review": None, "optimize": None, "post_processing": post_processing})


# ── Runner 进度/结果（前缀匹配）─────────────────────────────────────

@route_post_prefix("/api/runner/jobs/")
def _post_runner_jobs_action(handler, qs, path):
    prefix = "/api/runner/jobs/"
    tail = path[len(prefix):] if path.startswith(prefix) else ""
    parts = [part for part in tail.split("/") if part]
    job_id = parts[0] if parts else ""
    action = parts[1] if len(parts) > 1 else ""
    if job_id and action == "progress":
        _handle_runner_job_progress(handler, job_id)
        return
    if job_id and action == "report-ready":
        _handle_runner_job_report_ready(handler, job_id)
        return
    if job_id and action == "result":
        _handle_runner_job_result(handler, job_id)
        return
    handler._json({"ok": False, "error": "未知 Runner 任务操作", "path": path}, 404)


def _handle_runner_job_progress(handler, job_id):
    if _require_user_auth(handler):
        return
    d = handler._body()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with JOB_LOCK:
        target, jobs = find_job(job_id)
        if not target:
            handler._json({"ok": False, "error": "任务不存在"}, 404)
            return
        if target.get("status") in ("pending", "running"):
            target["status"] = "running"
        if not target.get("started_at"):
            target["started_at"] = now
        progress = safe_int(d.get("progress"), safe_int(target.get("progress"), 0))
        progress = max(0, min(99, progress))
        target["progress"] = progress
        target["current_task_name"] = d.get("current_task_name") or d.get("currentTaskName") or target.get("current_task_name", "")
        target["current_task_index"] = safe_int(d.get("current_task_index") or d.get("currentTaskIndex"), safe_int(target.get("current_task_index"), 0))
        target["completed_task_count"] = safe_int(d.get("completed_task_count") or d.get("completedTaskCount"), safe_int(target.get("completed_task_count"), 0))
        target["total_task_count"] = safe_int(d.get("total_task_count") or d.get("totalTaskCount"), safe_int(target.get("total_task_count"), 0))
        target["progress_message"] = d.get("message") or target.get("progress_message", "")
        target["stdout_tail"] = (d.get("stdout_tail") or d.get("stdoutTail") or target.get("stdout_tail", ""))[-2000:]
        event = {
            "ts": now,
            "type": "progress",
            "title": "进度回传",
            "message": target.get("progress_message", ""),
            "progress": progress,
            "current_task_name": target.get("current_task_name", ""),
            "current_task_index": target.get("current_task_index", 0),
            "completed_task_count": target.get("completed_task_count", 0),
            "total_task_count": target.get("total_task_count", 0),
        }
        events = target.setdefault("events", [])
        if isinstance(events, list):
            if not events or events[-1].get("progress") != progress or events[-1].get("current_task_name") != event["current_task_name"]:
                events.append(event)
                target["events"] = events[-80:]
        target["updated_at"] = now
        save_jobs(jobs)
    handler._json({"ok": True, "job": target})


def _handle_runner_job_report_ready(handler, job_id):
    if _require_user_auth(handler):
        return
    d = handler._body()
    try:
        job = attach_sonic_background_report(
            job_id,
            d.get("report_url") or d.get("reportUrl") or "",
            d.get("local_report_path") or d.get("localReportPath") or "",
            d.get("report_upload_error") or d.get("reportUploadError") or ""
        )
    except ValueError as e:
        handler._json({"ok": False, "error": str(e)}, 404)
        return
    append_job_event(job_id, {
        "type": "report_ready",
        "title": "报告后台上传",
        "message": job.get("report_url") or job.get("report_upload_error") or "报告状态已更新",
        "report_url": job.get("report_url", ""),
    })
    handler._json({"ok": True, "job_id": job_id, "report_url": job.get("report_url", "")})


def _handle_runner_job_result(handler, job_id):
    if _require_user_auth(handler):
        return
    d = handler._body()
    status = normalize_job_status(d.get("status", "failed"))
    run_dir = safe_join(LEARNING_DIR, "runs", job_id)
    os.makedirs(run_dir, exist_ok=True)
    stdout = d.get("stdout", "")
    stderr = d.get("stderr", "")
    summary = d.get("summary")
    report_html = d.get("report_html", "")
    incoming_report_url = d.get("report_url") or d.get("reportUrl") or ""
    local_report_path = d.get("local_report_path") or d.get("localReportPath") or ""
    report_upload_error = d.get("report_upload_error") or d.get("reportUploadError") or ""
    report_upload_pending = safe_bool(d.get("report_upload_pending") or d.get("reportUploadPending"))
    report_missing_reason = d.get("report_missing_reason") or d.get("reportMissingReason") or ""
    upload_warning = d.get("upload_warning") or d.get("uploadWarning") or ""
    attempts = d.get("attempts") if isinstance(d.get("attempts"), list) else []
    screenshots = d.get("screenshots") if isinstance(d.get("screenshots"), list) else []
    write_text_file(safe_join(run_dir, "stdout.log"), stdout)
    write_text_file(safe_join(run_dir, "stderr.log"), stderr)
    if summary is not None:
        write_json_file(safe_join(run_dir, "summary.json"), summary)
    if attempts:
        write_json_file(safe_join(run_dir, "attempts.json"), attempts)
    saved_screenshots = []
    if screenshots:
        screenshot_dir = safe_join(run_dir, "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)
        for idx, shot in enumerate(screenshots[:4], start=1):
            if not isinstance(shot, dict) or not shot.get("contentBase64"):
                continue
            raw_name = clean_asset_filename(shot.get("name") or f"midscene-{idx}.png")
            try:
                data = base64.b64decode(shot["contentBase64"])
            except Exception:
                continue
            if not data or len(data) > 2 * 1024 * 1024:
                continue
            name = f"{idx:02d}-{raw_name}"
            write_bytes_file(safe_join(screenshot_dir, name), data)
            saved_screenshots.append({
                "name": name,
                "mime": shot.get("mime") or guess_mime(name),
                "local_path": shot.get("local_path") or shot.get("localPath") or ""
            })
    report_url = incoming_report_url
    if report_html:
        report_name = f"{job_id}.html"
        write_text_file(safe_join(REPORT_DIR, report_name), report_html)
        report_url = public_report_url(report_name)
    with JOB_LOCK:
        jobs = load_jobs()
        found = None
        for job in jobs:
            if job.get("job_id") == job_id:
                found = job
                break
        if found:
            found["status"] = status
            found["progress"] = 100 if status == "success" else max(safe_int(found.get("progress"), 0), 0)
            if status == "success":
                found["completed_task_count"] = found.get("total_task_count") or found.get("completed_task_count") or 0
            found["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            found["report_url"] = report_url
            found["local_report_path"] = local_report_path
            found["report_upload_error"] = report_upload_error
            found["report_upload_pending"] = report_upload_pending
            found["report_missing_reason"] = report_missing_reason
            found["upload_warning"] = upload_warning
            found["attempts"] = attempts[-5:] if attempts else found.get("attempts", [])
            found["execution_screenshots"] = saved_screenshots
            found["run_dir"] = run_dir
            found["device_id"] = d.get("device_id") or d.get("deviceId") or found.get("device_id", "")
            found["stdout_tail"] = stdout[-2000:]
            found["stderr_tail"] = stderr[-2000:]
            event_message = "执行成功" if status == "success" else (report_missing_reason or upload_warning or stderr[-300:] or "执行失败")
            events = found.setdefault("events", [])
            if isinstance(events, list):
                events.append({
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "type": "result",
                    "title": "执行结果",
                    "status": status,
                    "message": event_message,
                    "report_url": report_url,
                })
                found["events"] = events[-80:]
            save_jobs(jobs)
            found_is_yaml_dry_run = str(found.get("job_type") or found.get("type") or "").strip().lower() == "yaml_dry_run"
            if not is_app_install_job(found) and not found_is_yaml_dry_run and found.get("module") and found.get("file"):
                update_task_meta(found["module"], found["file"], {
                    "last_job_id": job_id, "last_status": status,
                    "last_target_task_name": found.get("target_task_name", ""),
                    "last_run_at": found["finished_at"],
                    "last_report_url": report_url
                })
    failure_review = None
    found_is_yaml_dry_run = bool(found and str(found.get("job_type") or found.get("type") or "").strip().lower() == "yaml_dry_run")
    if found and status != "success" and not is_app_install_job(found) and not found_is_yaml_dry_run:
        try:
            failure_review = call_dashscope_failure_review(found, stdout, stderr, summary)
        except Exception as e:
            failure_review = {"category": "unknown", "confidence": 0, "reason": f"复检失败：{e}", "evidence": [], "suggested_action": "人工查看日志", "can_auto_repair": False}
        with JOB_LOCK:
            jobs = load_jobs()
            for job in jobs:
                if job.get("job_id") == job_id:
                    job["failure_review"] = failure_review
                    break
            save_jobs(jobs)
    optimize_result = None
    should_auto_repair = (
        ENABLE_AUTOMATIC_BASELINE_REPAIR
        and found and status != "success"
        and found.get("run_mode") == "baseline"
        and safe_bool(found.get("auto_optimize"))
        and failure_review
        and failure_review.get("category") == "script_issue"
        and safe_int(found.get("attempt"), 1) < safe_int(found.get("max_attempt"), 2)
    )
    if should_auto_repair:
        try:
            repaired, repair_dir = optimize_job_yaml_by_scope(found, stdout, stderr, summary)
            next_job = create_pending_job(
                found["module"], found["file"],
                auto_optimize=True,
                max_attempt=safe_int(found.get("max_attempt"), 2),
                attempt=safe_int(found.get("attempt"), 1) + 1,
                parent_job_id=job_id,
                device_id=found.get("device_id", ""),
                runner_id=found.get("target_runner_id") or found.get("runner_id", ""),
                device_strategy=found.get("device_strategy") or found.get("deviceStrategy") or "",
                run_mode=found.get("run_mode", "baseline"),
                target_task_name=found.get("target_task_name", "")
            )
            optimize_result = {"ok": True, "analysis": repaired.get("analysis", ""), "changes": repaired.get("changes", []), "repair_dir": repair_dir, "next_job": next_job}
            with JOB_LOCK:
                jobs = load_jobs()
                for job in jobs:
                    if job.get("job_id") == job_id:
                        job["optimize_result"] = optimize_result
                        break
                save_jobs(jobs)
        except Exception as e:
            optimize_result = {"ok": False, "error": str(e)}
            with JOB_LOCK:
                jobs = load_jobs()
                for job in jobs:
                    if job.get("job_id") == job_id:
                        job["optimize_result"] = optimize_result
                        break
                save_jobs(jobs)
    handler._json({"ok": True, "job_id": job_id, "status": status, "report_url": report_url, "failure_review": failure_review, "optimize": optimize_result})


# ── Job 操作（正则匹配）─────────────────────────────────────────────

def _job_safe_run_dir(job):
    job_id = job.get("job_id", "")
    runs_root = os.path.abspath(safe_join(LEARNING_DIR, "runs"))
    candidates = [job.get("run_dir") or ""]
    if job_id:
        candidates.append(safe_join(runs_root, job_id))
    for candidate in candidates:
        if not candidate:
            continue
        abs_candidate = os.path.abspath(candidate)
        try:
            if os.path.commonpath([runs_root, abs_candidate]) != runs_root:
                continue
        except ValueError:
            continue
        if os.path.isdir(abs_candidate):
            return abs_candidate
    return ""


def _read_job_failure_material(job):
    run_dir = _job_safe_run_dir(job)
    stdout_path = safe_join(run_dir, "stdout.log") if run_dir else ""
    stderr_path = safe_join(run_dir, "stderr.log") if run_dir else ""
    summary_path = safe_join(run_dir, "summary.json") if run_dir else ""
    stdout_exists = bool(stdout_path and os.path.exists(stdout_path))
    stderr_exists = bool(stderr_path and os.path.exists(stderr_path))
    summary_exists = bool(summary_path and os.path.exists(summary_path))
    stdout = read_text_file(stdout_path, "") if stdout_path else ""
    stderr = read_text_file(stderr_path, "") if stderr_path else ""
    summary = read_json_file(summary_path, None) if summary_path else None
    if not stdout:
        stdout = job.get("stdout_tail", "")
    if not stderr:
        stderr = job.get("stderr_tail", "")
    if summary is None and isinstance(job.get("summary"), dict):
        summary = job.get("summary")
    yaml_text = ""
    try:
        yaml_text = read_text_file(safe_join(TASK_DIR, job.get("module", ""), job.get("file", "")), "")
    except Exception:
        yaml_text = ""
    return {
        "run_dir": run_dir,
        "stdout": stdout,
        "stderr": stderr,
        "summary": summary,
        "yaml": yaml_text,
        "source": {
            "used_full_logs": stdout_exists or stderr_exists or summary_exists,
            "run_dir": run_dir,
            "stdout_chars": len(stdout or ""),
            "stderr_chars": len(stderr or ""),
            "summary_available": summary is not None,
            "log_files": {
                "stdout": stdout_exists,
                "stderr": stderr_exists,
                "summary": summary_exists,
            },
            "yaml_chars": len(yaml_text or ""),
            "report_url": job.get("report_url", ""),
        },
    }


@route_post_regex(r"^/api/jobs/([^/]+)/analyze-failure$")
def _post_job_analyze_failure(handler, qs, match):
    job_id = match.group(1)
    with JOB_LOCK:
        target, _ = find_job(job_id)
        target = dict(target) if target else None
    if not target:
        handler._json({"ok": False, "error": "任务不存在"}, 404)
        return
    material = _read_job_failure_material(target)
    if not (material["stdout"] or material["stderr"] or material["summary"] or target.get("failure_review")):
        handler._json({"ok": False, "error": "还没有收集到 Runner 执行日志，暂时无法做失败分析"}, 400)
        return
    try:
        review = call_dashscope_failure_review(target, material["stdout"], material["stderr"], material["summary"])
        review_error = ""
    except Exception as e:
        review_error = str(e)
        review = {
            "category": "unknown",
            "confidence": 0,
            "reason": f"AI 失败分析暂不可用：{review_error}",
            "evidence": [],
            "suggested_action": "请先查看执行报告、stdout/stderr 完整日志和设备状态",
            "can_auto_repair": False,
        }
    source = dict(material["source"])
    if review_error:
        source["ai_error"] = review_error
    with JOB_LOCK:
        target, jobs = find_job(job_id)
        if target:
            target["failure_review"] = review
            target["failure_review_source"] = source
            target["failure_reviewed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_jobs(jobs)
    handler._json({
        "ok": True,
        "job_id": job_id,
        "analysis": review,
        "failure_review": review,
        "source": source,
        "yaml": material["yaml"],
    })

@route_post_regex(r"^/api/jobs/([^/]+)/repair$")
def _post_job_repair(handler, qs, match):
    job_id = match.group(1)
    d = handler._body()
    with JOB_LOCK:
        jobs = load_jobs()
        target = None
        for job in jobs:
            if job.get("job_id") == job_id:
                target = job
                break
    if not target:
        handler._json({"ok": False, "error": "任务不存在"}, 404)
        return
    try:
        result = repair_job_and_create_next(target, create_next=True, force=safe_bool(d.get("forceRepair") or d.get("force_repair") or d.get("force")))
        with JOB_LOCK:
            jobs = load_jobs()
            for job in jobs:
                if job.get("job_id") == job_id:
                    job["manual_repair_result"] = result
                    break
            save_jobs(jobs)
        handler._json(result)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)


@route_post_regex(r"^/api/jobs/([^/]+)/cancel$")
def _post_job_cancel(handler, qs, match):
    job_id = match.group(1)
    d = handler._body()
    with JOB_LOCK:
        target, jobs = find_job(job_id)
        if not target:
            handler._json({"ok": False, "error": "任务不存在"}, 404)
            return
        if target.get("status") not in ("pending", "running"):
            handler._json({"ok": False, "error": "只有排队中或执行中的任务可以取消"}, 400)
            return
        target["status"] = "cancelled"
        target["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        target["cancel_reason"] = d.get("reason") or "manual"
        save_jobs(jobs)
    update_task_meta(target["module"], target["file"], {
        "last_job_id": job_id, "last_status": "cancelled",
        "last_target_task_name": target.get("target_task_name", ""),
        "last_run_at": target.get("finished_at")
    })
    handler._json({"ok": True, "job": target})


@route_post_regex(r"^/api/jobs/([^/]+)/retry$")
def _post_job_retry(handler, qs, match):
    job_id = match.group(1)
    d = handler._body()
    with JOB_LOCK:
        target, _ = find_job(job_id)
    if not target:
        handler._json({"ok": False, "error": "任务不存在"}, 404)
        return
    next_job = create_pending_job(
        target["module"], target["file"],
        auto_optimize=automatic_baseline_repair_enabled(target.get("auto_optimize")),
        max_attempt=safe_int(target.get("max_attempt"), 2),
        attempt=safe_int(target.get("attempt"), 1) + 1,
        parent_job_id=job_id,
        device_id=d.get("device_id") or d.get("deviceId") or target.get("device_id", ""),
        runner_id=d.get("runner_id") or d.get("runnerId") or target.get("target_runner_id") or target.get("runner_id", ""),
        device_strategy=d.get("device_strategy") or d.get("deviceStrategy") or target.get("device_strategy") or target.get("deviceStrategy") or "",
        run_mode=d.get("run_mode") or d.get("runMode") or target.get("run_mode", "test"),
        target_task_name=d.get("target_task_name") or d.get("targetTaskName") or target.get("target_task_name", "")
    )
    handler._json({"ok": True, "job": next_job})


@route_post_regex(r"^/api/jobs/([^/]+)/review$")
def _post_job_review(handler, qs, match):
    job_id = match.group(1)
    d = handler._body()
    category = d.get("category") or "unknown"
    allowed = ("product_bug", "script_issue", "env_issue", "data_issue", "model_issue", "unknown")
    if category not in allowed:
        handler._json({"ok": False, "error": "非法归因分类"}, 400)
        return
    with JOB_LOCK:
        target, jobs = find_job(job_id)
        if not target:
            handler._json({"ok": False, "error": "任务不存在"}, 404)
            return
        review = target.get("failure_review") or {}
        review.update({
            "category": category,
            "reason": d.get("reason") or review.get("reason", ""),
            "suggested_action": d.get("suggested_action") or d.get("suggestedAction") or review.get("suggested_action", ""),
            "manual_confirmed": True,
            "confirmed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "confirmed_by": d.get("user") or "manual",
            "can_auto_repair": category == "script_issue"
        })
        target["failure_review"] = review
        save_jobs(jobs)
    handler._json({"ok": True, "job": target, "failure_review": target["failure_review"]})


# ── 文件状态 ────────────────────────────────────────────────────────

@route_post("/api/file/status")
def _post_file_status(handler, qs):
    d = handler._body()
    mod = d.get("module", "")
    file = d.get("file", "")
    status = d.get("status", "draft")
    allowed = ("draft", "review", "active", "baseline", "maintenance", "blocked", "deprecated")
    if status not in allowed:
        handler._json({"ok": False, "error": "非法用例状态"}, 400)
        return
    if not mod or not file:
        handler._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
        return
    row = update_task_meta(mod, file, {
        "status": status,
        "status_note": d.get("note", ""),
        "status_updated_by": d.get("user") or "manual"
    })
    handler._json({"ok": True, "meta": row})


# ── 文件修复 ────────────────────────────────────────────────────────

@route_post("/api/file/repair-latest")
def _post_file_repair_latest(handler, qs):
    d = handler._body()
    mod = d.get("module", "")
    file = d.get("file", "")
    if not mod or not file:
        handler._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
        return
    try:
        result = repair_file_latest_result(d)
        handler._json(result)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)


@route_post("/api/file/repair-task-latest")
def _post_file_repair_task_latest(handler, qs):
    d = handler._body()
    mod = d.get("module", "")
    file = d.get("file", "")
    task_name = d.get("taskName", "")
    if not mod or not file or not task_name:
        handler._json({"ok": False, "error": "module、file 和 taskName 不能为空"}, 400)
        return
    try:
        result = repair_task_latest_result(d)
        handler._json(result)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)


# ── 模块创建 ────────────────────────────────────────────────────────

@route_post("/api/module")
def _post_module(handler, qs):
    d = handler._body()
    name = d.get("name", "")
    if not name:
        handler._json({"ok": False, "error": "模块名称不能为空"}, 400)
        return
    try:
        os.makedirs(safe_join(TASK_DIR, name), exist_ok=True)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({"ok": True})


# ── Task App 保存 ───────────────────────────────────────────────────

@route_post("/api/task-app")
def _post_task_app(handler, qs):
    d = handler._body()
    try:
        app = resolve_task_app_sonic_binding(normalize_task_app(d))
        data = load_task_apps()
        apps = [item for item in data.get("apps", []) if item.get("package") != app["package"]]
        apps.append(app)
        data["apps"] = sorted(apps, key=lambda item: item.get("name") or item.get("package") or "")
        save_task_apps(data)
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 400)
        return
    handler._json({"ok": True, "app": app})


# ── Sonic 诊断 ──────────────────────────────────────────────────────

@route_post("/api/sonic/diagnose")
def _post_sonic_diagnose(handler, qs):
    d = handler._body()
    try:
        projects_payload = sonic_list_projects()
        if isinstance(projects_payload, dict):
            projects = projects_payload.get("projects") or projects_payload.get("data") or []
        elif isinstance(projects_payload, list):
            projects = projects_payload
        else:
            projects = []
        projects = [item for item in projects if isinstance(item, dict)]
        apps = sonic_notify_known_apps()
        matched = []
        probe_project_id = safe_int(d.get("project_id") or d.get("projectId"), 0)
        for app in apps:
            project_id = sonic_project_id_for_app(app)
            project_name = sonic_project_name_for_app(app)
            found = None
            for project in projects:
                if project_id and safe_int(project.get("id"), 0) == project_id:
                    found = project
                    break
                name = project.get("projectName") or project.get("name") or ""
                if project_name and name == project_name:
                    found = project
                    break
            suite_binding = {
                "configured": bool(sonic_suite_id_for_app(app) or sonic_suite_name_for_app(app)),
                "matched": False, "id": sonic_suite_id_for_app(app),
                "name": sonic_suite_name_for_app(app), "case_count": 0, "error": "",
            }
            if found and suite_binding["configured"]:
                try:
                    bound = resolve_task_app_sonic_binding(app)
                    suite_binding.update({
                        "matched": bool(sonic_suite_id_for_app(bound)),
                        "id": sonic_suite_id_for_app(bound),
                        "name": sonic_suite_name_for_app(bound),
                        "case_count": safe_int(bound.get("sonic_suite_case_count"), 0),
                    })
                except Exception as e:
                    suite_binding["error"] = str(e)
            matched.append({
                "package": app.get("package"), "name": app.get("name"),
                "sonic_project_id": project_id, "sonic_project_name": project_name,
                "matched": bool(found), "project": found or None, "suite": suite_binding,
            })
            if not probe_project_id and found:
                probe_project_id = safe_int(found.get("id"), 0)
        probes = [sonic_probe_endpoint("/projects/list"), sonic_probe_token()]
        if probe_project_id:
            probes.extend([
                sonic_probe_endpoint("/modules/list", {"projectId": probe_project_id}),
                sonic_probe_endpoint("/testCases/list", {"projectId": probe_project_id, "platform": 1, "name": "", "page": 1, "pageSize": 5, "editTimeSort": "desc"}),
                sonic_probe_endpoint("/testSuites/listAll", {"projectId": probe_project_id}),
                sonic_probe_endpoint("/testSuites/list", {"projectId": probe_project_id, "name": "", "page": 1, "pageSize": 5}),
                sonic_probe_endpoint("/results/list", {"projectId": probe_project_id, "page": 1, "pageSize": 15}),
            ])
        recommendations = []
        token_probe = next((item for item in probes if item.get("path") == "/users"), {})
        if not token_probe.get("ok"):
            auth = sonic_auth_preview()
            if auth.get("login_configured") and auth.get("login_error"):
                recommendations.append("账号密码已被服务进程读取，但自动登录失败：" + re.sub(r"\s+", " ", auth.get("login_error", ""))[:180] + "；请检查 Sonic 登录网关状态后重试。")
            elif auth.get("login_configured"):
                recommendations.append("账号密码自动登录已配置，但 Token 未通过 Sonic 校验；请检查登录账号状态并重新诊断。")
            else:
                recommendations.append("服务进程未读取到 SONIC_USERNAME/SONIC_PASSWORD；配置后重启 Python 服务再重新诊断。")
        permission_paths = [item.get("path") for item in probes if item.get("auth_status") == "permission_denied"]
        if permission_paths:
            recommendations.append("当前 Sonic 账号缺少资源权限：" + "、".join(permission_paths) + "；请在 Sonic 角色资源中授权这些接口。")
        unmatched_apps = [item.get("name") or item.get("package") for item in matched if not item.get("matched")]
        if unmatched_apps:
            recommendations.append("这些应用尚未匹配 Sonic 项目：" + "、".join(unmatched_apps) + "；填写项目名称后保存。")
        unbound_suites = [item.get("name") or item.get("package") for item in matched if item.get("matched") and not (item.get("suite") or {}).get("configured")]
        if unbound_suites:
            recommendations.append("这些应用未绑定测试套：" + "、".join(unbound_suites) + "；填写测试套名称后保存，平台会自动回填 ID 并同步用例。")
        invalid_suites = [item.get("name") or item.get("package") for item in matched if (item.get("suite") or {}).get("error")]
        if invalid_suites:
            recommendations.append("这些应用的测试套绑定无效：" + "、".join(invalid_suites) + "；请重新选择对应项目内的测试套。")
        missing_webhooks = []
        invalid_webhooks = []
        for item in matched:
            app_name_val = item.get("name") or item.get("package")
            try:
                if not task_app_feishu_webhook(sonic_suite_app_info(item.get("package") or "", "")):
                    missing_webhooks.append(app_name_val)
            except ValueError:
                invalid_webhooks.append(app_name_val)
        if missing_webhooks:
            recommendations.append("这些应用未配置飞书汇总群：" + "、".join(missing_webhooks) + "；请在应用分组中填写 Webhook，避免跨应用误发。")
        if invalid_webhooks:
            recommendations.append("这些应用的飞书 Webhook 格式无效：" + "、".join(invalid_webhooks) + "；只填写单行机器人地址，不要粘贴 export 命令或中文引号。")
        if not recommendations:
            recommendations.append("Sonic 接入检查通过；同步到 Sonic 后，新用例会自动加入绑定测试套并按整套汇总通知。")
        handler._json({
            "ok": True,
            "base_url": sonic_base_url(),
            "token_configured": bool(sonic_token()),
            "token_source": sonic_token_source(),
            "token_fingerprint": sonic_token_fingerprint(),
            "auth": sonic_auth_preview(),
            "login_configured": sonic_auth_preview()["login_configured"],
            "source_findings": [
                "Sonic 2.7.2 Gateway 读取请求头 SonicToken；/projects/list 是网关白名单，成功不代表 token 已通过鉴权。",
                "Controller PermissionFilter 会按 servletPath + HTTP method 校验角色资源权限，例如 GET /results/list。",
                "Sonic 2.7.2 登录接口为 POST /users/login，Task 平台可通过 SONIC_USERNAME/SONIC_PASSWORD 自动刷新 SonicToken。",
                "测试套执行总数来自 sendMsgCount，只有 receiveMsgCount 达到 sendMsgCount 才按执行完成汇总。",
            ],
            "projects": projects, "apps": matched, "probe_project_id": probe_project_id,
            "probes": probes, "recommendations": recommendations,
        })
    except Exception as e:
        handler._json({
            "ok": False, "error": str(e),
            "base_url": sonic_base_url(),
            "token_configured": bool(sonic_token()),
            "token_source": sonic_token_source(),
            "token_fingerprint": sonic_token_fingerprint(),
            "auth": sonic_auth_preview(),
            "login_configured": sonic_auth_preview()["login_configured"],
            "probes": [sonic_probe_endpoint("/projects/list")] if sonic_token() else []
        }, 500)


# ── Sonic 遗留扫描 ──────────────────────────────────────────────────

@route_post("/api/sonic/scan-legacy")
def _post_sonic_scan_legacy(handler, qs):
    d = handler._body()
    try:
        rows = sonic_scan_midscene_cases(
            app_package=d.get("app_package") or d.get("appPackage") or "",
            module=d.get("module", ""),
            file=clean_filename(d.get("file", "")) if d.get("file") else "",
            include_current=safe_bool(d.get("includeCurrent") or d.get("include_current"))
        )
        handler._json({
            "ok": True, "total": len(rows),
            "migratable": len([row for row in rows if row.get("action") == "migrate"]),
            "manual": len([row for row in rows if row.get("action") == "manual"]),
            "current": len([row for row in rows if row.get("step_state") == "bridge"]),
            "legacy": len([row for row in rows if row.get("step_state") == "legacy"]),
            "mixed": len([row for row in rows if row.get("step_state") == "mixed"]),
            "rows": rows
        })
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)


# ── Sonic 迁移遗留 ──────────────────────────────────────────────────

@route_post("/api/sonic/migrate-legacy")
def _post_sonic_migrate_legacy(handler, qs):
    d = handler._body()
    try:
        handler._json(sonic_migrate_midscene_cases(d))
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)


# ── Sonic 刷新桥接脚本 ──────────────────────────────────────────────

@route_post("/api/sonic/refresh-bridges")
def _post_sonic_refresh_bridges(handler, qs):
    d = handler._body()
    try:
        handler._json(sonic_refresh_bridge_scripts(d))
    except Exception as e:
        handler._json({"ok": False, "error": str(e), "results": []}, 500)


# ── Sonic 发布前检查 ────────────────────────────────────────────────

@route_post("/api/sonic/publish-check")
def _post_sonic_publish_check(handler, qs):
    d = handler._body()
    try:
        result = sonic_publish_precheck(d)
        handler._json(result)
    except Exception as e:
        handler._json({"ok": False, "error": str(e), "canPublish": False, "blockers": [str(e)]}, 500)


# ── Sonic 批量发布 ──────────────────────────────────────────────────

@route_post("/api/sonic/publish-batch")
def _post_sonic_publish_batch(handler, qs):
    d = handler._body()
    try:
        handler._json(sonic_publish_batch(d))
    except Exception as e:
        handler._json({"ok": False, "error": str(e), "results": []}, 500)


# ── Sonic 单条发布 ──────────────────────────────────────────────────

@route_post("/api/sonic/publish")
def _post_sonic_publish(handler, qs):
    d = handler._body()
    case_id = d.get("case_id") or d.get("caseId") or ""
    try:
        result = sonic_publish_yaml(d)
        handler._json(result, 200 if result.get("ok") else 400)
    except Exception as e:
        if case_id:
            with SONIC_LOCK:
                sync = load_sonic_sync()
                row = sync.setdefault("cases", {}).get(case_id, {"case_id": case_id, "module": d.get("module", ""), "file": d.get("file", ""), "task_name": d.get("taskName") or d.get("task_name") or ""})
                row.update({"status": "failed", "last_error": str(e), "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                sync["cases"][case_id] = row
                save_sonic_sync(sync)
        handler._json({"ok": False, "error": str(e)}, 500)


# ── Sonic 单条执行策略说明 ───────────────────────────────────────────

@route_post("/api/sonic/run-case")
def _post_sonic_run_case(handler, qs):
    handler._json({
        "ok": False,
        "deprecated": True,
        "error": "Sonic 单条临时测试套执行已下线；单条/多条调试请走本地 Windows/Mac Runner。",
        "runnerEndpoint": "/api/run-request",
        "suggestion": "Sonic 只用于已入库/基线用例的测试套回归和桥接脚本同步，避免创建临时套导致 Sonic 平台数据混乱。"
    }, 410)


@route_get("/api/sonic/run-case")
def _get_sonic_run_case(handler, qs):
    handler._json({
        "ok": False,
        "deprecated": True,
        "error": "Sonic 单条临时测试套执行已下线；此接口不再创建 Sonic 临时测试套。",
        "runnerEndpoint": "/api/run-request",
        "suggestion": "请在页面点击“Runner 单条调试”，或 POST /api/run-request 创建本地 Runner 调试任务。"
    }, 410)


# ── 文件操作 ────────────────────────────────────────────────────────

@route_post("/api/file/op")
def _post_file_op(handler, qs):
    d = handler._body()
    op = d.get("op") or "copy"
    move = op in ("move", "rename")
    src_module = d.get("module", "")
    src_file = d.get("file", "")
    dst_module = d.get("targetModule") or d.get("target_module") or src_module
    dst_file = d.get("targetFile") or d.get("target_file") or src_file
    overwrite = safe_bool(d.get("overwrite"))
    if not src_module or not src_file or not dst_module or not dst_file:
        handler._json({"ok": False, "error": "源模块、源文件、目标模块、目标文件不能为空"}, 400)
        return
    try:
        final_file = copy_or_move_task_file(src_module, src_file, dst_module, dst_file, move=move, overwrite=overwrite)
    except FileNotFoundError as e:
        handler._json({"ok": False, "error": str(e)}, 404)
        return
    except FileExistsError as e:
        handler._json({"ok": False, "error": str(e)}, 409)
        return
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)
        return
    handler._json({"ok": True, "op": op, "module": dst_module, "file": final_file})


@route_post("/api/files/op")
def _post_files_op(handler, qs):
    d = handler._body()
    op = d.get("op") or "move"
    if op not in ("move", "copy"):
        handler._json({"ok": False, "error": "批量操作只支持 move/copy"}, 400)
        return
    items = d.get("items") or []
    dst_module = d.get("targetModule") or d.get("target_module") or ""
    overwrite = safe_bool(d.get("overwrite"))
    if not items or not dst_module:
        handler._json({"ok": False, "error": "请选择文件和目标模块"}, 400)
        return
    results = []
    errors = []
    for item in items:
        src_module = item.get("module", "")
        src_file = item.get("file", "")
        try:
            final_file = copy_or_move_task_file(src_module, src_file, dst_module, src_file, move=(op == "move"), overwrite=overwrite)
            results.append({"module": src_module, "file": src_file, "targetModule": dst_module, "targetFile": final_file})
        except Exception as e:
            errors.append({"module": src_module, "file": src_file, "error": str(e)})
    handler._json({
        "ok": len(errors) == 0,
        "error": f"{len(errors)} 个文件操作失败" if errors else "",
        "results": results, "errors": errors
    }, 207 if errors else 200)


# ── 文件恢复 ────────────────────────────────────────────────────────

@route_post("/api/file/restore")
def _post_file_restore(handler, qs):
    d = handler._body()
    mod = d.get("module", "")
    file = d.get("file", "")
    version_id = d.get("version") or d.get("id")
    if not mod or not file or not version_id:
        handler._json({"ok": False, "error": "module、file 和 version 不能为空"}, 400)
        return
    try:
        meta, content = read_file_version(mod, file, version_id)
        fpath = safe_join(TASK_DIR, mod, clean_filename(file))
        if os.path.exists(fpath):
            save_file_version(mod, file, reason="before_restore")
        write_text_file(fpath, content)
    except FileNotFoundError:
        handler._json({"ok": False, "error": "版本不存在"}, 404)
        return
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({"ok": True, "version": meta})


# ── 文件保存 ────────────────────────────────────────────────────────

@route_post("/api/file")
def _post_file_save(handler, qs):
    d = handler._body()
    mod = d.get("module", "")
    file = clean_filename(d.get("file", ""))
    content = d.get("content", "")
    try:
        module_dir = safe_join(TASK_DIR, mod)
        os.makedirs(module_dir, exist_ok=True)
        fpath = safe_join(module_dir, file)
        if os.path.exists(fpath):
            save_file_version(mod, file, reason=d.get("reason") or "save")
        write_text_file(fpath, content)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({"ok": True})


# ── Agent Runs ──────────────────────────────────────────────────────

@route_post("/api/agent-runs/start")
def _post_agent_runs_start(handler, qs):
    d = handler._body()
    run = create_agent_run(d)
    run = advance_agent_run(run["runId"])
    handler._json({"ok": True, "run": run})


@route_post("/api/agent-runs/preview")
def _post_agent_runs_preview(handler, qs):
    d = handler._body()
    goal = str(d.get("target") or d.get("goal") or "").strip()
    app_name = str(d.get("appName") or "").strip() or "智小白3D APP"
    platform = str(d.get("platform") or "android").strip()
    scope = str(d.get("scope") or "smoke").strip()
    mode = str(d.get("mode") or "AUTO_SAFE").upper()
    risk_hits = [kw for kw in AGENT_RISK_KEYWORDS if kw in goal]
    handler._json({
        "ok": True,
        "plan": {
            "mode": mode, "appName": app_name, "platform": platform, "scope": scope,
            "riskHits": risk_hits,
            "steps": [
                "1. 分析测试目标",
                "2. 整理输入来源",
                "3. 匹配已有用例或生成新用例",
                "4. 生成并校验 Midscene YAML",
                "5. 通过 Windows/Mac Runner 执行已确认 YAML",
                "6. 收集报告并分析失败",
                "7. SCRIPT_ISSUE 生成修复草稿；PRODUCT_BUG 生成缺陷草稿",
                "8. Runner 测试动作风险仅提醒；平台级写操作进入 WAIT_CONFIRM",
                "9. 生成总结报告"
            ]
        }
    })


@route_post_regex(r"^/api/agent-runs/([^/]+)/confirm$")
def _post_agent_runs_confirm(handler, qs, match):
    run_id = urllib.parse.unquote(match.group(1))
    d = handler._body()
    confirm_id = d.get("confirmationId") or d.get("id") or ""
    if not confirm_id:
        run = next((r for r in load_agent_runs() if r.get("runId") == run_id), None)
        pending = (run or {}).get("pendingConfirmations") or []
        confirm_id = pending[0].get("id") if pending else ""
    action = d.get("action") or d.get("decision") or "confirmed"
    result = confirm_agent_step(run_id, confirm_id, action, d)
    if not result:
        handler._json({"ok": False, "error": "Agent Run 不存在"}, 404)
        return
    if isinstance(result, dict) and result.get("error"):
        handler._json({"ok": False, "error": result.get("error"), "run": result.get("run")}, 400)
        return
    if result.get("status") == "RUNNING":
        _start_agent_worker(run_id)
    handler._json({"ok": True, "run": result})


@route_post_regex(r"^/api/agent-runs/([^/]+)/cancel$")
def _post_agent_runs_cancel(handler, qs, match):
    run_id = urllib.parse.unquote(match.group(1))
    d = handler._body()
    run = cancel_agent_run(run_id, d.get("reason") or "用户取消")
    if not run:
        handler._json({"ok": False, "error": "Agent Run 不存在"}, 404)
        return
    handler._json({"ok": True, "run": run})


# ── Agent Context ───────────────────────────────────────────────────

@route_post("/api/agent-context")
def _post_agent_context(handler, qs):
    from task_server.services.system_context_service import build_agent_context
    d = handler._body()
    if not isinstance(d, dict):
        handler._json({'ok': False, 'error': '请求体必须是 JSON 对象'}, 400)
        return
    ctx = build_agent_context(
        target=str(d.get('target') or '').strip(),
        app_name=str(d.get('appName') or '智小白3D APP'),
        module=str(d.get('module') or '').strip()
    )
    handler._json({'ok': True, 'context': ctx})


# ══════════════════════════════════════════════════════════════════════
#  DELETE 路由注册
# ══════════════════════════════════════════════════════════════════════

# ── 通用 DELETE 认证守卫 ────────────────────────────────────────────

def _require_delete_auth(handler):
    """DELETE 请求通用认证检查，未通过返回 True。"""
    qs, path = handler._qs()
    if path.startswith("/api/"):
        return _require_user_auth(handler)
    return False


# ── Agent 运行记录删除 ──────────────────────────────────────────────

@route_delete_regex(r"^/api/agent-runs/([^/]+)$")
def _delete_agent_run(handler, qs, match):
    if _require_delete_auth(handler):
        return
    run_id = urllib.parse.unquote(match.group(1))
    result = delete_agent_run(run_id)
    status = int(result.pop("status", 200) or 200)
    handler._json(result, status)


# ── 文件删除 ────────────────────────────────────────────────────────

@route_delete("/api/file")
def _delete_file(handler, qs):
    if _require_delete_auth(handler):
        return
    try:
        fpath = safe_join(TASK_DIR, qs.get("module", ""), qs.get("file", ""))
        if os.path.exists(fpath):
            os.remove(fpath)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({"ok": True})


# ── 模块删除 ────────────────────────────────────────────────────────

@route_delete("/api/module")
def _delete_module(handler, qs):
    if _require_delete_auth(handler):
        return
    try:
        mp = safe_join(TASK_DIR, qs.get("module", ""))
        if os.path.exists(mp):
            shutil.rmtree(mp)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({"ok": True})


# ── Task App 删除 ───────────────────────────────────────────────────

@route_delete("/api/task-app")
def _delete_task_app(handler, qs):
    if _require_delete_auth(handler):
        return
    package = qs.get("package") or qs.get("app_package") or qs.get("appPackage")
    if not package:
        handler._json({"ok": False, "error": "包名不能为空"}, 400)
        return
    data = load_task_apps()
    data["apps"] = [item for item in data.get("apps", []) if item.get("package") != package]
    save_task_apps(data)
    handler._json({"ok": True})


# ── 知识库页面删除 ──────────────────────────────────────────────────

@route_delete("/api/knowledge/page")
def _delete_knowledge_page(handler, qs):
    if _require_delete_auth(handler):
        return
    app_package = qs.get("app_package") or qs.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    page_id = qs.get("page_id") or qs.get("pageId")
    if not page_id:
        handler._json({"ok": False, "error": "page_id 不能为空"}, 400)
        return
    try:
        page_dir = knowledge_page_dir(app_package, page_id)
        if os.path.exists(page_dir):
            shutil.rmtree(page_dir)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({"ok": True})


# ── 脑图删除 ────────────────────────────────────────────────────────

@route_delete("/api/cases/mindmap")
def _delete_cases_mindmap(handler, qs):
    if _require_delete_auth(handler):
        return
    case_set_id = qs.get("case_set_id") or qs.get("id")
    if not case_set_id:
        handler._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
        return
    try:
        result = remove_generation_mindmap_file(case_set_id)
        mark_generation_mindmap_deleted(case_set_id)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({
        "ok": True,
        "deleted": bool(result.get("existed")),
        "removed": bool(result.get("removed")),
        "delete_warning": result.get("error") or "",
        "mindmap_deleted": True,
    })


@route_delete("/api/cases/mindmap-record")
def _delete_cases_mindmap_record(handler, qs):
    if _require_delete_auth(handler):
        return
    case_set_id = qs.get("case_set_id") or qs.get("id")
    if not case_set_id:
        handler._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
        return
    try:
        summary = read_json_file(generation_summary_path(case_set_id), default=None)
        if not isinstance(summary, dict):
            handler._json({"ok": False, "error": "脑图记录不存在"}, 404)
            return
        now_text = time.strftime("%Y-%m-%d %H:%M:%S")
        result = remove_generation_mindmap_file(case_set_id)
        mark_generation_mindmap_deleted(case_set_id, now_text)
        mark_generation_mindmap_record_deleted(case_set_id, now_text)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({
        "ok": True,
        "deleted": True,
        "removed": bool(result.get("removed")),
        "delete_warning": result.get("error") or "",
        "case_set_id": case_set_id,
        "mindmap_record_deleted": True,
    })


# ── UI 设计稿删除 ──────────────────────────────────────────────────

@route_delete("/api/cases/ui-design")
def _delete_cases_ui_design(handler, qs):
    if _require_delete_auth(handler):
        return
    case_set_id = qs.get("case_set_id") or qs.get("id")
    asset_id = qs.get("asset_id") or qs.get("assetId") or ""
    filename = qs.get("filename") or ""
    if not case_set_id or not (asset_id or filename):
        handler._json({"ok": False, "error": "case_set_id 和 asset_id 不能为空"}, 400)
        return
    try:
        deleted, meta = delete_case_ui_design_asset(case_set_id, asset_id=asset_id, filename=filename)
        summary = read_json_file(generation_summary_path(case_set_id), default=None)
        if summary:
            summary["ui_design_assets"] = meta.get("designs") or []
            write_generation_summary(case_set_id, summary)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    except Exception as e:
        handler._json({"ok": False, "error": str(e)}, 500)
        return
    handler._json({"ok": True, "deleted": deleted, "ui_designs": meta})


# ── 知识库应用删除 ──────────────────────────────────────────────────

@route_delete("/api/knowledge/app")
def _delete_knowledge_app(handler, qs):
    if _require_delete_auth(handler):
        return
    app_package = qs.get("app_package") or qs.get("appPackage")
    if not app_package:
        handler._json({"ok": False, "error": "app_package 不能为空"}, 400)
        return
    try:
        app_dir = knowledge_app_dir(app_package)
        if os.path.exists(app_dir):
            shutil.rmtree(app_dir)
    except ValueError:
        handler._json({"ok": False, "error": "非法路径"}, 400)
        return
    handler._json({"ok": True})
