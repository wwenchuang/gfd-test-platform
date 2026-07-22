"""Read-only Apifox OpenAPI export adapter."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict


APIFOX_API_VERSION = "2024-03-28"
APIFOX_CLI_API_VERSION = "2026-05-28"
APIFOX_USER_AGENT = "midscene-task-platform/api-sync"
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class ApifoxRequestError(ValueError):
    """Safe public error for Apifox configuration and export failures."""


def _header_value(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    try:
        value = headers.get(name, "")
        if value:
            return str(value)
    except Exception:
        pass
    if isinstance(headers, dict):
        target = name.lower()
        for key, value in headers.items():
            if str(key).lower() == target:
                return str(value or "")
    return ""


def _safe_error_text(value: Any, token: str = "") -> str:
    text = str(value or "").strip()
    if token:
        text = text.replace(token, "[REDACTED]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+|bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    return text[:500]


def _canonical_hash(document: Dict[str, Any]) -> str:
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _environment_ids(value: Any) -> list[int]:
    values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,\s]+", str(value or ""))
    result: list[int] = []
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        if not text.isdigit():
            raise ApifoxRequestError("Apifox environment_id 必须是数字 ID")
        environment_id = int(text)
        if environment_id not in result:
            result.append(environment_id)
    return result


def _branch_value(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    return int(text) if text.isdigit() else text


class _ApifoxRouteUnavailable(Exception):
    pass


class ApifoxSourceAdapter:
    def __init__(
        self,
        opener: Callable[..., Any] | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._opener = opener or urllib.request.urlopen
        self._max_response_bytes = max(1, int(max_response_bytes))

    def _read_response(self, request: urllib.request.Request, timeout: float, token: str) -> tuple[bytes, Any]:
        try:
            with self._opener(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status < 200 or status >= 300:
                    if status in {404, 405}:
                        raise _ApifoxRouteUnavailable()
                    raise ApifoxRequestError(f"Apifox HTTP {status}")
                raw = response.read(self._max_response_bytes + 1)
                if len(raw) > self._max_response_bytes:
                    raise ApifoxRequestError("Apifox OpenAPI 响应过大，超过 20 MiB 上限")
                return raw, getattr(response, "headers", None)
        except (_ApifoxRouteUnavailable, ApifoxRequestError):
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 405}:
                raise _ApifoxRouteUnavailable() from None
            raise ApifoxRequestError(f"Apifox HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise ApifoxRequestError(f"Apifox 请求失败：{_safe_error_text(exc.reason, token)}") from None
        except Exception as exc:
            raise ApifoxRequestError(f"Apifox 请求失败：{_safe_error_text(exc, token)}") from None

    def fetch_openapi(self, source: Dict[str, Any], timeout: float = 30) -> Dict[str, Any]:
        project_id = str(source.get("project_id") or "").strip()
        token = str(source.get("access_token") or source.get("token") or "").strip()
        if not project_id:
            raise ApifoxRequestError("Apifox project_id 未配置")
        if not token:
            raise ApifoxRequestError("Apifox 访问令牌未配置")
        base_url = str(source.get("base_url") or "https://api.apifox.com").strip().rstrip("/")
        parsed_base = urllib.parse.urlparse(base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
            raise ApifoxRequestError("Apifox base_url 无效")
        encoded_project_id = urllib.parse.quote(project_id, safe="")
        payload: Dict[str, Any] = {
            "scope": {"type": "ALL"},
            "options": {
                "includeApifoxExtensionProperties": True,
                "addFoldersToTags": False,
            },
            "oasVersion": "3.0",
        }
        environment_ids = _environment_ids(source.get("environment_ids", source.get("environment_id")))
        if environment_ids:
            payload["environmentIds"] = environment_ids
        official_payload = dict(payload)
        official_payload["exportFormat"] = "JSON"
        branch_id = _branch_value(source.get("branch_id"))
        if branch_id is not None:
            official_payload["branchId"] = branch_id
        official_request = urllib.request.Request(
            f"{base_url}/v1/projects/{encoded_project_id}/export-openapi?locale=zh-CN",
            data=json.dumps(official_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": APIFOX_USER_AGENT,
                "X-Apifox-Api-Version": APIFOX_API_VERSION,
            },
            method="POST",
        )
        use_cli_fallback = False
        try:
            raw, response_headers = self._read_response(official_request, timeout, token)
            use_cli_fallback = not raw
        except _ApifoxRouteUnavailable:
            use_cli_fallback = True
        if use_cli_fallback:
            fallback_headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": APIFOX_USER_AGENT,
                "X-Apifox-Api-Version": APIFOX_CLI_API_VERSION,
                "X-Project-Id": project_id,
            }
            branch_text = str(source.get("branch_id") or "").strip()
            if branch_text:
                fallback_headers["X-Branch-Id"] = branch_text
            fallback_request = urllib.request.Request(
                f"{base_url}/api/v1/projects/{encoded_project_id}/export-openapi",
                data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers=fallback_headers,
                method="POST",
            )
            raw, response_headers = self._read_response(fallback_request, timeout, token)
        if not raw:
            raise ApifoxRequestError("Apifox OpenAPI 响应为空")
        etag = _header_value(response_headers, "ETag")
        last_modified = _header_value(response_headers, "Last-Modified")
        try:
            document = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ApifoxRequestError(f"Apifox OpenAPI JSON 解析失败：{_safe_error_text(exc)}") from None
        if not isinstance(document, dict):
            raise ApifoxRequestError("Apifox OpenAPI JSON 必须是对象")
        paths = document.get("paths")
        if not isinstance(paths, dict) or not paths:
            raise ApifoxRequestError("Apifox OpenAPI paths 为空")
        return {
            "document": document,
            "document_hash": _canonical_hash(document),
            "etag": etag,
            "last_modified": last_modified,
            "source_revision": etag or last_modified,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def probe(self, source: Dict[str, Any], timeout: float = 30) -> Dict[str, Any]:
        result = self.fetch_openapi(source, timeout=timeout)
        return {
            "ok": True,
            "document_hash": result.get("document_hash"),
            "source_revision": result.get("source_revision"),
            "endpoint_paths": len((result.get("document") or {}).get("paths") or {}),
        }


__all__ = [
    "APIFOX_API_VERSION",
    "APIFOX_CLI_API_VERSION",
    "APIFOX_USER_AGENT",
    "ApifoxRequestError",
    "ApifoxSourceAdapter",
    "DEFAULT_MAX_RESPONSE_BYTES",
]
