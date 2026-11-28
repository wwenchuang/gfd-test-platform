"""Capture one current read of the puzzle catalog with the workbench actor's environment."""

import http.client
import re
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4


_PATH = "/app/print3d/api/v1/puzzle/presets"
_ENVIRONMENTS = {
    "正式": ("生产环境（新）-腾讯云", "print.wisebeginner3d.com"),
    "测试": ("测试环境-腾讯云", "qa-print.wisebeginner3d.com"),
}


class _Unavailable(Exception):
    """Safe operator-facing reason for an unmet prerequisite."""


def _authorized_runtime(username, environment):
    from sqlalchemy import select
    from task_server.api_testing import access
    from task_server.api_testing.db import _session_factory
    from task_server.api_testing.models.environment import ApiEnvironment
    from task_server.api_testing.models.project import ApiProject
    from task_server.api_testing.services.environment_service import EnvironmentService

    if not username or not access.get_access_profile(username):
        raise _Unavailable("工作台账号未授权")
    access.require_permission(username, "api.view")
    access.require_permission(username, "api.execute")
    if environment == "正式":
        access.require_permission(username, "api.production")
    factory = _session_factory()
    with factory() as session:
        active = session.execute(select(ApiEnvironment.active_revision_id, ApiEnvironment.project_id)
            .join(ApiProject, ApiProject.id == ApiEnvironment.project_id)
            .where(ApiProject.status == "active", ApiEnvironment.status == "active",
                   ApiEnvironment.name == _ENVIRONMENTS[environment][0],
                   ApiEnvironment.active_revision_id.is_not(None),
                   access.resource_predicate(username, ApiEnvironment))).all()
        if len(active) != 1:
            raise _Unavailable("需要工作台中唯一、已启用且当前账号可执行的同名 API 环境")
        revision, project = active[0]
        access.require_execution_environment(session, revision, username, project)
    return revision, EnvironmentService(factory).resolve_runtime(revision, {}, service_name="default")


def _public_image_url(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
            return ""
        # Object storage may attach temporary credentials to the query string.
        return parsed._replace(query="", fragment="").geturl()
    except ValueError:
        return ""


def fetch_presets(username, environment):
    """Return a sanitized receipt. An empty request_id means no GET was attempted."""
    target = _ENVIRONMENTS.get(environment) if isinstance(environment, str) else None
    host = target[1] if target else ""
    receipt = dict(ok=False, environment=environment, environment_revision_id="", method="GET",
                   host=host, path=_PATH, queried_at=datetime.now(timezone.utc).isoformat(),
                   request_id="", response_request_id="", http=None, business_code=None,
                   presets=[], error="")
    try:
        if not target:
            raise _Unavailable("环境必须为正式或测试")
        revision, runtime = _authorized_runtime(username, environment)
        receipt["environment_revision_id"] = str(revision)
        base = "https://" + host + "/app"
        if runtime.base_url_for("default").rstrip("/") != base:
            raise _Unavailable("工作台 default 服务地址与指定环境的拼图域名或路径不符")

        from task_server.api_testing.executor import HttpExecutor, ExecutorLimits
        executor = HttpExecutor(None, None, limits=ExecutorLimits(timeout_seconds=12, max_redirects=0))
        headers = {key: value for key, value in runtime.headers.items()
                   if key.lower() not in {"x-request-id", "cache-control", "pragma"}}
        request_id = str(uuid4())
        headers.update({"X-Request-ID": request_id, "Cache-Control": "no-cache", "Pragma": "no-cache"})
        receipt.update(request_id=request_id, queried_at=datetime.now(timezone.utc).isoformat())
        response = executor._request("GET", "https://" + host + _PATH, headers, None,
                                     authorized_base=base, follow_redirects=False)
        receipt["http"] = response.status_code
        upstream = next((value for key, value in response.headers.items() if key.lower() == "x-request-id"), "")
        if isinstance(upstream, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", upstream):
            receipt["response_request_id"] = upstream
        body = response.json_body if isinstance(response.json_body, dict) else {}
        code = body.get("code")
        if type(code) is int or (isinstance(code, str) and re.fullmatch(r"-?[0-9]{1,16}", code)):
            receipt["business_code"] = code
        if not 200 <= response.status_code < 300:
            raise _Unavailable("拼图预设接口返回 HTTP 错误")
        if receipt["business_code"] not in (0, "0"):
            raise _Unavailable("拼图预设接口未返回成功业务码")
        if not isinstance(body.get("data"), list):
            raise _Unavailable("拼图预设接口未返回列表")
        presets = []
        for item in body["data"]:
            name = item.get("name") if isinstance(item, dict) else None
            image_url = _public_image_url(item.get("imageUrl")) if isinstance(item, dict) else ""
            if not isinstance(name, str) or not name.strip() or not image_url:
                raise _Unavailable("拼图预设记录缺少有效的 name 或 imageUrl")
            presets.append({"name": name, "imageUrl": image_url})
        receipt.update(ok=True, presets=presets)
    except _Unavailable as exc:
        receipt["error"] = str(exc)
    except PermissionError:
        receipt["error"] = "工作台账号缺少 API 执行权限或环境授权"
    except Exception:
        # Infrastructure exceptions can include URLs, header values or DB connection strings.
        receipt["error"] = ("拼图预设接口请求失败" if receipt["request_id"] else "工作台 API 环境或凭据不可用")
    return receipt


_OBS_HOST = "micro.obs.cn-east-2.myhuaweicloud.com"
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def fetch_resource(url: str) -> bytes:
    """Fetch a single puzzle image from its public OBS directory without credentials."""
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("预设图片地址无效")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != _OBS_HOST or parsed.query or parsed.fragment:
        raise ValueError("图片地址不在拼图 OBS 范围内")
    path = unquote(parsed.path, errors="strict")
    if not re.fullmatch(r"/print3d/template/浮雕拼图/[\w ()-]{1,180}\.(?:png|jpe?g|webp)", path, re.I):
        raise ValueError("图片地址不在拼图 OBS 目录内")
    connection = http.client.HTTPSConnection(_OBS_HOST, timeout=12)
    try:
        connection.request("GET", quote(path), headers={"Accept": "image/png,image/jpeg,image/webp"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("拼图 OBS 图片返回 HTTP %d" % response.status)
        image = response.read(_MAX_IMAGE_BYTES + 1)
        if len(image) > _MAX_IMAGE_BYTES:
            raise ValueError("预设图片超过 8 MiB")
        if not (image.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))
                or (image.startswith(b"RIFF") and image[8:12] == b"WEBP")):
            raise ValueError("预设资源未返回 PNG、JPEG 或 WebP 图片")
        return image
    finally:
        connection.close()
