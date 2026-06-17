"""HTTP 响应工具方法 Mixin，供 Handler 继承使用。"""

import json
import traceback
import urllib.parse

from .config import MAX_BODY_SIZE, MAX_UPLOAD_BODY_SIZE, TASK_ALLOWED_ORIGINS


class BodyTooLarge(Exception):
    pass


class ResponseMixin:
    """HTTP 响应工具方法 Mixin，供 Handler 继承使用。"""

    # ── 安全调用 ──────────────────────────────────────────────────

    def _safe_call(self, fn):
        """安全调用包装，异常保护"""
        try:
            return fn()
        except (BrokenPipeError, ConnectionResetError):
            return
        except BodyTooLarge as e:
            try:
                self._json({"ok": False, "error": str(e) or "请求体过大"}, 413)
            except Exception:
                pass
        except Exception as e:
            print(f"{fn.__name__} failed: {e}\n{traceback.format_exc()}", flush=True)
            try:
                self._json({"ok": False, "error": f"服务端异常：{e}"}, 500)
            except Exception:
                pass

    # ── CORS ──────────────────────────────────────────────────────

    def _cors(self):
        """设置 CORS 响应头"""
        origin = self.headers.get("Origin", "")
        if origin and origin in TASK_ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        elif not origin:
            self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,x-token,x-filename,Authorization")

    # ── 响应发送 ──────────────────────────────────────────────────

    def _json(self, data, code=200):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _text(self, text, code=200):
        """发送纯文本响应"""
        body = text.encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _html(self, text, code=200):
        """发送 HTML 响应"""
        body = text.encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── 请求体读取 ────────────────────────────────────────────────

    def _raw_body(self):
        """读取原始请求体"""
        length = int(self.headers.get("Content-Length", 0))
        qs, path = self._qs()
        limit = MAX_UPLOAD_BODY_SIZE if path in ("/report", "/api/report/chunk", "/api/report/chunk-finish") else MAX_BODY_SIZE
        if length > limit:
            raise BodyTooLarge("请求体过大")
        return self.rfile.read(length) if length else b""

    def _body_size_allowed(self, path):
        """验证请求体大小是否允许"""
        length = int(self.headers.get("Content-Length", 0))
        limit = MAX_UPLOAD_BODY_SIZE if path in ("/report", "/api/report/chunk", "/api/report/chunk-finish") else MAX_BODY_SIZE
        if length > limit:
            self._json({"ok": False, "error": "请求体过大"}, 413)
            return False
        return True

    def _body(self):
        """读取并解析 JSON 请求体（支持多编码）"""
        raw = self._raw_body()
        if not raw:
            return {}
        last_error = None
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin1"):
            try:
                return json.loads(raw.decode(encoding))
            except Exception as e:
                last_error = e
        raise last_error

    # ── 查询字符串 ────────────────────────────────────────────────

    def _qs(self):
        """解析查询字符串和路径，返回 (query_params_dict, path_string)"""
        parsed = urllib.parse.urlparse(self.path)
        return dict(urllib.parse.parse_qsl(parsed.query)), parsed.path
