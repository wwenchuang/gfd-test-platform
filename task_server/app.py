"""应用入口：task_server 包的 HTTP 服务器。

TaskHTTPHandler 继承 BaseHTTPRequestHandler + ResponseMixin，
通过 router.py 注册的路由表分发请求，不再依赖 midscene-upload.py。
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

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from .config import (
    TASK_DIR, REPORT_DIR, LEARNING_DIR, ASSET_DIR,
    CASE_DIR, GENERATE_JOB_DIR, KNOWLEDGE_DIR, PORT,
    TOKEN, SONIC_CALLBACK_TOKEN, TASK_ALLOWED_ORIGINS,
    MAX_BODY_SIZE, MAX_UPLOAD_BODY_SIZE, ALLOW_QUERY_TOKEN,
    safe_int, safe_bool, AGENT_RISK_KEYWORDS,
    ENABLE_AUTOMATIC_BASELINE_REPAIR,
    validate_runtime_secrets,
)
from .auth import (
    bearer_token, verify_session_token, is_user_authorized,
    is_runner_authorized, is_sonic_callback_authorized,
    is_authorized_with_query, REVOKED_SESSION_TOKENS,
)
from .response import ResponseMixin, BodyTooLarge
from .storage import (
    safe_join, read_json_file, write_json_file, read_text_file,
    write_text_file, write_bytes_file, runtime_path_status,
    clean_filename, clean_asset_filename, clean_id, is_visible_yaml_filename,
)
from .router import (
    dispatch_get, dispatch_post, dispatch_delete, dispatch_head,
)


# ── MIME type helper ────────────────────────────────────────────────
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
    ".woff": "font/woff",
    ".woff2":"font/woff2",
    ".ttf":  "font/ttf",
    ".map":  "application/json",
}


def guess_mime(filename):
    """根据文件扩展名推断 Content-Type。"""
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_MAP.get(ext, "application/octet-stream")


# ── 线程化 HTTP 服务器 ──────────────────────────────────────────────
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ── 请求处理器 ──────────────────────────────────────────────────────
class TaskHTTPHandler(ResponseMixin, BaseHTTPRequestHandler):
    """HTTP 请求处理器。

    继承 ResponseMixin 获得 _json/_text/_html/_cors/_body/_qs/_safe_call 等工具方法，
    通过 router.py 的路由表分发 GET/POST/DELETE/HEAD 请求。
    """

    def log_message(self, format, *args):
        pass  # 静默日志

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_HEAD(self):
        return self._safe_call(lambda: dispatch_head(self))

    def do_GET(self):
        return self._safe_call(lambda: dispatch_get(self))

    def do_POST(self):
        return self._safe_call(lambda: dispatch_post(self))

    def do_DELETE(self):
        return self._safe_call(lambda: dispatch_delete(self))

    # ── 认证快捷方法（供路由 handler 调用）────────────────────────
    def _authorized(self):
        return is_user_authorized(self.headers)

    def _authorized_runner(self):
        return is_runner_authorized(self.headers)

    def _authorized_sonic_callback(self):
        return is_sonic_callback_authorized(self.headers)

    def _authorized_with_qs(self, qs):
        return is_authorized_with_query(self.headers, qs)


# ── 静态文件服务 ─────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _serve_static(handler, path):
    """处理静态文件请求（HTML/CSS/JS/图片等）。"""
    # 首页
    if path in ("/", "/task-manager.html", "/trace-viewer.html"):
        html_name = "trace-viewer.html" if path == "/trace-viewer.html" else "task-manager.html"
        html_path = os.path.join(_PROJECT_ROOT, html_name)
        if os.path.exists(html_path):
            handler._html(read_text_file(html_path))
        else:
            handler._text(f"{html_name} not found", 404)
        return True

    # /assets/ 目录
    if path.startswith("/assets/"):
        root = os.path.join(_PROJECT_ROOT, "assets").rstrip("/") + "/"
        root_abs = os.path.abspath(root)
        rel = path[len("/assets/"):].lstrip("/")
        asset_path = os.path.normpath(os.path.join(root_abs, rel))
        if not asset_path.startswith(root_abs) or not os.path.isfile(asset_path):
            handler._text("asset not found", 404)
            return True
        handler.send_response(200)
        handler._cors()
        handler.send_header("Content-Type", guess_mime(asset_path))
        handler.send_header("Cache-Control", "public, max-age=3600")
        handler.end_headers()
        with open(asset_path, "rb") as f:
            handler.wfile.write(f.read())
        return True

    # /css/ 目录
    if path.startswith("/css/"):
        root = os.path.join(_PROJECT_ROOT, "css").rstrip("/") + "/"
        root_abs = os.path.abspath(root)
        rel = path[len("/css/"):].lstrip("/")
        file_path = os.path.normpath(os.path.join(root_abs, rel))
        if not file_path.startswith(root_abs) or not os.path.isfile(file_path):
            handler._text("not found", 404)
            return True
        handler.send_response(200)
        handler._cors()
        handler.send_header("Content-Type", guess_mime(file_path))
        handler.send_header("Cache-Control", "public, max-age=3600")
        handler.end_headers()
        with open(file_path, "rb") as f:
            handler.wfile.write(f.read())
        return True

    # /js/ 目录
    if path.startswith("/js/"):
        root = os.path.join(_PROJECT_ROOT, "js").rstrip("/") + "/"
        root_abs = os.path.abspath(root)
        rel = path[len("/js/"):].lstrip("/")
        file_path = os.path.normpath(os.path.join(root_abs, rel))
        if not file_path.startswith(root_abs) or not os.path.isfile(file_path):
            handler._text("not found", 404)
            return True
        handler.send_response(200)
        handler._cors()
        handler.send_header("Content-Type", guess_mime(file_path))
        handler.send_header("Cache-Control", "public, max-age=3600")
        handler.end_headers()
        with open(file_path, "rb") as f:
            handler.wfile.write(f.read())
        return True

    return False


# ── 发送附件 ────────────────────────────────────────────────────────
def send_attachment(handler, body_bytes, filename, content_type):
    """以附件方式发送二进制内容。"""
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


# ── 启动辅助 ────────────────────────────────────────────────────────
def ensure_dirs():
    """确保所有必要目录存在"""
    for d in [TASK_DIR, REPORT_DIR, LEARNING_DIR, ASSET_DIR,
              CASE_DIR, GENERATE_JOB_DIR, KNOWLEDGE_DIR]:
        os.makedirs(d, exist_ok=True)


def start_background_jobs():
    """启动后台任务"""
    from .services.api_sync_service import start_api_sync_scheduler
    from .services.sonic_service import restore_pending_sonic_suite_summary_timers
    from .services.report_service import start_report_cleanup_scheduler
    restore_pending_sonic_suite_summary_timers()
    start_report_cleanup_scheduler()
    start_api_sync_scheduler()


# ── 主入口 ──────────────────────────────────────────────────────────
def main():
    """服务启动入口。"""
    validate_runtime_secrets()
    ensure_dirs()
    start_background_jobs()
    print(f"MidScene task server (task_server) running on port {PORT}")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), TaskHTTPHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


# 兼容旧 import
Handler = TaskHTTPHandler
