"""HTTP 响应工具方法 Mixin，供 Handler 继承使用。"""

import json
import os
import threading
import time
import traceback
import urllib.parse
from pathlib import Path

from .config import MAX_BODY_SIZE, MAX_UPLOAD_BODY_SIZE, TASK_ALLOWED_ORIGINS


class BodyTooLarge(Exception):
    pass


class InvalidBodyLength(Exception):
    pass


def _limit_mb(limit):
    try:
        return max(1, round(int(limit) / 1024 / 1024))
    except Exception:
        return 0


def request_body_limit(path):
    """Return the in-memory body limit for a route.

    The legacy raw report endpoint is streamed to disk and may use the larger
    upload limit. JSON and base64 chunk endpoints stay on the normal limit.
    """
    return MAX_UPLOAD_BODY_SIZE if path == "/report" else MAX_BODY_SIZE


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
        except InvalidBodyLength as e:
            try:
                self._json({"ok": False, "error": str(e) or "Content-Length 格式无效"}, 400)
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
        if getattr(self, "_main_access", None) is not None:
            from task_server.access_control import filter_access_response
            data = filter_access_response(self, data, code)
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
        if length < 0:
            raise InvalidBodyLength("Content-Length 不能为负数")
        _qs, path = self._qs()
        limit = request_body_limit(path)
        if length > limit:
            raise BodyTooLarge(f"请求体过大，当前上限约 {_limit_mb(limit)}MB")
        return self.rfile.read(length) if length else b""

    def _stream_body_to_file(self, destination, chunk_size=1024 * 1024):
        """Stream the current request body to *destination* using an atomic rename."""
        length = int(self.headers.get("Content-Length", 0))
        _qs, path = self._qs()
        limit = request_body_limit(path)
        if length < 0:
            raise InvalidBodyLength("Content-Length 不能为负数")
        if length > limit:
            raise BodyTooLarge(f"请求体过大，当前上限约 {_limit_mb(limit)}MB")
        chunk_size = max(1, min(int(chunk_size or 0), 1024 * 1024))
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(
            f".{target.name}.tmp.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}"
        )
        remaining = length
        try:
            with open(tmp, "wb") as output:
                while remaining:
                    chunk = self.rfile.read(min(chunk_size, remaining))
                    if not chunk:
                        raise ConnectionError("请求体未完整接收，请重新上传")
                    output.write(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return length

    def _body_size_allowed(self, path):
        """验证请求体大小是否允许"""
        length = int(self.headers.get("Content-Length", 0))
        if length < 0:
            self._json({"ok": False, "error": "Content-Length 不能为负数"}, 400)
            return False
        limit = request_body_limit(path)
        if length > limit:
            self._json({"ok": False, "error": f"请求体过大，当前上限约 {_limit_mb(limit)}MB"}, 413)
            return False
        return True

    def _body(self):
        """读取并解析 JSON 请求体（支持多编码）"""
        if hasattr(self, "_parsed_body"):
            return self._parsed_body
        raw = self._raw_body()
        if not raw:
            self._parsed_body = {}
            return self._parsed_body
        last_error = None
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin1"):
            try:
                self._parsed_body = json.loads(raw.decode(encoding))
                return self._parsed_body
            except Exception as e:
                last_error = e
        raise last_error

    # ── 查询字符串 ────────────────────────────────────────────────

    def _qs(self):
        """解析查询字符串和路径，返回 (query_params_dict, path_string)"""
        parsed = urllib.parse.urlparse(self.path)
        return dict(urllib.parse.parse_qsl(parsed.query)), parsed.path
