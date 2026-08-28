"""Small urllib-based HTTP client with explicit timeout and JSON helpers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict


class ResponseTooLargeError(RuntimeError):
    """Raised before an upstream response can grow process memory without bound."""


def read_response_bytes(response, max_bytes: int, label: str = "上游服务") -> bytes:
    max_bytes = max(1, int(max_bytes))
    content_length = ""
    try:
        content_length = str(response.headers.get("Content-Length") or "").strip()
    except Exception:
        content_length = ""
    if content_length.isdigit() and int(content_length) > max_bytes:
        raise ResponseTooLargeError(
            f"{label}响应超过 {max_bytes} 字节限制（Content-Length={content_length}）"
        )
    payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ResponseTooLargeError(f"{label}响应超过 {max_bytes} 字节限制")
    return payload


@dataclass
class HttpResponse:
    status: int
    body: str
    headers: Dict[str, str]

    @property
    def ok(self) -> bool:
        return 200 <= int(self.status or 0) < 300

    def json(self, default: Any = None) -> Any:
        if not self.body:
            return default
        try:
            return json.loads(self.body)
        except Exception:
            return default


class HttpClient:
    """Thin wrapper around urllib to avoid scattered ad-hoc network calls."""

    def request(
        self,
        url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: Dict[str, str] | None = None,
        timeout: int | float = 10,
        read_limit: int | None = None,
    ) -> HttpResponse:
        req = urllib.request.Request(
            str(url),
            data=data,
            headers=headers or {},
            method=str(method or "GET").upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = (
                    read_response_bytes(resp, read_limit, "HTTP")
                    if read_limit is not None
                    else resp.read()
                )
                raw = payload.decode("utf-8", errors="replace")
                return HttpResponse(int(resp.status), raw, dict(resp.headers.items()))
        except urllib.error.HTTPError as exc:
            payload = (
                read_response_bytes(exc, read_limit, "HTTP 错误")
                if read_limit is not None
                else exc.read()
            )
            body = payload.decode("utf-8", errors="replace")
            return HttpResponse(int(exc.code or 0), body, dict(exc.headers.items()) if exc.headers else {})

    def get(
        self,
        url: str,
        headers: Dict[str, str] | None = None,
        timeout: int | float = 10,
        read_limit: int | None = None,
    ) -> HttpResponse:
        return self.request(url, method="GET", headers=headers, timeout=timeout, read_limit=read_limit)

    def post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str] | None = None,
        timeout: int | float = 30,
        read_limit: int | None = None,
    ) -> HttpResponse:
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        merged_headers = {"Content-Type": "application/json; charset=utf-8"}
        merged_headers.update(headers or {})
        return self.request(
            url,
            method="POST",
            data=data,
            headers=merged_headers,
            timeout=timeout,
            read_limit=read_limit,
        )


http_client = HttpClient()
