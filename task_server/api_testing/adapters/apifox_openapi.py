"""Official Apifox OpenAPI export HTTP adapter."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class ApifoxOpenApiError(RuntimeError):
    pass


def _redact(value, token):
    text = str(value or "")
    if token:
        text = text.replace(token, "[REDACTED]")
    return re.sub(
        r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", text
    )[:500]


def _numeric_or_text(value):
    text = str(value or "").strip()
    if not text:
        return None
    return int(text) if text.isdigit() else text


class ApifoxOpenApiAdapter:
    def __init__(self, opener=None, max_response_bytes=DEFAULT_MAX_RESPONSE_BYTES):
        self._opener = opener or urllib.request.urlopen
        self._max_response_bytes = max(1, int(max_response_bytes))

    def export(
        self,
        token,
        project_id,
        branch_id="",
        environment_id="",
        timeout=30,
    ):
        token = str(token or "").strip()
        project_id = str(project_id or "").strip()
        if not token:
            raise ApifoxOpenApiError("Apifox 访问令牌未配置")
        if not project_id:
            raise ApifoxOpenApiError("Apifox 项目 ID 未配置")
        payload = {
            "scope": {"type": "ALL"},
            "options": {
                "includeApifoxExtensionProperties": True,
                "addFoldersToTags": True,
            },
            "oasVersion": "3.0",
            "exportFormat": "JSON",
        }
        branch = _numeric_or_text(branch_id)
        environment = _numeric_or_text(environment_id)
        if branch is not None:
            payload["branchId"] = branch
        if environment is not None:
            payload["environmentIds"] = [environment]
        request = urllib.request.Request(
            "https://api.apifox.com/v1/projects/%s/export-openapi?locale=zh-CN"
            % urllib.parse.quote(project_id, safe=""),
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "midscene-task-platform/api-testing",
                "X-Apifox-Api-Version": "2024-03-28",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status < 200 or status >= 300:
                    raise ApifoxOpenApiError("Apifox HTTP %d" % status)
                raw = response.read(self._max_response_bytes + 1)
        except ApifoxOpenApiError:
            raise
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise ApifoxOpenApiError("Apifox 访问令牌无效或已过期") from None
            if error.code == 403:
                raise ApifoxOpenApiError("当前令牌无权导出该 Apifox 项目") from None
            raise ApifoxOpenApiError("Apifox HTTP %d" % error.code) from None
        except Exception as error:
            raise ApifoxOpenApiError(
                "Apifox OpenAPI 请求失败：%s" % _redact(error, token)
            ) from None
        if len(raw) > self._max_response_bytes:
            raise ApifoxOpenApiError("Apifox OpenAPI 响应超过 20 MiB 上限")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise ApifoxOpenApiError(
                "Apifox OpenAPI JSON 解析失败：%s" % _redact(error, token)
            ) from None
        if not isinstance(document, dict):
            raise ApifoxOpenApiError("Apifox OpenAPI JSON 必须是对象")
        if not isinstance(document.get("paths"), dict) or not document["paths"]:
            raise ApifoxOpenApiError("Apifox OpenAPI paths 为空")
        return document
