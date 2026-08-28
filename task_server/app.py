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
    MAX_CONCURRENT_REQUESTS, MAX_CONCURRENT_LARGE_REQUESTS,
    LARGE_REQUEST_THRESHOLD,
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
    dispatch_get, dispatch_post, dispatch_put, dispatch_delete, dispatch_head,
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
    block_on_close = False
    request_queue_size = 128

    def __init__(self, server_address, handler_class, bind_and_activate=True, max_requests=None):
        self.max_requests = max(1, int(max_requests or MAX_CONCURRENT_REQUESTS))
        self.max_large_requests = MAX_CONCURRENT_LARGE_REQUESTS
        self._request_slots = threading.BoundedSemaphore(self.max_requests)
        self._large_request_slots = threading.BoundedSemaphore(self.max_large_requests)
        self._runtime_lock = threading.Lock()
        self._active_requests = 0
        self._active_large_requests = 0
        super().__init__(server_address, handler_class, bind_and_activate=bind_and_activate)

    @staticmethod
    def _reject_busy(request):
        body = json.dumps({
            "ok": False,
            "error": "服务当前请求较多，请稍后重试",
        }, ensure_ascii=False).encode("utf-8")
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Connection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            + body
        )
        try:
            request.sendall(response)
        except OSError:
            pass

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            self._reject_busy(request)
            self.shutdown_request(request)
            return
        with self._runtime_lock:
            self._active_requests += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release_request_slot()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_request_slot()

    def _release_request_slot(self):
        with self._runtime_lock:
            self._active_requests = max(0, self._active_requests - 1)
        self._request_slots.release()

    def acquire_large_request(self):
        acquired = self._large_request_slots.acquire(blocking=False)
        if acquired:
            with self._runtime_lock:
                self._active_large_requests += 1
        return acquired

    def release_large_request(self):
        with self._runtime_lock:
            self._active_large_requests = max(0, self._active_large_requests - 1)
        self._large_request_slots.release()

    def runtime_status(self):
        with self._runtime_lock:
            return {
                "active_requests": self._active_requests,
                "max_requests": self.max_requests,
                "active_large_requests": self._active_large_requests,
                "max_large_requests": self.max_large_requests,
            }


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
        return self._safe_write_call(lambda: dispatch_post(self))

    def do_PUT(self):
        return self._safe_write_call(lambda: dispatch_put(self))

    def do_DELETE(self):
        return self._safe_call(lambda: dispatch_delete(self))

    def _safe_write_call(self, fn):
        _qs, path = self._qs()
        if path.startswith("/api/api-testing/"):
            return self._safe_call(fn)
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._json({"ok": False, "error": "Content-Length 格式无效"}, 400)
            return
        if length <= LARGE_REQUEST_THRESHOLD:
            return self._safe_call(fn)
        if not self.server.acquire_large_request():
            self._json({
                "ok": False,
                "error": "当前有较大的上传或生成请求正在处理，请稍后重试",
            }, 503)
            return
        try:
            return self._safe_call(fn)
        finally:
            self.server.release_large_request()

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
_API_TEST_BUILD_ROOT = os.path.join(_PROJECT_ROOT, "api-test")


def _send_static_file(handler, file_path):
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", guess_mime(file_path))
    handler.send_header("Cache-Control", "public, max-age=3600")
    handler.send_header("Content-Length", str(os.path.getsize(file_path)))
    handler.end_headers()
    with open(file_path, "rb") as f:
        shutil.copyfileobj(f, handler.wfile)


def _serve_api_test(handler, path):
    if path != "/api-test" and not path.startswith("/api-test/"):
        return False

    root = os.path.abspath(_API_TEST_BUILD_ROOT)
    relative_path = urllib.parse.unquote(path[len("/api-test"):]).lstrip("/")
    candidate = os.path.abspath(os.path.join(root, relative_path))
    if "\\" in relative_path or os.path.commonpath((root, candidate)) != root:
        handler._text("api test asset not found", 404)
        return True
    if relative_path and os.path.isfile(candidate):
        _send_static_file(handler, candidate)
        return True
    if relative_path.startswith("assets/") or os.path.splitext(relative_path)[1]:
        handler._text("api test asset not found", 404)
        return True

    index_path = os.path.join(root, "index.html")
    if os.path.isfile(index_path):
        handler._html(read_text_file(index_path))
    else:
        handler._text("api test application not found", 404)
    return True


def _serve_static(handler, path):
    """处理静态文件请求（HTML/CSS/JS/图片等）。"""
    if _serve_api_test(handler, path):
        return True

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
        handler.send_header("Content-Length", str(os.path.getsize(asset_path)))
        handler.end_headers()
        with open(asset_path, "rb") as f:
            shutil.copyfileobj(f, handler.wfile)
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
        handler.send_header("Content-Length", str(os.path.getsize(file_path)))
        handler.end_headers()
        with open(file_path, "rb") as f:
            shutil.copyfileobj(f, handler.wfile)
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
        handler.send_header("Content-Length", str(os.path.getsize(file_path)))
        handler.end_headers()
        with open(file_path, "rb") as f:
            shutil.copyfileobj(f, handler.wfile)
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
    from .background_jobs import restore_persisted_background_jobs
    from .services.sonic_service import restore_pending_sonic_suite_summary_timers
    from .services.report_service import start_report_cleanup_scheduler
    from .runtime_metrics import start_runtime_memory_monitor
    restore_persisted_background_jobs()
    restore_pending_sonic_suite_summary_timers()
    start_report_cleanup_scheduler()
    start_runtime_memory_monitor()


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
