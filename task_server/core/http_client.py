"""Small urllib-based HTTP client with explicit timeout and JSON helpers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict


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
                raw = resp.read(read_limit).decode("utf-8", errors="replace")
                return HttpResponse(int(resp.status), raw, dict(resp.headers.items()))
        except urllib.error.HTTPError as exc:
            body = exc.read(read_limit).decode("utf-8", errors="replace")
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
