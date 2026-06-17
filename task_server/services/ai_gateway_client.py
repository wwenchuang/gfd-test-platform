"""Task Server 调 AI Gateway 的统一客户端。

AI Gateway 运行在 ``http://127.0.0.1:8090``，本模块封装了对各 AI 端点的
HTTP 调用，提供：

* 健康检查
* AI 规划
* 失败分析
* YAML 优化
* 缺陷草稿生成
* 总结报告生成

所有函数均返回 ``dict``；网络异常或非 2xx 响应不会抛出，而是返回
包含 ``ok=False`` 和 ``error`` 描述的字典，便于上层路由安全响应。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# AI Gateway 基地址
# ---------------------------------------------------------------------------

AI_GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "http://127.0.0.1:8090").rstrip("/")


# ---------------------------------------------------------------------------
# 内部请求工具
# ---------------------------------------------------------------------------

def _post_json(
    path: str,
    payload: Dict[str, Any],
    timeout: float = 120,
) -> Dict[str, Any]:
    """对 AI Gateway 发起 POST 请求，返回解析后的 JSON 响应。

    Args:
        path: 路径（如 ``/api/agent/plan``）。
        payload: 请求体字典。
        timeout: 超时秒数，AI 调用默认较长。

    Returns:
        解析后的响应字典；失败时返回 ``{"ok": False, "error": ...}``。
    """
    url = AI_GATEWAY_URL + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            parsed.setdefault("ok", True)
        return parsed
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return {"ok": False, "error": f"AI Gateway HTTP {exc.code}: {body}", "http_status": exc.code}
    except Exception as exc:
        return {"ok": False, "error": f"AI Gateway 请求失败：{exc}"}


def _get_json(
    path: str,
    timeout: float = 15,
) -> Dict[str, Any]:
    """对 AI Gateway 发起 GET 请求，返回解析后的 JSON 响应。"""
    url = AI_GATEWAY_URL + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            parsed.setdefault("ok", True)
        return parsed
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return {"ok": False, "error": f"AI Gateway HTTP {exc.code}: {body}", "http_status": exc.code}
    except Exception as exc:
        return {"ok": False, "error": f"AI Gateway 请求失败：{exc}"}


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def ai_gateway_health() -> Dict[str, Any]:
    """检查 AI Gateway 健康。

    Returns:
        健康检查结果字典，至少包含 ``ok`` 和 ``url`` 字段。
    """
    started = time.time()
    result = _get_json("/health")
    result["url"] = AI_GATEWAY_URL
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    return result


def agent_plan(payload: Dict[str, Any]) -> Dict[str, Any]:
    """AI 规划。

    将测试目标 / 上下文发送给 AI Gateway，获取执行计划。

    Args:
        payload: 规划请求体，通常包含 ``target`` / ``scope`` / ``mode`` 等。

    Returns:
        AI 规划结果字典。
    """
    return _post_json("/api/agent/plan", payload, timeout=120)


def analyze_failure(payload: Dict[str, Any]) -> Dict[str, Any]:
    """AI 分析失败。

    将失败 job 的上下文发送给 AI Gateway，获取失败原因分析。

    Args:
        payload: 分析请求体，通常包含 ``jobId`` / ``stderr`` / ``yaml`` / ``report``。

    Returns:
        AI 分析结果字典，通常包含 ``failureType`` / ``summary`` / ``suggestion``。
    """
    return _post_json("/api/agent/analyze-failure", payload, timeout=120)


def optimize_yaml(payload: Dict[str, Any]) -> Dict[str, Any]:
    """AI 优化 YAML。

    将待优化的 YAML 发送给 AI Gateway，获取优化建议或优化后的 YAML。

    Args:
        payload: 优化请求体，通常包含 ``yaml`` / ``target`` / ``issues``。

    Returns:
        AI 优化结果字典，通常包含 ``optimizedYaml`` / ``changes``。
    """
    return _post_json("/api/agent/optimize-yaml", payload, timeout=120)


def generate_bug_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    """AI 生成缺陷草稿。

    将失败分析结果发送给 AI Gateway，生成飞书缺陷草稿。

    Args:
        payload: 生成请求体，通常包含 ``failureType`` / ``summary`` / ``jobId``。

    Returns:
        AI 生成的缺陷草稿字典，通常包含 ``title`` / ``description`` / ``severity``。
    """
    return _post_json("/api/agent/generate-bug-draft", payload, timeout=120)


def generate_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    """AI 生成总结。

    将运行结果汇总发送给 AI Gateway，生成总结报告。

    Args:
        payload: 生成请求体，通常包含 ``jobId`` / ``results`` / ``stats``。

    Returns:
        AI 生成的总结字典，通常包含 ``summary`` / ``highlights`` / ``recommendations``。
    """
    return _post_json("/api/agent/generate-summary", payload, timeout=120)


__all__ = [
    "AI_GATEWAY_URL",
    "ai_gateway_health",
    "agent_plan",
    "analyze_failure",
    "optimize_yaml",
    "generate_bug_draft",
    "generate_summary",
]
