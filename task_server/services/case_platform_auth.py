"""Authenticated JSON client for the external AgileTC case platform."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


AUTHORITY_ERROR_CODE = 100011
DEFAULT_LOGIN_PATH = "/api/user/login"
DEFAULT_MFA_VERIFY_PATH = "/api/user/mfa/verify"
DEFAULT_TOTP_FIELD = "code"


class CasePlatformAuthError(RuntimeError):
    """Raised when AgileTC credentials are missing, invalid, or unauthorized."""


class CasePlatformRequestError(RuntimeError):
    """Raised when AgileTC cannot be reached or returns an invalid response."""

    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = _clean(os.environ.get(name)).lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise CasePlatformAuthError(f"{name} 必须配置为 true 或 false")


def generate_totp(
    secret: str,
    *,
    timestamp: Optional[float] = None,
    period: int = 30,
    digits: int = 6,
) -> str:
    """Generate an RFC 6238 SHA-1 TOTP without exposing the source secret."""

    normalized = "".join(_clean(secret).split()).upper()
    if not normalized:
        raise CasePlatformAuthError("用例平台动态验证码密钥未配置")
    if period <= 0 or digits <= 0:
        raise CasePlatformAuthError("用例平台动态验证码配置无效")
    try:
        padding = "=" * ((8 - len(normalized) % 8) % 8)
        key = base64.b32decode(normalized + padding, casefold=True)
    except Exception as exc:
        raise CasePlatformAuthError("用例平台动态验证码密钥格式无效") from exc

    counter = int((time.time() if timestamp is None else timestamp) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


@dataclass(frozen=True)
class CasePlatformAuthConfig:
    required: bool
    access_token: str
    token_header: str
    token_prefix: str
    username: str
    password: str
    totp_secret: str
    login_path: str
    mfa_verify_path: str
    totp_field: str

    @classmethod
    def from_env(cls) -> "CasePlatformAuthConfig":
        return cls(
            required=_env_bool("CASE_PLATFORM_AUTH_REQUIRED"),
            access_token=_clean(os.environ.get("CASE_PLATFORM_ACCESS_TOKEN")),
            token_header=_clean(os.environ.get("CASE_PLATFORM_TOKEN_HEADER")) or "Authorization",
            token_prefix=_clean(os.environ.get("CASE_PLATFORM_TOKEN_PREFIX", "Bearer")),
            username=_clean(os.environ.get("CASE_PLATFORM_USERNAME")),
            password=_clean(os.environ.get("CASE_PLATFORM_PASSWORD")),
            totp_secret=_clean(os.environ.get("CASE_PLATFORM_TOTP_SECRET")),
            login_path=_clean(os.environ.get("CASE_PLATFORM_LOGIN_PATH")) or DEFAULT_LOGIN_PATH,
            mfa_verify_path=_clean(os.environ.get("CASE_PLATFORM_MFA_VERIFY_PATH")) or DEFAULT_MFA_VERIFY_PATH,
            totp_field=_clean(os.environ.get("CASE_PLATFORM_TOTP_FIELD")) or DEFAULT_TOTP_FIELD,
        )

    @property
    def mode(self) -> str:
        if self.access_token:
            return "token"
        credential_values = (self.username, self.password, self.totp_secret)
        if all(credential_values):
            return "totp"
        if self.required or any(credential_values):
            raise CasePlatformAuthError("用例平台已要求认证，但未配置完整凭证")
        return "anonymous"

    def cache_key(self) -> str:
        raw = "\0".join(
            (
                str(self.required),
                self.access_token,
                self.token_header,
                self.token_prefix,
                self.username,
                self.password,
                self.totp_secret,
                self.login_path,
                self.mfa_verify_path,
                self.totp_field,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sanitize(message: Any, config: CasePlatformAuthConfig, *extra_values: str) -> str:
    result = _clean(message) or "用例平台认证失败"
    sensitive_values = (
        config.access_token,
        config.username,
        config.password,
        config.totp_secret,
        *extra_values,
    )
    for value in sensitive_values:
        if value:
            result = result.replace(value, "***")
    return result[:300]


def _url_origin(url: str) -> tuple[str, str, Optional[int]]:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, (parsed.hostname or "").lower(), parsed.port or default_port


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that could forward credentials to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _url_origin(req.full_url) != _url_origin(newurl):
            raise CasePlatformRequestError("用例平台拒绝跨域重定向")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class CasePlatformClient:
    """Cookie-aware AgileTC client with Token and TOTP authentication modes."""

    def __init__(
        self,
        base_url: str,
        timeout: int,
        *,
        config: Optional[CasePlatformAuthConfig] = None,
    ):
        self.base_url = _clean(base_url).rstrip("/")
        self.timeout = timeout
        self.config = config or CasePlatformAuthConfig.from_env()
        self.mode = self.config.mode
        if (
            self.mode != "anonymous"
            and _url_origin(self.base_url)[0] != "https"
        ):
            raise CasePlatformAuthError("用例平台认证凭证只允许通过 HTTPS 传输")
        self._cookie_jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookie_jar),
            SameOriginRedirectHandler(),
        )
        self._authenticated = False
        self._session_generation = 0
        self._lock = threading.RLock()

    def _headers(self, *, json_body: bool = False) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Midscene-Task-Platform/1.0",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.mode == "token":
            token_value = f"{self.config.token_prefix} {self.config.access_token}".strip()
            headers[self.config.token_header] = token_value
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        sensitive_values: tuple[str, ...] = (),
    ) -> Dict[str, Any]:
        query = urllib.parse.urlencode(
            {key: value for key, value in (params or {}).items() if value not in (None, "")},
            doseq=True,
        )
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers=self._headers(json_body=payload is not None),
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise CasePlatformAuthError("用例平台登录已失效或账号权限不足") from exc
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise CasePlatformRequestError(
                f"用例平台接口返回 HTTP {exc.code}: "
                f"{_sanitize(detail, self.config, *sensitive_values)}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise CasePlatformRequestError(f"连接用例平台失败：{exc.reason}") from exc
        except TimeoutError as exc:
            raise CasePlatformRequestError("连接用例平台超时") from exc
        try:
            result = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise CasePlatformRequestError("用例平台返回内容不是合法 JSON") from exc
        if not isinstance(result, dict):
            raise CasePlatformRequestError("用例平台返回内容不是 JSON 对象")
        return result

    def _login(self) -> None:
        result = self._request("POST", self.config.login_path, payload={
            "username": self.config.username,
            "password": self.config.password,
        })
        if int(result.get("code") or 0) != 200:
            raise CasePlatformAuthError(_sanitize(result.get("msg"), self.config))

        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if data.get("mfaRequired"):
            mfa_token = _clean(data.get("mfaToken"))
            if not mfa_token:
                raise CasePlatformAuthError("用例平台未返回动态验证码校验凭证")
            code = generate_totp(self.config.totp_secret)
            verify_result = self._request(
                "POST",
                self.config.mfa_verify_path,
                payload={
                    "mfaToken": mfa_token,
                    self.config.totp_field: code,
                },
                sensitive_values=(mfa_token, code),
            )
            if int(verify_result.get("code") or 0) != 200:
                raise CasePlatformAuthError(
                    _sanitize(verify_result.get("msg"), self.config, mfa_token, code)
                )
        self._authenticated = True
        self._session_generation += 1

    def _ensure_login(self) -> None:
        if self.mode != "totp" or self._authenticated:
            return
        with self._lock:
            if not self._authenticated:
                self._login()

    def _clear_session_locked(self) -> None:
        self._authenticated = False
        try:
            self._cookie_jar.clear()
        except KeyError:
            pass

    def _refresh_session(self, observed_generation: int) -> None:
        with self._lock:
            if self._authenticated and self._session_generation != observed_generation:
                return
            self._clear_session_locked()
            self._login()

    @staticmethod
    def _is_authority_error(result: Dict[str, Any]) -> bool:
        try:
            return int(result.get("code") or 0) == AUTHORITY_ERROR_CODE
        except (TypeError, ValueError):
            return False

    def request_json(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_login()
        observed_generation = self._session_generation
        try:
            result = self._request("GET", path, params=params)
        except CasePlatformAuthError:
            if self.mode != "totp":
                raise
            self._refresh_session(observed_generation)
            retried = self._request("GET", path, params=params)
            if self._is_authority_error(retried):
                raise CasePlatformAuthError("用例平台登录已失效或账号权限不足")
            return retried

        if not self._is_authority_error(result):
            return result
        if self.mode != "totp":
            raise CasePlatformAuthError("用例平台访问凭证无效或账号权限不足")

        self._refresh_session(observed_generation)
        retried = self._request("GET", path, params=params)
        if self._is_authority_error(retried):
            raise CasePlatformAuthError("用例平台登录已失效或账号权限不足")
        return retried


_CLIENT_CACHE_LOCK = threading.Lock()
_CLIENT_CACHE: Dict[str, CasePlatformClient] = {}


def get_case_platform_client(base_url: str, timeout: int) -> CasePlatformClient:
    config = CasePlatformAuthConfig.from_env()
    key = f"{_clean(base_url).rstrip('/')}|{timeout}|{config.cache_key()}"
    with _CLIENT_CACHE_LOCK:
        client = _CLIENT_CACHE.get(key)
        if client is None:
            client = CasePlatformClient(base_url, timeout, config=config)
            _CLIENT_CACHE.clear()
            _CLIENT_CACHE[key] = client
        return client


def reset_case_platform_client_cache() -> None:
    with _CLIENT_CACHE_LOCK:
        _CLIENT_CACHE.clear()
