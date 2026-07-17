"""AI Skill framework service.

从 midscene-upload.py 全量迁移的 AI 技能框架，提供：

* AI skill prompt / schema 的加载、渲染
* JSON Schema 最小校验
* DashScope 通用对话调用
* 各 AI skill 调用函数（requirement_analyzer, scenario_designer,
  automation_filter, visual_grounder, coverage_auditor）
* 用例生成相关辅助函数（normalize, coverage, audit 等）
* DashScope 用例生成 / 精修函数（legacy + skill pipeline）
"""

from __future__ import annotations

import base64
import copy
import concurrent.futures
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from task_server.config import (
    AI_CHAT_RETRY_COUNT,
    AI_CHAT_TIMEOUT_SECONDS,
    AI_GATEWAY_URL,
    AI_COVERAGE_AUDITOR_TIMEOUT_SECONDS,
    AI_COVERAGE_MODEL_WHEN_LOCAL_OK,
    AI_COVERAGE_REPAIR_TIMEOUT_SECONDS,
    AI_COVERAGE_TOTAL_BUDGET_SECONDS,
    AI_SKILLS_DIR,
    AI_VISION_IMAGE_LIMIT,
    DEFAULT_APP_PACKAGE,
    TASK_DIR,
    dashscope_api_key,
    dashscope_base_url,
    dashscope_model_for_images,
    dashscope_text_model,
    dashscope_vl_model,
    safe_bool,
    safe_int,
)
from task_server.storage import (
    clean_asset_filename,
    clean_id,
    read_json_file,
    read_text_file,
    safe_join,
)
from task_server.services.yaml_service import (
    normalize_cases_payload,
    normalize_model_json,
    normalize_text_list,
    first_non_empty,
    case_value,
    case_priority,
    case_tags,
    is_smoke_case,
    audit_case_coverage,
    strip_yaml_quotes,
    evidence_needs_adb_input_fallback,
    validate_midscene_yaml,
    yaml_task_names,
    find_yaml_task_block,
    normalize_full_yaml_structure,
    normalize_yaml_from_model,
    normalize_yaml_task_block_from_model,
    baseline_branch_anchor_terms,
    diff_yaml,
    _case_has_deep_external_action,
    _case_is_bounded_external_landing_check,
)
from task_server.services.knowledge_service import (
    repair_knowledge_context,
    task_business_context,
    load_knowledge_context,
)
from task_server.services.report_service import (
    report_image_context,
    report_text_context,
)

AI_SMOKE_SELECTOR_ENABLED = safe_bool(os.getenv("MIDSCENE_AI_SMOKE_SELECTOR_ENABLED", "1"), True)
AI_SMOKE_SELECTOR_TIMEOUT_SECONDS = max(20, safe_int(os.getenv("MIDSCENE_AI_SMOKE_SELECTOR_TIMEOUT_SECONDS", "45"), 45))
AI_BASELINE_RERANKER_TIMEOUT_SECONDS = max(20, safe_int(os.getenv("MIDSCENE_AI_BASELINE_RERANKER_TIMEOUT_SECONDS", "45"), 45))
AI_EXECUTION_SCOPE_PLANNER_TIMEOUT_SECONDS = max(20, safe_int(os.getenv("MIDSCENE_AI_EXECUTION_SCOPE_PLANNER_TIMEOUT_SECONDS", "45"), 45))
AI_EXECUTABLE_YAML_PLANNER_TIMEOUT_SECONDS = max(30, safe_int(os.getenv("MIDSCENE_AI_EXECUTABLE_YAML_PLANNER_TIMEOUT_SECONDS", "75"), 75))
AI_EXECUTABLE_YAML_EVIDENCE_CONVERGENCE_TIMEOUT_SECONDS = max(
    30,
    min(
        AI_EXECUTABLE_YAML_PLANNER_TIMEOUT_SECONDS,
        safe_int(os.getenv("MIDSCENE_AI_EXECUTABLE_YAML_EVIDENCE_CONVERGENCE_TIMEOUT_SECONDS", "60"), 60),
    ),
)
AI_SKILLS_STRICT_MODEL = safe_bool(os.getenv("MIDSCENE_AI_SKILLS_STRICT_MODEL", "0"), False)
AI_GATEWAY_VISION_FALLBACK_PROVIDER_ID = str(
    os.getenv("MIDSCENE_AI_GATEWAY_VISION_FALLBACK_PROVIDER_ID", "qwen_plus")
).strip() or "qwen_plus"
AI_SKILL_JSON_REPAIR_TIMEOUT_SECONDS = max(
    30,
    min(60, safe_int(os.getenv("MIDSCENE_AI_SKILL_JSON_REPAIR_TIMEOUT_SECONDS", "45"), 45)),
)
AI_SKILL_JSON_REPAIR_MAX_CHARS = max(
    8000,
    min(60000, safe_int(os.getenv("MIDSCENE_AI_SKILL_JSON_REPAIR_MAX_CHARS", "30000"), 30000)),
)


# ---------------------------------------------------------------------------
# AI skill path & prompt / schema loading
# ---------------------------------------------------------------------------

def ai_skill_path(*parts):
    """构建 AI skill 目录下的安全路径。"""
    return safe_join(AI_SKILLS_DIR, *parts)


def load_ai_skill_prompt(skill_name, version="v1"):
    """加载 AI skill prompt 文件。"""
    name = clean_id(skill_name, "skill")
    ver = clean_id(version, "v1")
    path = ai_skill_path("prompts", f"{name}.{ver}.md")
    return read_text_file(path, "")


def load_ai_skill_schema(skill_name):
    """加载 AI skill JSON Schema。"""
    name = clean_id(skill_name, "skill")
    path = ai_skill_path("schemas", f"{name}.schema.json")
    schema = read_json_file(path, default=None)
    if not schema:
        raise ValueError(f"AI skill schema 不存在：{name}")
    return schema


# ---------------------------------------------------------------------------
# JSON Schema minimal validation
# ---------------------------------------------------------------------------

def validate_json_schema_minimal(value, schema, path="$"):
    """最小化 JSON Schema 校验。"""
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} 必须是 object")
        for key in schema.get("required") or []:
            if key not in value:
                raise ValueError(f"{path}.{key} 为必填字段")
        properties = schema.get("properties") or {}
        for key, child_schema in properties.items():
            if key in value:
                validate_json_schema_minimal(value[key], child_schema, f"{path}.{key}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} 必须是 array")
    elif expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} 必须是 string")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path} 必须是 boolean")
    elif expected in ("number", "integer"):
        if not isinstance(value, (int, float)):
            raise ValueError(f"{path} 必须是 number")
    return True


def validate_ai_skill_output(skill_name, value):
    """校验 AI skill 输出是否符合对应 JSON Schema。"""
    schema = load_ai_skill_schema(skill_name)
    validate_json_schema_minimal(value, schema)
    return value


# ---------------------------------------------------------------------------
# AI skill prompt rendering & execution
# ---------------------------------------------------------------------------

def _run_ai_skill_call_with_hard_timeout(func, timeout, label):
    timeout_seconds = max(30, safe_int(timeout, 180))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"{label} 超过 {timeout_seconds}s 未返回，已中断并交给本地兜底") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def render_ai_skill_prompt(skill_name, payload=None, version="v1", fallback_prompt=""):
    """渲染 AI skill prompt 模板。"""
    template = load_ai_skill_prompt(skill_name, version)
    if not template:
        return fallback_prompt
    payload_text = json.dumps(payload or {}, ensure_ascii=False, indent=2)
    return template.replace("{{payload}}", payload_text)


def _merge_missing_output_defaults(value, defaults):
    """Fill required context omitted by a model without replacing model output."""
    if not isinstance(value, dict) or not isinstance(defaults, dict):
        return value
    merged = copy.deepcopy(value)
    for key, default_value in defaults.items():
        if key not in merged or merged.get(key) is None:
            merged[key] = copy.deepcopy(default_value)
        elif isinstance(merged.get(key), dict) and isinstance(default_value, dict):
            merged[key] = _merge_missing_output_defaults(merged[key], default_value)
    return merged


def _repair_ai_skill_json_output(skill_name, raw, parse_error, model_config, runtime_trace, max_tokens=None):
    """Ask the selected model once to repair JSON syntax without regenerating business content."""
    raw_text = str(raw or "")
    parse_error_text = f"{type(parse_error).__name__}: {parse_error}"
    repair_meta = {
        "inputChars": len(raw_text),
        "maxInputChars": AI_SKILL_JSON_REPAIR_MAX_CHARS,
        "timeoutSeconds": AI_SKILL_JSON_REPAIR_TIMEOUT_SECONDS,
        "parseError": parse_error_text[:500],
        "sameSelectedModel": True,
    }
    runtime_trace["jsonRepairAttempted"] = False
    runtime_trace["jsonRepairSucceeded"] = False
    runtime_trace["jsonRepair"] = repair_meta
    if len(raw_text) > AI_SKILL_JSON_REPAIR_MAX_CHARS:
        repair_meta["skippedReason"] = "malformed_output_exceeds_repair_limit"
        raise RuntimeError(
            f"AI skill {skill_name} 返回 JSON 语法错误，且输出 {len(raw_text)} 字符超过 "
            f"{AI_SKILL_JSON_REPAIR_MAX_CHARS} 字符修复上限"
        ) from parse_error

    schema = load_ai_skill_schema(skill_name)
    repair_prompt = (
        "你是严格的 JSON 语法修复器。只修复下方模型输出中的 JSON 语法错误，"
        "不得新增、删除、改写、概括或重新排序任何业务字段、数组元素或字符串值。\n"
        "要求：\n"
        "1. 输出且只输出一个合法 JSON 对象，不要 Markdown 代码块或解释。\n"
        "2. JSON 必须满足给定 schema；schema 只约束结构，不能作为补造业务内容的依据。\n"
        "3. 如果字符串中包含引号、换行或反斜杠，只做 JSON 所需转义。\n\n"
        f"skill: {skill_name}\n"
        f"parse_error: {parse_error_text}\n"
        f"schema: {json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}\n"
        "malformed_json:\n"
        f"{raw_text}"
    )
    repair_trace = {}
    runtime_trace["jsonRepairAttempted"] = True
    try:
        repaired_raw = _run_ai_skill_call_with_hard_timeout(
            lambda: ai_gateway_skill_content(
                skill_name,
                repair_prompt,
                payload={"jsonRepair": True, "sourceSkill": skill_name},
                timeout=AI_SKILL_JSON_REPAIR_TIMEOUT_SECONDS,
                temperature=0,
                json_response=True,
                model_config=model_config,
                image_assets=None,
                runtime_trace=repair_trace,
                max_tokens=max_tokens,
            ),
            AI_SKILL_JSON_REPAIR_TIMEOUT_SECONDS,
            f"AI Gateway JSON repair {skill_name}",
        )
        result = normalize_model_json(repaired_raw)
    except Exception as exc:
        repair_meta["modelTrace"] = copy.deepcopy(repair_trace)
        repair_meta["error"] = str(exc)[:500]
        raise RuntimeError(
            f"AI skill {skill_name} JSON 语法修复失败：原始错误={parse_error_text}；"
            f"修复错误={type(exc).__name__}: {exc}"
        ) from exc
    repair_meta["modelTrace"] = copy.deepcopy(repair_trace)
    repair_meta["syntaxValid"] = True
    return result


def run_ai_skill(
    skill_name,
    payload=None,
    image_assets=None,
    version="v1",
    temperature=0.1,
    timeout=180,
    fallback_prompt="",
    respect_global_timeout=True,
    retry_count=None,
    model_config=None,
    output_defaults=None,
    max_tokens=None,
    runtime_trace=None,
    repair_invalid_json=False,
):
    """执行 AI skill：优先走统一 Gateway，平台直连仅用于无显式模型的最终兼容兜底。"""
    prompt = render_ai_skill_prompt(skill_name, payload, version=version, fallback_prompt=fallback_prompt)
    if not prompt:
        raise ValueError(f"AI skill prompt 不存在：{skill_name}.{version}")
    runtime_trace = runtime_trace if isinstance(runtime_trace, dict) else {}
    gateway_enabled = safe_bool(os.getenv("MIDSCENE_AI_SKILLS_USE_GATEWAY", "1"), True)
    gateway_error = ""
    if gateway_enabled:
        try:
            raw = _run_ai_skill_call_with_hard_timeout(
                lambda: ai_gateway_skill_content(
                    skill_name,
                    prompt,
                    payload=payload,
                    timeout=timeout,
                    temperature=temperature,
                    json_response=True,
                    model_config=model_config,
                    image_assets=image_assets,
                    runtime_trace=runtime_trace,
                    max_tokens=max_tokens,
                ),
                timeout,
                f"AI Gateway skill {skill_name}",
            )
            json_repaired = False
            try:
                result = normalize_model_json(raw)
            except json.JSONDecodeError as exc:
                if not repair_invalid_json:
                    raise
                result = _repair_ai_skill_json_output(
                    skill_name,
                    raw,
                    exc,
                    model_config if isinstance(model_config, dict) else {},
                    runtime_trace,
                    max_tokens=max_tokens,
                )
                json_repaired = True
            result = _merge_missing_output_defaults(result, output_defaults)
            try:
                validated = validate_ai_skill_output(skill_name, result)
            except Exception as exc:
                if json_repaired:
                    runtime_trace["jsonRepairSucceeded"] = False
                    repair_meta = runtime_trace.get("jsonRepair")
                    if isinstance(repair_meta, dict):
                        repair_meta["error"] = f"schema_validation: {exc}"[:500]
                raise
            if json_repaired:
                runtime_trace["jsonRepairSucceeded"] = True
                repair_meta = runtime_trace.get("jsonRepair")
                if isinstance(repair_meta, dict):
                    repair_meta["succeeded"] = True
            return validated
        except TimeoutError:
            runtime_trace.update({"source": "ai_gateway", "error": f"AI Gateway skill {skill_name} timeout"})
            raise
        except Exception as exc:
            gateway_error = str(exc)
            runtime_trace.update({"source": "ai_gateway", "error": gateway_error[:500]})
            if model_config or AI_SKILLS_STRICT_MODEL:
                raise RuntimeError(
                    f"AI Gateway skill 调用失败；显式模型只允许由 Gateway 按可用性/能力策略回退：{exc}"
                ) from exc
    raw = dashscope_chat_content(
        prompt,
        image_assets=image_assets,
        temperature=temperature,
        timeout=timeout,
        json_response=True,
        respect_global_timeout=respect_global_timeout,
        retry_count=retry_count,
        max_tokens=max_tokens,
    )
    runtime_trace.update({
        "selectedProviderId": (model_config or {}).get("providerId") if isinstance(model_config, dict) else "",
        "selectedModel": (model_config or {}).get("model") if isinstance(model_config, dict) else "",
        "providerId": "dashscope_direct",
        "model": dashscope_model_for_images(image_assets),
        "fallbackUsed": bool(gateway_enabled),
        "fallbackReason": gateway_error[:500],
        "source": "dashscope_direct",
    })
    result = normalize_model_json(raw)
    result = _merge_missing_output_defaults(result, output_defaults)
    return validate_ai_skill_output(skill_name, result)


def _decode_ai_gateway_json_response(raw, status=200, content_type="", endpoint="/ai/skill"):
    """Decode a Gateway response without collapsing empty/HTML bodies into JSON errors."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw or "")
    normalized_type = str(content_type or "").strip() or "unknown"
    if not text.strip():
        raise RuntimeError(
            f"AI Gateway 返回空响应：endpoint={endpoint} status={status} content-type={normalized_type}"
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = re.sub(r"\s+", " ", text).strip()[:240]
        raise RuntimeError(
            f"AI Gateway 返回非 JSON 响应：endpoint={endpoint} status={status} "
            f"content-type={normalized_type} body={preview!r}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(
            f"AI Gateway 返回了非对象 JSON：endpoint={endpoint} status={status} "
            f"content-type={normalized_type} type={type(data).__name__}"
        )
    return data


def _ai_gateway_response_content_type(response):
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    try:
        return str(headers.get("Content-Type") or "")
    except Exception:
        return ""


def ai_gateway_skill_content(
    skill_name,
    prompt,
    payload=None,
    timeout=180,
    temperature=0.1,
    json_response=True,
    model_config=None,
    image_assets=None,
    runtime_trace=None,
    max_tokens=None,
):
    """Call the unified Gateway for text or image skills and expose the actual routed model."""
    model_config = model_config if isinstance(model_config, dict) else {}
    runtime_trace = runtime_trace if isinstance(runtime_trace, dict) else {}
    normalized_images = []
    for index, asset in enumerate((image_assets or [])[:AI_VISION_IMAGE_LIMIT], start=1):
        if not isinstance(asset, dict):
            continue
        base64_text = str(asset.get("base64") or asset.get("contentBase64") or "").strip()
        data_url = str(asset.get("dataUrl") or "").strip()
        if not base64_text and not data_url:
            continue
        normalized_images.append({
            "name": str(asset.get("name") or asset.get("fileName") or f"image-{index}"),
            "mime": str(asset.get("mime") or asset.get("contentType") or "image/png"),
            "base64": base64_text,
            "dataUrl": data_url,
        })
    fallback_model_config = {}
    if normalized_images:
        fallback_model_config = {
            "providerId": AI_GATEWAY_VISION_FALLBACK_PROVIDER_ID,
            "model": dashscope_vl_model(),
        }
    request_body = {
        "skillName": skill_name,
        "prompt": prompt,
        "payload": payload or {},
        "temperature": temperature,
        "jsonResponse": json_response,
        "modelConfig": model_config,
        "providerId": model_config.get("providerId") or model_config.get("provider") or "",
        "model": model_config.get("model") or model_config.get("modelName") or "",
        "imageAssets": normalized_images,
        "fallbackModelConfig": fallback_model_config,
        "timeoutMs": max(5000, (safe_int(timeout, 180) - 2) * 1000),
    }
    requested_max_tokens = safe_int(max_tokens, 0)
    if requested_max_tokens > 0:
        request_body["maxTokens"] = requested_max_tokens
    body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{AI_GATEWAY_URL}/ai/skill",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    endpoint = "/ai/skill"
    try:
        with urllib.request.urlopen(req, timeout=max(30, safe_int(timeout, 180))) as resp:
            data = _decode_ai_gateway_json_response(
                resp.read(),
                status=getattr(resp, "status", 200),
                content_type=_ai_gateway_response_content_type(resp),
                endpoint=endpoint,
            )
    except urllib.error.HTTPError as exc:
        try:
            error_data = _decode_ai_gateway_json_response(
                exc.read(),
                status=exc.code,
                content_type=_ai_gateway_response_content_type(exc),
                endpoint=endpoint,
            )
            error_text = str(error_data.get("error") or error_data.get("message") or "AI Gateway skill 调用失败")
        except RuntimeError as decode_exc:
            error_text = str(decode_exc)
        raise RuntimeError(f"AI Gateway HTTP {exc.code}：{error_text}") from exc
    selected_provider_id = model_config.get("providerId") or model_config.get("provider") or ""
    selected_model = model_config.get("model") or model_config.get("modelName") or ""
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    runtime_trace.update({
        "selectedProviderId": selected_provider_id,
        "selectedModel": selected_model,
        "providerId": data.get("providerId") or selected_provider_id,
        "model": data.get("model") or selected_model,
        "fallbackUsed": bool(data.get("fallbackUsed")),
        "fallbackIndex": safe_int(data.get("fallbackIndex"), 0),
        "fallbackReason": str(data.get("fallbackReason") or "")[:500],
        "source": "ai_gateway",
        "imageCount": len(normalized_images),
        "finishReason": str(data.get("finishReason") or ""),
        "usage": usage,
    })
    if not data.get("success"):
        raise RuntimeError(str(data.get("error") or "AI Gateway skill 调用失败"))
    content = data.get("content")
    if content is None:
        content = data.get("data")
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False)
    else:
        content = str(content or "")
    if not content.strip():
        raise RuntimeError(
            "AI Gateway skill 返回空内容："
            f"provider={data.get('providerId') or selected_provider_id} "
            f"model={data.get('model') or selected_model} "
            f"finish_reason={data.get('finishReason') or 'unknown'} "
            f"completion_tokens={safe_int(usage.get('completionTokens'), 0)} "
            f"reasoning_tokens={safe_int(usage.get('reasoningTokens'), 0)}"
        )
    return content


# ---------------------------------------------------------------------------
# DashScope chat API
# ---------------------------------------------------------------------------

def build_dashscope_chat_body(
    prompt,
    image_assets=None,
    temperature=0.1,
    json_response=True,
    image_limit=None,
    max_tokens=None,
):
    """构建 DashScope Chat API 请求体。"""
    image_assets = image_assets or []
    image_limit = max(1, int(image_limit or AI_VISION_IMAGE_LIMIT))
    model = dashscope_model_for_images(image_assets)
    if image_assets:
        user_content = [{"type": "text", "text": prompt}]
        for asset in image_assets[:image_limit]:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{asset['mime']};base64,{asset['base64']}"
                }
            })
    else:
        user_content = prompt
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON。"},
            {"role": "user", "content": user_content}
        ],
        "temperature": temperature
    }
    if json_response:
        body["response_format"] = {"type": "json_object"}
        if re.match(r"^qwen3\.(?:5|6|7)(?:-|$)", str(model or "").strip().lower()):
            # Qwen 3.5/3.6/3.7 defaults to thinking mode, while DashScope JSON
            # Mode requires non-thinking output. Structured skills are still
            # evaluated by their schema and the platform quality gates.
            body["enable_thinking"] = False
    if safe_int(max_tokens, 0) > 0:
        body["max_tokens"] = safe_int(max_tokens, 0)
    return body


def dashscope_chat_content(
    prompt,
    image_assets=None,
    temperature=0.1,
    timeout=180,
    json_response=True,
    image_limit=None,
    respect_global_timeout=True,
    retry_count=None,
    max_tokens=None,
):
    """调用 DashScope Chat API 并返回 content 字符串。"""
    api_key = dashscope_api_key()
    base_url = dashscope_base_url()
    model = dashscope_model_for_images(image_assets)
    timeout = safe_int(timeout, 180)
    if respect_global_timeout:
        timeout = max(timeout, AI_CHAT_TIMEOUT_SECONDS)
    else:
        timeout = max(30, timeout)
    retries = AI_CHAT_RETRY_COUNT if retry_count is None else max(0, safe_int(retry_count, 0))
    body = json.dumps(build_dashscope_chat_body(
        prompt,
        image_assets=image_assets,
        temperature=temperature,
        json_response=json_response,
        image_limit=image_limit,
        max_tokens=max_tokens,
    ), ensure_ascii=False).encode("utf-8")
    last_error = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
            return resp_data["choices"][0]["message"]["content"]
        except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
            last_error = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise TimeoutError(
                f"千问模型响应超时：{model} 在 {timeout}s 内未返回，已重试 {retries} 次；"
                "建议减少本次上传的大图/长文档，补充关键截图即可，或稍后重新生成"
            ) from e
        except Exception:
            raise
    raise last_error


# ---------------------------------------------------------------------------
# Utility helpers (migrated from midscene-upload.py)
# ---------------------------------------------------------------------------

def normalize_lines(value):
    """规范化行列表。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]
    return []


def is_image_file(filename):
    """判断文件名是否为图片格式。"""
    return filename.lower().endswith((".png", ".jpg", ".jpeg"))


def guess_mime(filename):
    """根据文件名猜测 MIME 类型。"""
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith(".doc"):
        return "application/msword"
    if lower.endswith(".mm"):
        return "application/x-freemind"
    return "text/plain"


def normalize_case_json_from_model(text):
    """从模型输出中解析用例 JSON。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    parse_error = None
    try:
        payload = json.loads(text)
        return normalize_cases_payload(payload)
    except Exception as exc:
        parse_error = exc
    starts = [(pos, char) for char, pos in (("{", text.find("{")), ("[", text.find("["))) if pos >= 0]
    if not starts:
        raise ValueError(f"模型未返回可解析 JSON：{parse_error}")
    start, opener = min(starts, key=lambda item: item[0])
    closer = "}" if opener == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise ValueError("模型返回的 JSON 不完整")
    payload = json.loads(text[start:end + 1])
    return normalize_cases_payload(payload)


def compact_text_assets(text_assets, max_chars=24000):
    """将文本资产合并为单段文本并截断。"""
    text = "\n\n".join(str(item or "").strip() for item in (text_assets or []) if str(item or "").strip())
    return text[:max_chars]


def extract_yaml_reference_context(text_assets, max_chars=14000):
    """从输入资料中提取平台 YAML 写法参考，传递给后续 AI skill。"""
    chunks = []
    for item in text_assets or []:
        text = str(item or "").strip()
        if not text:
            continue
        if "【现有 YAML 步骤经验库】" in text or "```yaml" in text or "YAML 步骤经验" in text:
            chunks.append(text)
    if not chunks:
        return ""
    return "\n\n".join(chunks)[:max_chars]


# ---------------------------------------------------------------------------
# Failure analysis helpers (migrated from midscene-upload.py)
# ---------------------------------------------------------------------------

def runtime_toast_error_from_text(text=""):
    """从文本中检测运行时 toast/错误浮层信号。"""
    raw = str(text or "")
    lower = raw.lower()
    patterns = (
        ("mapper function returned a null value", "The mapper function returned a null value."),
        ("returned a null value", "returned a null value"),
        ("null value", "null value"),
        ("nullpointerexception", "NullPointerException"),
        ("空指针", "空指针"),
        ("系统异常", "系统异常"),
        ("服务异常", "服务异常"),
        ("发生错误", "发生错误"),
        ("操作失败", "操作失败"),
    )
    for needle, label in patterns:
        if needle in lower or needle in raw:
            return label
    return ""


def evidence_is_toast_assertion_issue(text=""):
    """判断日志是否为 toast 断言问题。"""
    text = str(text or "")
    toast_words = ("toast", "提示", "成功", "已保存", "完成", "没看到", "没有看到", "未找到", "未出现", "无法找到")
    action_words = ("保存", "下载", "导出", "转换", "生成", "写入", "相册", "完成", "成功")
    return any(word.lower() in text.lower() for word in toast_words) and any(word in text for word in action_words)


def review_ui_terms(text):
    """提取复检文本中引用的 UI 术语。"""
    raw = str(text or "")
    terms = []
    _open = "\u300c\u201c\"\u0027"
    _close = "\u300d\u201d\"\u0027"
    _pattern = "[" + _open + "]([^" + _close + "]{1,40})[" + _close + "]"
    for item in re.findall(_pattern, raw):
        item = item.strip()
        if item and item not in terms:
            terms.append(item)
    ui_words = (
        "\u786e\u8ba4\u6253\u5370", "\u7ee7\u7eed\u6253\u5370", "\u53bb\u7f16\u8f91", "\u53bb\u6253\u5370", "\u4e0b\u4e00\u6b65", "\u8fd4\u56de", "\u53d6\u6d88\u6253\u5370",
        "\u7acb\u5373\u6253\u5370", "\u67e5\u770b\u5168\u90e8", "\u641c\u7d22", "\u4fdd\u5b58\u6210\u529f", "\u4fdd\u5b58\u5230\u76f8\u518c", "\u5bfc\u51fa", "\u5b8c\u6210",
        "\u8ff7\u4f60\u4fdd\u9f84\u7403\u5957\u88c5", "\u4fdd\u9f84\u7403", "\u8bd5\u5377\u5939"
    )
    for word in ui_words:
        if word in raw and word not in terms:
            terms.append(word)
    return terms[:12]


def detect_wait_strategy_issue(yaml_text, log_text):
    """检测等待策略过短的失败问题。"""
    log_lower = str(log_text or "").lower()
    hard_non_script_signals = (
        "http error", "request entity too large", "502", "503", "504", "model configuration",
        "adb: device", "device offline", "no devices", "应用崩溃", "闪退", "exception",
        "服务器异常", "网络异常", "接口异常", "系统错误", "产品缺陷"
    )
    if any(signal.lower() in log_lower for signal in hard_non_script_signals):
        return None
    loading_failure_signals = (
        "timeout", "timed out", "超时", "卡在", "加载中", "按钮持续不可点击",
        "不可点击", "未出现", "没有出现", "failed to locate", "task failed"
    )
    slow_business_signals = (
        "进度", "35%", "100%", "100.0%", "确认打印", "取消打印", "下一步",
        "模型处理", "切片", "上传", "导入", "生成"
    )
    if not any(signal.lower() in log_lower for signal in loading_failure_signals):
        return None
    if not any(signal.lower() in log_lower for signal in slow_business_signals):
        return None
    short_waits = []
    lines = (yaml_text or "").splitlines()
    for idx, line in enumerate(lines):
        m = re.match(r"^\s*-\s+aiWaitFor\s*:\s*(.+?)\s*$", line)
        if not m:
            continue
        condition = strip_yaml_quotes(m.group(1))
        timeout = 0
        j = idx + 1
        while j < len(lines):
            child = lines[j]
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            tm = re.match(r"^\s*timeout\s*:\s*(\d+)\s*$", child)
            if tm:
                timeout = safe_int(tm.group(1), 0)
                break
            j += 1
        next_key, next_text = "", ""
        for look_line in lines[j:j + 4]:
            nm = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.+?)\s*$", look_line)
            if nm:
                next_key, next_text = nm.group(1), strip_yaml_quotes(nm.group(2))
                break
        context = "\n".join(lines[max(0, idx - 1):min(len(lines), idx + 5)])
        timeout_context = context
        if next_key in ("aiTap", "ai", "aiAction", "aiAct") and next_text:
            timeout_context = "\n".join([condition, next_text])
        from task_server.services.yaml_service import loading_wait_timeout_for_context
        desired = loading_wait_timeout_for_context(timeout_context)
        if desired >= 60000 and (not timeout or timeout < desired):
            short_waits.append(f"{condition} timeout={timeout or '未设置'}，建议 {desired}ms")
    if short_waits:
        source_text = "\n".join([str(yaml_text or ""), str(log_text or "")])
        wait_targets = []
        for word in ("进度条", "目标按钮", "下一步", "去打印", "确认打印", "取消打印", "返回"):
            if word == "目标按钮" or word in source_text:
                wait_targets.append(word)
        wait_target_text = " / ".join(wait_targets[:4]) or "目标 UI"
        return {
            "category": "script_issue",
            "confidence": 0.82,
            "reason": "失败更像业务加载等待策略过短，可先做一次脚本等待修复；若重跑仍在长等待后失败，应保留为产品/环境问题复核",
            "evidence": short_waits[:5],
            "suggested_action": f"将本次脚本真实涉及的慢加载节点（{wait_target_text}）改为 aiWaitFor + 合理 timeout，只重跑验证一次；仍失败则不要继续放宽脚本",
            "can_auto_repair": True
        }
    return None


def detect_horizontal_scroll_script_issue(yaml_text, log_text):
    """Detect a missing or ineffective horizontal reveal before declaring a product bug."""
    text = str(yaml_text or "")
    log = str(log_text or "")
    combined = "\n".join([text, log])
    has_horizontal_scroll = (
        "aiScroll" in text
        and any(word in text for word in ("横向", "icon", "图标", "我的学习", "功能", "列表"))
    )
    missing_target = any(word in log for word in (
        "未出现", "没有出现", "没有发现", "找不到", "未找到", "不可见",
        "failed to locate", "not found", "看不到",
    ))
    target_is_icon = any(word in combined for word in ("试卷夹", "入口", "icon", "图标"))
    clipped_row_evidence = bool(
        any(word in log for word in ("右侧", "左侧", "屏幕边缘", "可见区域", "当前界面区域"))
        and any(word in log for word in ("被截断", "截断", "只显示", "仅显示", "露出一部分", "部分可见"))
        and any(word in combined for word in ("入口", "icon", "图标", "列表", "同级", "导入"))
    )
    if missing_target and target_is_icon and (has_horizontal_scroll or clipped_row_evidence):
        missing_scroll = clipped_row_evidence and not has_horizontal_scroll
        return {
            "category": "script_issue",
            "confidence": 0.93 if missing_scroll else 0.94,
            "reason": (
                "失败关键帧显示同级入口行在屏幕边缘被裁切，但 YAML 在断言前没有横向探索；"
                "目标可能仍在同一列表的屏外区域，应先做一次有界可见文字滑动修复，再判断是否为产品缺陷"
                if missing_scroll else
                "失败点前存在横向 icon 列表 aiScroll，但目标入口仍未出现，结合当前截图更像横向滑动未真正执行或滑动距离/方式不正确，不应判为产品缺陷"
            ),
            "evidence": [
                (
                    "报告文字明确描述同级入口行在屏幕边缘被截断，原 YAML 未包含横向 aiScroll"
                    if missing_scroll else "YAML 中存在横向 icon 列表 aiScroll"
                ),
                "执行日志显示目标入口未出现或定位失败",
                "当前页面只完整显示列表前部入口，符合目标仍在屏外的可恢复脚本分叉"
            ],
            "suggested_action": (
                "让修复 AI 根据报告关键帧，在失败等待前为具体同级入口区域补充一次或两次官方 aiScroll，"
                "使用可见文字描述区域并在滑动后重新等待目标；禁止坐标或 ADB swipe"
            ),
            "can_auto_repair": True
        }
    return None


def sanitize_failure_review_against_sources(review, yaml_text="", stdout="", stderr="", summary=None, ctx=None):
    """校验失败复检结论是否引用了源文本中不存在的 UI 术语。"""
    if not isinstance(review, dict):
        return review
    ctx = ctx or {}
    source_text = "\n".join([
        str(yaml_text or ""),
        str(stdout or ""),
        str(stderr or ""),
        json.dumps(summary, ensure_ascii=False) if summary is not None else "",
        str(ctx.get("report_text") or ""),
    ])
    review_text = "\n".join([
        str(review.get("reason") or ""),
        str(review.get("suggested_action") or ""),
        "\n".join([str(item) for item in (review.get("evidence") or [])]),
    ])
    unseen_terms = []
    for term in review_ui_terms(review_text):
        if term and term not in source_text and term not in unseen_terms:
            unseen_terms.append(term)
    if not unseen_terms:
        return review
    sanitized = dict(review)
    sanitized["category"] = "unknown"
    sanitized["failure_type"] = "review_source_mismatch"
    sanitized["confidence"] = min(float(sanitized.get("confidence") or 0), 0.45)
    sanitized["reason"] = (
        "失败复检引用了当前 YAML、执行日志或报告文本中不存在的控件/步骤："
        + "、".join(unseen_terms[:5])
        + "。已降级为不确定，避免把旧脚本、串用报告或模型臆测当成真实失败原因。"
    )
    sanitized["evidence"] = [
        "当前 YAML/本次日志未出现：" + "、".join(unseen_terms[:5]),
        "请优先确认 Sonic 是否执行了最新同步的 YAML，以及 Midscene 原始报告中的真实失败步骤。"
    ]
    sanitized["suggested_action"] = "不要自动修改业务链路；先核对 Sonic 用例模板和当前 YAML 是否一致，再按原始报告失败步骤处理。"
    sanitized["can_auto_repair"] = False
    return sanitized


def extract_failure_brief(stdout="", stderr="", summary=None):
    """从执行输出中提取失败摘要。"""
    text = "\n".join([stdout or "", stderr or ""])
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    signal_patterns = (
        "error:", "Task failed:", "Assertion failed", "failed to locate", "Failed to continue",
        "unknown flowItem", "Model configuration", "No such file", "timeout", "Timed out",
        "Replanned", "exceeding the limit", "I can see", "Reason:", "toast",
        "mapper function returned", "returned a null value", "null value", "系统异常", "操作失败"
    )
    signals = []
    for idx, line in enumerate(lines):
        if any(pattern.lower() in line.lower() for pattern in signal_patterns):
            start = max(0, idx - 1)
            end = min(len(lines), idx + 3)
            for item in lines[start:end]:
                if item not in signals:
                    signals.append(item)
        if len(signals) >= 12:
            break

    failed_tasks = []
    for line in lines:
        m = re.search(r"[✘x]\s+(.+?)\s+\(task\s+\d+/\d+\)", line)
        if m and m.group(1).strip() not in failed_tasks:
            failed_tasks.append(m.group(1).strip())
    if isinstance(summary, dict):
        for key in ("failed", "failedTasks", "errors"):
            value = summary.get(key)
            if isinstance(value, list):
                for item in value[:6]:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("task") or item.get("title")
                        err = item.get("error") or item.get("message")
                        if name and name not in failed_tasks:
                            failed_tasks.append(str(name))
                        if err and str(err) not in signals:
                            signals.append(str(err)[:500])
                    elif item and str(item) not in signals:
                        signals.append(str(item)[:500])

    lower = text.lower()
    repair_plan = {
        "priority": "manual_review",
        "can_repair_yaml": False,
        "focus": [],
        "avoid": []
    }
    if any(word in lower for word in ("ai call error", "failed to call ai model service", "request was aborted", "model-provider.html")):
        failure_type = "model_service"
        repair_plan = {
            "priority": "environment_first",
            "can_repair_yaml": False,
            "focus": [
                "检查 Runner 侧模型环境变量是否包含 OPENAI_API_KEY、OPENAI_BASE_URL、MIDSCENE_MODEL_NAME、MIDSCENE_USE_QWEN_VL=1",
                "检查 Windows Runner 到 DashScope compatible-mode 接口的网络连通性和超时",
                "确认最新部署包已下发 runtime-env，必要时重启 Runner 清理旧环境缓存"
            ],
            "avoid": ["不要把模型服务中断误判成元素定位问题", "不要自动修 YAML", "不要删除或放宽业务断言"]
        }
    elif any(word in lower for word in ("unknown flowitem", "property", "yaml", "failed to load")):
        failure_type = "yaml_syntax"
        repair_plan = {
            "priority": "rule_first",
            "can_repair_yaml": True,
            "focus": ["修复 flowItem 名称大小写、冒号空格、缩进、aiAssert/aiInput 子字段结构", "不改变业务路径和断言含义"],
            "avoid": ["不要重写整条业务链路", "不要新增无关点击"]
        }
    elif any(word in lower for word in ("model configuration", "api_key", "base url", "midscene_model_name")):
        failure_type = "model_config"
        repair_plan = {
            "priority": "environment_first",
            "can_repair_yaml": False,
            "focus": ["检查 Midscene 模型环境变量和 API 配置"],
            "avoid": ["不要修改 YAML 业务步骤"]
        }
    elif any(word in lower for word in ("adb", "device offline", "no device", "device not found")):
        failure_type = "device_env"
        repair_plan = {
            "priority": "environment_first",
            "can_repair_yaml": False,
            "focus": ["检查设备连接、adb devices、Sonic runner 设备占用"],
            "avoid": ["不要修改 YAML 业务步骤"]
        }
    elif runtime_toast_error_from_text(text):
        failure_type = "runtime_toast_error"
        repair_plan = {
            "priority": "product_or_data_first",
            "can_repair_yaml": False,
            "focus": ["报告或截图出现运行时 toast/错误浮层，优先按产品/数据/环境问题处理"],
            "avoid": ["不要删除断言", "不要通过加等待或放宽断言掩盖运行时错误"]
        }
    elif evidence_needs_adb_input_fallback(text):
        failure_type = "input_failed"
        repair_plan = {
            "priority": "targeted_yaml_repair",
            "can_repair_yaml": True,
            "focus": ["修复输入步骤：先 aiTap 输入框，再 aiInput + value", "只有确认 aiInput 没有实际输入时才允许 ADB input text 兜底", "避免重复输入"],
            "avoid": ["不要同时默认保留 aiInput 和 adb input text", "不要把中文输入改成 adb input text"]
        }
    elif evidence_is_toast_assertion_issue(text):
        failure_type = "toast_assertion"
        repair_plan = {
            "priority": "targeted_yaml_repair",
            "can_repair_yaml": True,
            "focus": [
                "保存/导出/下载/生成/转换这类结果型操作后，立即等待多个同义成功提示",
                "如果短暂提示消失，改用结果流程结束且无失败态作为兜底",
                "只校验没有保存失败、导出失败、下载失败、生成失败、转换失败、权限失败、网络错误或异常弹窗；不要要求页面保持静止或某个按钮仍可见"
            ],
            "avoid": ["不要把断言放宽成页面正常", "不要删除结果校验", "不要要求页面保持静止", "不要要求导出/保存按钮仍可见", "不要无限加长等待 toast"]
        }
    elif any(word in lower for word in ("failed to locate", "找不到", "not found", "cannot find")):
        failure_type = "element_not_found"
        repair_plan = {
            "priority": "targeted_yaml_repair",
            "can_repair_yaml": True,
            "focus": ["参考页面知识和失败截图修正入口文案/目标描述", "必要时补充从首页到目标页面的稳定导航", "点击动作使用明确 aiTap"],
            "avoid": ["不要坐标点击", "不要点击随机相似元素", "不要删除业务关键步骤"]
        }
    elif any(word in lower for word in ("assertion failed", "task failed:", "验证", "assert")):
        failure_type = "assertion_failed"
        repair_plan = {
            "priority": "review_then_repair",
            "can_repair_yaml": True,
            "focus": ["判断是否断言过严", "把断言改为真实可见、符合业务意图的 UI 状态", "如果页面确实不符合需求，保留为产品 Bug"],
            "avoid": ["不要为了通过删除关键断言", "不要把真实失败改成泛化的页面正常"]
        }
    elif any(word in lower for word in ("timeout", "timed out", "超时")):
        failure_type = "timeout"
        repair_plan = {
            "priority": "review_then_repair",
            "can_repair_yaml": True,
            "focus": ["区分环境超时和业务加载等待短", "短等待改成 aiWaitFor + 目标 UI 条件 + 合理 timeout", "只做一次等待策略修复"],
            "avoid": ["不要无限加长 timeout", "不要用固定长 sleep 代替条件等待"]
        }
    elif any(word in lower for word in ("弹窗", "dialog", "popup", "permission", "overlay", "遮挡")):
        failure_type = "popup_overlay"
        repair_plan = {
            "priority": "targeted_yaml_repair",
            "can_repair_yaml": True,
            "focus": ["只在关键路径前增加弹窗/权限/浮层处理", "处理后继续回到业务目标"],
            "avoid": ["不要每一步都加弹窗处理", "不要坐标关闭"]
        }
    else:
        failure_type = "unknown"

    return {
        "failure_type": failure_type,
        "failed_tasks": failed_tasks[:8],
        "signals": signals[:16],
        "repair_plan": repair_plan
    }


def repair_strategy_guide():
    """返回修复决策策略文本。"""
    return """
修复决策策略：
1. 先判断是否真的应该修脚本。模型配置、设备离线、网络断连、服务端 5xx、ADB 异常不应改 YAML，只在 analysis 里说明环境问题。
2. 修复优先级：YAML 语法/flowItem 名称/冒号空格/空 flow > App 启动和关闭 > 页面稳定起点 > 弹窗遮挡 > 加载等待 > 导航路径 > 断言表达。
3. 如果是 YAML 语法问题，只修语法，不改业务路径。常见问题包括 terminate:com.xxx 缺空格、tap/click/action 这类非标准 key、sleep 写成字符串、flow 为空。
4. 如果是启动/页面起点问题，优先补 HOME、force-stop、launch、首页稳定导航、底部首页 Tab、必要的 aiWaitFor。
5. 如果脚本还没跑到业务步骤，不要改业务步骤和断言；只补运行时守卫后让下一轮重跑。
6. 如果是找不到入口，优先参考页面知识和截图中的真实文案，增加从稳定页面到目标入口的导航路径；不要臆造按钮，不要坐标。
7. 如果是弹窗/权限/升级/广告/引导遮挡，只在关键路径前补自然语言弹窗处理，不要每一步都加，避免拖慢。
8. 如果是加载慢，使用 aiWaitFor + timeout 等目标 UI 条件，不要用固定长 sleep。Midscene 自身会重试/重规划，不要无限加长等待；任何新增或修改的 aiWaitFor timeout 都不得超过 300000ms。只有 3D/模型/建模/切片/STL/OBJ/模型导入这类链路才允许写"模型处理进度到 100%"和 180000~240000ms；2D/文档/错题/基础打印/相册/扫描/格式转换链路禁止套用"模型处理进度"，应等待"打印前准备完成、立即打印按钮、确认打印弹窗/按钮"等真实 UI 条件，通常 30000~60000ms。只能在"原等待明显偏短或条件过泛"时修一次，不要反复加长等待掩盖真实产品/环境问题。
8.1 如果失败发生在中间流程，例如点击"完成/确认/下一步"后目标格式按钮、PNG/PDF/Word、导出或确认按钮尚未渲染，应该在这两个业务动作之间补 aiWaitFor 等待目标按钮/选项出现，不要把它误修成最终保存成功校验。
8.2 失败关键帧若明确显示同级入口行在屏幕边缘被裁切，应先尝试一次有界横向恢复再判产品缺陷。必须使用官方 aiScroll：目标用当前页真实可见文案描述具体横向区域，`scrollType: "singleAction"`、`direction: "right"`、`distance` 不超过 400，滑动后重新等待目标入口；一次不足时最多补第二次。禁止坐标、ADB swipe 和含糊的整页滚动。
9. 如果是断言失败，先判断是否断言过严。可把"完全一致"改为"页面标题、关键入口、列表或空态可见"等视觉可验证断言；不要把真实产品缺陷改没。
9.1 如果失败是保存/导出/下载/生成/转换这类结果型操作的短暂提示没捕捉到，先结合原 YAML 的业务链路和失败截图判断。可以优化为更合理的成功提示或失败态校验，但只能改失败相关步骤，不要批量插入重复校验，不要改变中间业务流程。
10. 修复业务链路时，必须先对齐 goal、start_page、business_path、expected_result：入口路径可以修，等待条件可以修，断言表达可以修，但不能绕开核心业务目标。
11. 每个 aiTap 都必须有业务目的：进入目标页面、触发目标功能、选择目标条件、提交目标操作。不要为了"能点"而点击无关卡片、返回键、广告、推荐内容或随机入口。
12. 每个断言都必须验证业务结果：页面标题、目标入口状态、列表/空态、弹窗文案、按钮状态、结果区域。不要用"页面正常展示""操作成功"这类无法对应业务目标的泛化断言替代真实预期。
13. 不要为了让用例通过而删除关键步骤或关键断言；只能把过严/不稳定的表述改成更贴近真实 UI 的可见断言。
14. 如果当前步骤和业务链路冲突，优先修正为页面知识/截图支持的真实链路；如果页面知识不足，不要大幅改写，只补稳定导航和更清晰断言。
15. 如果页面知识/截图与原 YAML 冲突，优先页面知识/截图；如果仍不确定，做最小改动并保留 baseline 注释。
16. 每次修复要最小化：只改失败相关 task 或相关步骤，不要重写大量用例，不要改变包名。
17. 输出 changes 要具体说明"为什么改、改了哪里"，便于人工审查。
18. 必须服从失败摘要里的 repair_plan：can_repair_yaml=false 时不要实质改业务 YAML；priority=rule_first 时只做确定性语法/结构修复；priority=targeted_yaml_repair 时只改对应失败点；priority=review_then_repair 时先判断是否可能是真产品问题，再做最小脚本修复。
19. 修复前必须阅读业务链路上下文里的 goal、start_page、business_path、expected_result、current_actions、current_assertions。修复后必须保留原业务目标和核心路径锚点；可以改入口描述、等待条件、断言表达，但不能把"测试什么功能"改成另一个功能。
""".strip()


# ---------------------------------------------------------------------------
# Failure context helpers
# ---------------------------------------------------------------------------

def execution_screenshot_context(job, limit=4):
    """从 job 运行目录收集执行截图。"""
    run_dir = job.get("run_dir") or ""
    if not run_dir:
        return []
    screenshot_dir = Path(run_dir) / "screenshots"
    if not screenshot_dir.exists():
        return []
    assets = []
    for path in sorted(screenshot_dir.iterdir(), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
        if len(assets) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        try:
            data = path.read_bytes()
            if not data or len(data) > 2 * 1024 * 1024:
                continue
            assets.append({
                "name": path.name,
                "mime": guess_mime(path.name),
                "base64": base64.b64encode(data).decode("ascii")
            })
        except Exception:
            continue
    return assets


def flow_items_with_index(task_block):
    """解析 task block 中的 flow items 及其索引。"""
    items = []
    lines = (task_block or "").splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = re.match(r"^(\s*)-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", line)
        if not m:
            idx += 1
            continue
        indent, key, raw_value = m.groups()
        if key == "name":
            idx += 1
            continue
        children = []
        j = idx + 1
        while j < len(lines):
            child = lines[j]
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            children.append(child)
            j += 1
        items.append({
            "index": len(items),
            "line": idx + 1,
            "key": key,
            "value": strip_yaml_quotes(raw_value),
            "text": "\n".join([line] + children),
            "children": children,
            "indent": indent
        })
        idx = j
    return items


def failure_target_terms(text):
    """从文本中提取失败目标术语。"""
    terms = []
    raw = str(text or "")
    quoted = re.findall("[\u300c\u201c\"\u0027]([^\u300d\u201d\"\u0027]{1,30})[\u300d\u201d\"\u0027]", raw)
    for item in quoted:
        item = item.strip()
        if item and item not in terms:
            terms.append(item)
    for word in ("试卷夹", "确认打印", "立即打印", "下一步", "完成", "搜索", "保存", "导出", "登录", "首页"):
        if word in raw and word not in terms:
            terms.append(word)
    return terms[:8]


def locate_failure_window(task_block, evidence_text="", radius=5):
    """在 task block 中定位失败窗口。"""
    items = flow_items_with_index(task_block)
    if not items:
        return {"failed_index": -1, "before": [], "after": [], "items": []}
    evidence = str(evidence_text or "")
    terms = failure_target_terms(evidence)
    failed_index = -1
    for idx, item in enumerate(items):
        blob = "\n".join([item.get("value", ""), item.get("text", "")])
        if terms and any(term and term in blob for term in terms):
            failed_index = idx
            break
    if failed_index < 0:
        for idx, item in enumerate(items):
            if item.get("key") in ("aiAssert", "aiWaitFor") and any(word in item.get("value", "") for word in ("未出现", "找不到", "可见", "出现")):
                failed_index = idx
                break
    if failed_index < 0:
        failed_index = min(len(items) - 1, max(0, len(items) - 2))
    start = max(0, failed_index - radius)
    end = min(len(items), failed_index + radius + 1)
    return {
        "failed_index": failed_index,
        "before": items[start:failed_index],
        "failed": items[failed_index] if 0 <= failed_index < len(items) else None,
        "after": items[failed_index + 1:end],
        "items": items
    }


def build_failure_context(job, yaml_text, stdout="", stderr="", summary=None, task_name=""):
    """构建失败上下文，供后续分类和修复使用。"""
    module = job.get("module", "")
    file = job.get("file", "")
    report_text = report_text_context(job)
    evidence_text = "\n".join([
        stdout or "",
        stderr or "",
        json.dumps(summary, ensure_ascii=False)[:3000] if summary is not None else "",
        report_text or "",
    ])
    from task_server.services.yaml_service import detect_yaml_platform, resolve_app_package
    platform = detect_yaml_platform(yaml_text)
    app_package = resolve_app_package(module, file, yaml_text)
    target_task = task_name or job.get("target_task_name") or ""
    task_block = ""
    if target_task:
        try:
            task_block = find_yaml_task_block(yaml_text, target_task)["block"]
        except Exception:
            task_block = ""
    if not task_block:
        names = yaml_task_names(yaml_text)
        for name in names:
            if name and (name in evidence_text or not task_block):
                try:
                    task_block = find_yaml_task_block(yaml_text, name)["block"]
                    target_task = name
                    if name in evidence_text:
                        break
                except Exception:
                    continue
    business_context = task_business_context(task_block, "") if task_block else {}
    failure_window = locate_failure_window(task_block, evidence_text)
    return {
        "module": module,
        "file": file,
        "task_name": target_task,
        "platform": platform,
        "app_package": app_package,
        "run_mode": job.get("run_mode", "test"),
        "evidence_text": evidence_text,
        "report_text": report_text,
        "failure_brief": extract_failure_brief(stdout, stderr, summary),
        "business_context": business_context,
        "failure_window": failure_window,
        "task_block": task_block,
        "yaml_text": yaml_text
    }


def positive_overlay_evidence(text):
    """Return runtime overlay signals without treating negated assertions as evidence."""
    result = []
    positive_patterns = (
        r"(?:权限|授权|系统|引导)?弹窗(?:出现|弹出|遮挡|挡住)",
        r"(?:浮层|蒙层|引导层)(?:出现|遮挡|挡住)",
        r"(?:按钮|入口|页面|控件)被(?:弹窗|浮层|蒙层|引导)遮挡",
        r"(?:blocked by|covered by).*(?:dialog|modal|overlay)",
        r"(?:permission|system) (?:dialog|popup).*(?:shown|visible|blocking)",
    )
    negated_terms = (
        "无弹窗", "未出现弹窗", "没有弹窗", "不存在弹窗", "无遮挡", "无任何遮挡",
        "未被遮挡", "没有遮挡", "无浮层", "未出现浮层", "弹窗未出现", "not blocked",
        "no popup", "no dialog", "without overlay",
    )
    for line in str(text or "").splitlines():
        compact = line.strip()
        if not compact:
            continue
        lowered = compact.lower()
        if any(term in lowered for term in negated_terms):
            continue
        if any(re.search(pattern, compact, re.I) for pattern in positive_patterns):
            result.append(compact[:500])
            if len(result) >= 8:
                break
    return result


def classify_failure_by_context(ctx):
    """基于上下文对失败进行分类（确定性规则优先）。"""
    yaml_text = ctx.get("yaml_text", "")
    task_block = ctx.get("task_block", "")
    evidence = ctx.get("evidence_text", "")
    lower = evidence.lower()
    runtime_toast = runtime_toast_error_from_text(evidence)
    if runtime_toast:
        return {
            "category": "product_bug",
            "failure_type": "runtime_toast_error",
            "confidence": 0.93,
            "reason": f"报告或执行证据中出现运行时错误 toast/浮层：{runtime_toast}，这不是普通元素定位失败，不能通过放宽 YAML 掩盖",
            "evidence": [runtime_toast],
            "suggested_action": "保留失败并提交产品/运行时问题；若确认是测试数据或环境导致，再由人工调整数据后重跑",
            "can_auto_repair": False
        }
    brief = ctx.get("failure_brief") or {}
    if brief.get("failure_type") in ("model_config", "model_service", "device_env"):
        return {
            "category": "env_issue",
            "failure_type": brief.get("failure_type"),
            "confidence": 0.96,
            "reason": "失败属于模型配置、模型服务或设备环境问题，不应修改 YAML",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "先修复环境/设备/模型服务后重跑",
            "can_auto_repair": False
        }
    if any(word in lower for word in ("ai call error", "failed to call ai model service", "request was aborted", "model-provider.html")):
        return {
            "category": "env_issue",
            "failure_type": "model_service",
            "confidence": 0.96,
            "reason": "Midscene 调用视觉模型服务时被中断或超时，不是 YAML 业务链路错误",
            "evidence": [line for line in evidence.splitlines() if "AI call error" in line or "Request was aborted" in line or "model-provider" in line][:8],
            "suggested_action": "先检查 Runner 模型环境、DashScope 网络连通性和重试执行；确认模型服务稳定后再判断是否需要修脚本",
            "can_auto_repair": False
        }
    yaml_check = validate_midscene_yaml(yaml_text)
    if not yaml_check.get("ok"):
        return {
            "category": "script_issue",
            "failure_type": "yaml_syntax",
            "confidence": 0.98,
            "reason": "YAML 基础结构或 Midscene flowItem 校验未通过",
            "evidence": yaml_check.get("warnings", [])[:8],
            "suggested_action": "只执行规则级 YAML 结构/flowItem 修复，不改业务链路",
            "can_auto_repair": True
        }
    if brief.get("failure_type") in ("model_config", "model_service", "device_env"):
        return {
            "category": "env_issue",
            "failure_type": brief.get("failure_type"),
            "confidence": 0.96,
            "reason": "失败属于模型配置或设备环境问题，不应修改 YAML",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "先修复环境/设备/模型配置后重跑",
            "can_auto_repair": False
        }
    if "http error" in lower or "request entity too large" in lower or re.search(r"\b50[234]\b", lower):
        return {
            "category": "env_issue",
            "failure_type": "server_or_upload",
            "confidence": 0.9,
            "reason": "失败包含服务端或报告上传错误，不应修改业务 YAML",
            "evidence": [line for line in evidence.splitlines() if "HTTP" in line or "Error" in line][:6],
            "suggested_action": "先处理服务端上传/代理限制，再重跑",
            "can_auto_repair": False
        }
    if any(word in lower for word in ("ai call error", "failed to call ai model service", "request was aborted", "model-provider.html")):
        return {
            "category": "env_issue",
            "failure_type": "model_service",
            "confidence": 0.96,
            "reason": "Midscene 调用视觉模型服务时被中断或超时，不是 YAML 业务链路错误",
            "evidence": [line for line in evidence.splitlines() if "AI call error" in line or "Request was aborted" in line or "model-provider" in line][:8],
            "suggested_action": "先检查 Runner 模型环境、DashScope 网络连通性和重试执行；确认模型服务稳定后再判断是否需要修脚本",
            "can_auto_repair": False
        }
    horizontal = detect_horizontal_scroll_script_issue(task_block or yaml_text, evidence)
    if horizontal:
        horizontal["failure_type"] = "scroll_not_effective"
        return horizontal
    wait_issue = detect_wait_strategy_issue(task_block or yaml_text, evidence)
    if wait_issue:
        wait_issue["failure_type"] = "wait_strategy"
        return wait_issue
    if evidence_needs_adb_input_fallback(evidence):
        return {
            "category": "script_issue",
            "failure_type": "input_failed",
            "confidence": 0.9,
            "reason": "日志显示输入框未实际输入或输入失败，应修复输入动作",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "修复 aiInput + value，必要时仅对安全文本加 ADB input 兜底",
            "can_auto_repair": True
        }
    if any(word in lower for word in ("unknown flowitem", "failed to load", "property \"tasks\" is required", "yaml格式", "yaml语法")):
        return {
            "category": "script_issue",
            "failure_type": "yaml_syntax",
            "confidence": 0.94,
            "reason": "执行日志显示 YAML 语法或 flowItem 不兼容",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "优先规则修复 YAML 语法、flowItem 名称和缩进结构",
            "can_auto_repair": True
        }
    overlay_evidence = positive_overlay_evidence(evidence)
    if overlay_evidence:
        return {
            "category": "script_issue",
            "failure_type": "popup_overlay",
            "confidence": 0.82,
            "reason": "失败上下文出现弹窗/权限/浮层遮挡信号",
            "evidence": overlay_evidence,
            "suggested_action": "只在关键路径前增加弹窗/权限处理，然后继续原业务目标",
            "can_auto_repair": True
        }
    if any(word in lower for word in ("failed to locate", "not found", "cannot find")) or any(word in evidence for word in ("找不到", "未找到")):
        return {
            "category": "script_issue",
            "failure_type": "element_not_found",
            "confidence": 0.74,
            "reason": "目标元素未定位到，优先按脚本定位/导航问题处理一次；若修复后仍失败再转人工判断产品问题",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "结合失败步骤前后上下文、页面知识和截图修正定位描述或导航",
            "can_auto_repair": True
        }
    if "assert" in lower or "断言" in evidence or "验证" in evidence:
        return {
            "category": "script_issue",
            "failure_type": "assertion_too_strict",
            "confidence": 0.68,
            "reason": "断言失败，先检查是否断言过严或不贴近业务可见状态",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "把过严断言改成业务意图 + UI 可见信号，不删除关键断言",
            "can_auto_repair": True
        }
    return None


# ---------------------------------------------------------------------------
# AI Skill: requirement_analyzer
# ---------------------------------------------------------------------------

AI_REQUIREMENT_ANALYZER_TIMEOUT_SECONDS = max(30, safe_int(os.getenv("MIDSCENE_REQUIREMENT_ANALYZER_TIMEOUT_SECONDS", "90"), 90))


def normalize_source_quality(value):
    """规范化来源质量评估。"""
    source = value if isinstance(value, dict) else {}
    normalized = {}
    for key in ("requirement", "ui", "knowledge"):
        text = str(source.get(key) or "").strip().lower()
        normalized[key] = text if text in ("sufficient", "partial", "missing") else "missing"
    return normalized


def normalize_readiness_level(score, blockers=None, missing_inputs=None, questions=None, explicit=""):
    """规范化就绪等级。"""
    explicit = str(explicit or "").strip().lower()
    if explicit in ("ready", "review", "blocked"):
        return explicit
    if blockers:
        return "blocked"
    if score < 50:
        return "blocked"
    if score < 75 or missing_inputs or questions:
        return "review"
    return "ready"


def normalize_requirement_analysis_result(result):
    """规范化需求分析结果。"""
    result = result if isinstance(result, dict) else {}
    for key in (
        "business_goals", "roles", "entry_points", "state_assumptions",
        "data_assumptions", "visible_outcomes", "risks", "requirement_points",
        "questions", "missing_inputs", "blockers", "assumptions"
    ):
        result[key] = normalize_text_list(result.get(key))
    confidence = str(result.get("confidence") or "medium").strip().lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    result["confidence"] = confidence
    source_quality = normalize_source_quality(result.get("source_quality"))
    if source_quality.get("requirement") == "missing" and (result["requirement_points"] or result["business_goals"]):
        source_quality["requirement"] = "partial"
    if source_quality.get("ui") == "missing" and (result["entry_points"] or result["visible_outcomes"]):
        source_quality["ui"] = "partial"
    result["source_quality"] = source_quality
    score = safe_int(result.get("readiness_score") or result.get("readinessScore"), 0)
    if score <= 0:
        score = {"high": 86, "medium": 70, "low": 48}.get(confidence, 70)
        score -= min(25, len(result["questions"]) * 5)
        score -= min(25, len(result["missing_inputs"]) * 5)
        score -= min(30, len(result["blockers"]) * 12)
        if source_quality.get("requirement") == "missing":
            score -= 12
        if source_quality.get("ui") == "missing":
            score -= 8
        if not result["requirement_points"]:
            score -= 20
    score = max(0, min(100, score))
    result["readiness_score"] = score
    result["readiness_level"] = normalize_readiness_level(
        score,
        blockers=result["blockers"],
        missing_inputs=result["missing_inputs"],
        questions=result["questions"],
        explicit=result.get("readiness_level") or result.get("readinessLevel")
    )
    return result


def normalize_source_requirement_contract(value):
    """Normalize an explicit source-derived coverage contract for AI planning."""
    value = value if isinstance(value, dict) else {}
    source = str(value.get("source") or "").strip()
    candidate_only = value.get("candidateOnly") is True or value.get("candidate_only") is True
    if source != "requirement_candidates" or not candidate_only:
        return {}
    flows = []
    seen = set()
    for index, raw in enumerate(value.get("businessFlows") or value.get("business_flows") or []):
        if not isinstance(raw, dict):
            continue
        branch = str(raw.get("branch") or raw.get("name") or "").strip()
        checks = normalize_text_list(raw.get("checks"))[:8]
        branch_key = re.sub(r"\s+", "", branch).lower()
        if not branch_key or not checks or branch_key in seen:
            continue
        seen.add(branch_key)
        flows.append({
            "id": str(raw.get("id") or f"FLOW-{index + 1:03d}").strip()[:40],
            "name": str(raw.get("name") or branch).strip()[:120],
            "branch": branch[:80],
            "steps": normalize_text_list(raw.get("steps"))[:10],
            "checks": checks,
        })
        if len(flows) >= 8:
            break
    if not flows:
        return {}
    return {
        "source": source,
        "candidateOnly": True,
        "relationship": str(value.get("relationship") or "unknown").strip(),
        "businessFlows": flows,
    }


def source_requirement_contract_points(value):
    """Build immutable hard-coverage points from explicit source branches/checks."""
    contract = normalize_source_requirement_contract(value)
    points = []
    for index, flow in enumerate(contract.get("businessFlows") or [], start=1):
        checks = "；".join(normalize_text_list(flow.get("checks"))[:8])
        if not checks:
            continue
        points.append(f"REQ-{index:03d} {flow.get('branch') or flow.get('name')}：{checks}")
    return points


def classify_requirement_acceptance_check(value):
    """Classify one explicit acceptance check without changing its source text."""
    text = str(value or "").strip()
    compact = re.sub(r"\s+", "", text).lower()
    if any(term in compact for term in (
        "点击", "点按", "轻触", "跳转", "打开", "进入", "唤起", "可达", "落地页",
    )):
        return "reachability"
    if any(term in compact for term in (
        "同级", "层级", "位置", "关系", "并列", "相邻", "对齐", "布局", "排序",
    )):
        return "relation"
    if any(term in compact for term in (
        "文案", "文字", "文本", "命名", "名称", "完整", "截断", "清晰", "显示为",
    )):
        return "copy"
    if any(term in compact for term in ("可见", "展示", "显示", "存在", "出现")):
        return "visibility"
    return "general"


def source_requirement_acceptance_checks(value):
    """Expand branch-level requirement points into auditable acceptance dimensions."""
    contract = normalize_source_requirement_contract(value)
    checks = []
    for requirement_index, flow in enumerate(contract.get("businessFlows") or [], start=1):
        requirement_id = f"REQ-{requirement_index:03d}"
        for check_index, text in enumerate(normalize_text_list(flow.get("checks"))[:8], start=1):
            checks.append({
                "id": f"{requirement_id}-CHECK-{check_index:02d}",
                "requirementId": requirement_id,
                "flowId": str(flow.get("id") or "").strip(),
                "branch": str(flow.get("branch") or flow.get("name") or "").strip(),
                "kind": classify_requirement_acceptance_check(text),
                "text": text,
            })
    return checks


def requirement_acceptance_descriptor(check):
    """Return a stable internal descriptor that can focus the existing AI convergence pass."""
    check = check if isinstance(check, dict) else {}
    requirement_id = str(check.get("requirementId") or check.get("requirement_id") or "").strip()
    kind = str(check.get("kind") or "general").strip().lower() or "general"
    branch = str(check.get("branch") or "").strip()
    text = str(check.get("text") or "").strip()
    label = f"{branch}：{text}" if branch and text else (text or branch)
    return f"{requirement_id} [acceptance:{kind}] {label}".strip()


def apply_source_requirement_contract(analysis, value):
    """Keep AI interpretation advisory while source facts own the hard gate."""
    analysis = normalize_requirement_analysis_result(dict(analysis or {}))
    contract = normalize_source_requirement_contract(value)
    points = source_requirement_contract_points(contract)
    if not points:
        return analysis
    ai_points = normalize_text_list(analysis.get("requirement_points"))
    acceptance_checks = source_requirement_acceptance_checks(contract)
    analysis["requirement_points"] = points
    analysis["requirement_acceptance_checks"] = acceptance_checks
    analysis["ai_suggested_requirement_points"] = ai_points
    analysis["requirement_contract"] = {
        "applied": True,
        "source": contract.get("source"),
        "branch_count": len(contract.get("businessFlows") or []),
        "hard_point_count": len(points),
        "acceptance_check_count": len(acceptance_checks),
        "ai_suggested_point_count": len(ai_points),
        "rule": (
            "原始需求分支与验收维度决定硬覆盖；AI 可补充风险、问题和人工场景，"
            "但不能把推断状态升级为覆盖门禁或弱化明确分支。"
        ),
    }
    return analysis


def _fallback_requirement_points_from_text(title, text_assets):
    """从原始需求文本中提取保守需求点，避免需求分析模型超时后中断。"""
    raw = _joined_requirement_source(title, "", text_assets)
    if "百度网盘" in raw:
        click_flow = any(word in raw for word in (
            "点击触发", "点击后", "跳转", "授权", "登录", "文件选择", "导入文件",
            "进入百度网盘", "百度网盘导入", "WebView", "SDK", "埋点",
        ))
        suffix = "并校验入口位置及同级并列关系" if not click_flow else "并校验点击后进入百度网盘相关流程"
        points = []
        if "文档打印" in raw or "三方文档" in raw:
            points.append(f"三方文档打印：百度网盘入口移至第 2 个，位于本地文档之后{suffix}")
        if "普通照片" in raw:
            points.append(f"照片打印：普通照片打印导入时增加百度网盘入口{suffix}")
        if "普通证件照" in raw:
            points.append(f"照片打印：普通证件照导入时增加百度网盘入口{suffix}")
        if "智能证件照" in raw:
            points.append(f"照片打印：智能证件照导入时增加百度网盘入口{suffix}")
        if "照片拼版" in raw:
            points.append(f"照片打印：照片拼版导入时增加百度网盘入口{suffix}")
        if "扫描复印" in raw or "复印扫描" in raw:
            points.append(f"扫描复印：复印扫描首页增加百度网盘入口{suffix}")
        if "埋点" in raw:
            points.append("埋点：百度网盘文档、照片、复印入口点击上报")
        return points or [f"新增百度网盘入口{suffix}"]

    candidates = []
    for line in raw.splitlines():
        text = re.sub(r"^\s*[-*•\d.、)）]+\s*", "", line).strip()
        if 6 <= len(text) <= 90 and any(word in text for word in ("新增", "修改", "展示", "点击", "入口", "支持", "校验", "验证", "跳转")):
            candidates.append(text)
    return candidates[:12] or [str(title or "需求主流程").strip()]


def _fallback_requirement_analysis(title, module, text_assets, error=""):
    """需求分析模型超时/失败时的本地兜底结果。"""
    points = _fallback_requirement_points_from_text(title, text_assets)
    visible = []
    if any("百度网盘" in point for point in points):
        if any(any(word in point for word in ("点击后", "跳转", "授权", "登录", "文件选择", "导入文件", "埋点")) for point in points):
            visible = ["百度网盘入口可见", "点击入口后进入百度网盘导入、授权或登录提示流程"]
        else:
            visible = ["百度网盘入口可见", "百度网盘入口与同级导入入口并列展示"]
    else:
        visible = [f"{_fallback_feature_from_point(point)}相关页面可见" for point in points[:6]]
    return normalize_requirement_analysis_result({
        "business_goals": [str(title or module or "需求验证").strip()],
        "roles": ["普通用户"],
        "entry_points": ["App 首页", "需求相关入口"],
        "state_assumptions": ["已安装并登录 App", "网络正常"],
        "data_assumptions": [],
        "visible_outcomes": visible,
        "risks": [],
        "requirement_points": points,
        "questions": [],
        "missing_inputs": [],
        "blockers": [],
        "assumptions": ["需求分析 AI skill 超时或失败，已按原始需求文本进行本地保守抽取"],
        "confidence": "medium",
        "readiness_score": 78,
        "readiness_level": "review",
        "source_quality": {"requirement": "partial", "ui": "partial", "knowledge": "partial"},
        "fallback_reason": error,
    })


def call_skill_requirement_analyzer(
    title,
    module,
    text_assets,
    model_config=None,
    requirement_contract=None,
    runtime_trace=None,
):
    """调用 AI skill: requirement_analyzer。"""
    requirement_contract = normalize_source_requirement_contract(requirement_contract)
    payload = {
        "title": title,
        "module": module,
        "text_assets": compact_text_assets(text_assets)
    }
    if requirement_contract:
        payload["requirementContract"] = requirement_contract
    try:
        result = run_ai_skill(
            "requirement_analyzer",
            payload,
            timeout=AI_REQUIREMENT_ANALYZER_TIMEOUT_SECONDS,
            respect_global_timeout=False,
            retry_count=0,
            model_config=model_config,
            runtime_trace=runtime_trace,
        )
        return apply_source_requirement_contract(result, requirement_contract)
    except Exception as exc:
        fallback = _fallback_requirement_analysis(title, module, text_assets, error=str(exc))
        return apply_source_requirement_contract(fallback, requirement_contract)


# ---------------------------------------------------------------------------
# AI Skill: scenario_designer / automation_filter
# ---------------------------------------------------------------------------

def generation_volume_targets(analysis, mode="full"):
    """根据分析结果计算生成数量目标。"""
    from task_server.services.case_service import generation_volume_targets as _gvt
    return _gvt(analysis, mode=mode)


def generation_targets_for_scope(analysis, mode="full", scope_plan=None):
    """Use one platform-clamped 3/5/8 plan across the generation pipeline."""
    targets = dict(generation_volume_targets(analysis, mode=mode))
    scope_plan = scope_plan if isinstance(scope_plan, dict) else {}
    if not scope_plan or not scope_plan.get("targetCaseCount"):
        return targets
    target_count, _ = _clamp_scope_size(
        scope_plan.get("targetCaseCount"),
        targets.get("target_automation_cases") or 3,
    )
    point_count = len(normalize_text_list((analysis or {}).get("requirement_points")))
    requirement_floor = 3 if point_count <= 2 else (5 if point_count <= 5 else 8)
    target_count, size = _clamp_scope_size(max(target_count, requirement_floor), target_count)
    smoke_count = max(1, min(3, safe_int(scope_plan.get("smokeCount"), 3)))
    scenario_counts = {
        3: (3, 5),
        5: (5, 8),
        8: (8, 12),
    }
    min_scenarios, target_scenarios = scenario_counts[target_count]
    targets.update({
        "size": size,
        "min_automation_cases": target_count,
        "target_automation_cases": target_count,
        "max_automation_cases": target_count,
        "smoke_cases": smoke_count,
        "smoke_max_cases": 3,
        "min_scenarios": min_scenarios,
        "target_scenarios": target_scenarios,
        "scope_plan_applied": True,
        "scope_requirement_floor": requirement_floor,
        "scope_plan_reason": str(scope_plan.get("reason") or "AI 范围规划经平台 3/5/8 规则收敛"),
    })
    return targets


AI_SCENARIO_DESIGNER_TIMEOUT_SECONDS = max(30, safe_int(os.getenv("MIDSCENE_SCENARIO_DESIGNER_TIMEOUT_SECONDS", "90"), 90))
AI_AUTOMATION_FILTER_TIMEOUT_SECONDS = max(30, safe_int(os.getenv("MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS", "150"), 150))
AI_AUTOMATION_FILTER_MAX_TOKENS = max(
    4096,
    min(16384, safe_int(os.getenv("MIDSCENE_AUTOMATION_FILTER_MAX_TOKENS", "8192"), 8192)),
)


def scenario_requirement_point(scenario):
    """提取场景的需求点。"""
    if not isinstance(scenario, dict):
        return ""
    return first_non_empty(scenario.get("requirement_point"), scenario.get("requirementPoint"), scenario.get("coverage"), scenario.get("point"))


def _acceptance_requirement_ids(value):
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    result = []
    for match in re.finditer(r"\bREQ[-_ ]?0*(\d+)\b", str(text or ""), flags=re.I):
        requirement_id = f"REQ-{int(match.group(1)):03d}"
        if requirement_id not in result:
            result.append(requirement_id)
    return result


def _parse_requirement_acceptance_descriptor(value):
    text = str(value or "").strip()
    match = re.match(
        r"^(REQ[-_ ]?0*\d+)\s+\[acceptance:([a-z_]+)\]\s*(.*)$",
        text,
        flags=re.I,
    )
    if not match:
        return {}
    requirement_ids = _acceptance_requirement_ids(match.group(1))
    label = match.group(3).strip()
    branch, separator, check_text = label.partition("：")
    return {
        "id": text,
        "requirementId": requirement_ids[0] if requirement_ids else "",
        "branch": branch.strip() if separator else "",
        "kind": match.group(2).strip().lower(),
        "text": check_text.strip() if separator else label,
    }


def _acceptance_target_terms(value):
    text = str(value or "").strip()
    terms = []
    for item in re.findall(r"[「『“\"'‘]([^」』”\"'’]{1,32})[」』”\"'’]", text):
        item = item.strip()
        if item and item not in terms:
            terms.append(item)
    control_pattern = re.compile(
        r"(?:点击|点按|轻触|校验|验证|检查|确认|等待|展示|显示)?"
        r"[「『“\"'‘]?([\u4e00-\u9fffA-Za-z0-9_-]{2,24}?)[」』”\"'’]?"
        r"(?:入口|按钮|控件|选项|卡片|标签)"
    )
    for match in control_pattern.finditer(text):
        item = match.group(1).strip()
        item = re.sub(r"^(?:点击|点按|轻触|校验|验证|检查|确认|等待|展示|显示)", "", item)
        if item and item not in ("目标", "当前页面", "同级", "页面") and item not in terms:
            terms.append(item)
    if not terms:
        match = re.search(r"(?:点击|点按|轻触|打开|选择)([^，。；：]{1,24})", text)
        if match:
            item = re.sub(r"(?:入口|按钮|控件|选项|卡片|标签)$", "", match.group(1).strip())
            if item:
                terms.append(item)
    return terms[:4]


def _case_acceptance_evidence_items(case):
    case = case if isinstance(case, dict) else {}
    items = []

    def add(value):
        if value in (None, "", [], {}):
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                add(child)
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if child not in (None, "", [], {}):
                    items.append(f"{key}: {child}")
            return
        text = str(value).strip()
        if text:
            items.append(text)

    # Labels and requirement refs describe intent, not execution evidence. Only
    # concrete flow/assertion material can satisfy a source acceptance check.
    for key in (
        "steps", "assertions", "expected", "expected_result", "expectedResult", "content", "yaml",
    ):
        add(case.get(key))
    plan = case.get("ai_case_plan") if isinstance(case.get("ai_case_plan"), dict) else {}
    for key in ("flow", "assertionTarget", "executableReason"):
        add(plan.get(key))
    return items


def _case_execution_flow_items(case):
    """Return only flow material that can prove a concrete branch was visited."""
    case = case if isinstance(case, dict) else {}
    items = normalize_text_list(case.get("steps"))
    plan = case.get("ai_case_plan") if isinstance(case.get("ai_case_plan"), dict) else {}
    items.extend(normalize_text_list(plan.get("flow")))
    return list(dict.fromkeys(str(item or "").strip() for item in items if str(item or "").strip()))


def _compact_branch_text(value):
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).lower()


def _flow_item_has_navigation(value):
    compact = _compact_branch_text(value)
    return any(term in compact for term in (
        "点击", "点按", "轻触", "进入", "打开", "选择", "切换", "前往", "跳转到",
        "aitap", "aiaction", "aiact",
    ))


def _case_concrete_branch_segments(case, branch, sibling_branches):
    """Return branch-local flow segments instead of trusting aggregate prose."""
    branch_key = _compact_branch_text(branch)
    if not branch_key:
        return [_case_execution_flow_items(case)]
    sibling_keys = {
        _compact_branch_text(item)
        for item in (sibling_branches or [])
        if _compact_branch_text(item) and _compact_branch_text(item) != branch_key
    }
    flow = _case_execution_flow_items(case)
    segments = []
    for index, item in enumerate(flow):
        compact_item = _compact_branch_text(item)
        if branch_key not in compact_item or not _flow_item_has_navigation(item):
            continue
        if any(sibling_key in compact_item for sibling_key in sibling_keys):
            continue
        end = len(flow)
        for next_index in range(index + 1, len(flow)):
            next_item = flow[next_index]
            next_key = _compact_branch_text(next_item)
            if _flow_item_has_navigation(next_item) and any(
                sibling_key in next_key for sibling_key in sibling_keys
            ):
                end = next_index
                break
        segments.append(flow[index:end])
    return segments


def _case_has_concrete_branch_execution_evidence(case, branch, sibling_branches):
    """Require one branch-specific navigation step, not an aggregate branch claim."""
    return bool(_case_concrete_branch_segments(case, branch, sibling_branches))


def _case_covers_acceptance_in_portfolio(case, check, acceptance_checks):
    if not case_covers_requirement_acceptance(case, check):
        return False
    mapped_requirement_ids = set(_acceptance_requirement_ids([
        (case or {}).get("coverage"),
        (case or {}).get("requirement_point"),
        (case or {}).get("requirementPoint"),
        (case or {}).get("requirementRefs"),
        (case or {}).get("requirement_refs"),
    ]))
    if len(mapped_requirement_ids) <= 1:
        return True
    mapped_branches = list(dict.fromkeys(
        str(item.get("branch") or "").strip()
        for item in (acceptance_checks or [])
        if isinstance(item, dict)
        and str(item.get("requirementId") or "").strip() in mapped_requirement_ids
        and str(item.get("branch") or "").strip()
    ))
    distinct_branch_keys = {_compact_branch_text(item) for item in mapped_branches if _compact_branch_text(item)}
    branch = str((check or {}).get("branch") or "").strip()
    if branch and len(distinct_branch_keys) > 1:
        branch_key = _compact_branch_text(branch)
        sibling_keys = distinct_branch_keys.difference({branch_key})
        branch_assertions = [
            item for item in normalize_text_list(
                (case or {}).get("assertions")
                or (case or {}).get("expected_result")
                or (case or {}).get("expected")
            )
            if branch_key in _compact_branch_text(item)
            and not any(sibling_key in _compact_branch_text(item) for sibling_key in sibling_keys)
        ]
        for segment in _case_concrete_branch_segments(case, branch, mapped_branches):
            branch_probe = {
                "steps": segment,
                "assertions": branch_assertions,
                "requirementRefs": [str((check or {}).get("requirementId") or "")],
            }
            if case_covers_requirement_acceptance(branch_probe, check):
                return True
        return False
    return True


def case_covers_requirement_acceptance(case, check):
    """Require observable case evidence for one explicit acceptance dimension."""
    case = case if isinstance(case, dict) else {}
    check = check if isinstance(check, dict) else {}
    requirement_id = str(check.get("requirementId") or check.get("requirement_id") or "").strip()
    case_requirement_ids = _acceptance_requirement_ids([
        case.get("coverage"),
        case.get("requirement_point"),
        case.get("requirementPoint"),
        case.get("requirementRefs"),
        case.get("requirement_refs"),
    ])
    if requirement_id and case_requirement_ids and requirement_id not in case_requirement_ids:
        return False

    evidence_items = _case_acceptance_evidence_items(case)
    if not evidence_items:
        return False
    evidence = "\n".join(evidence_items)
    branch = str(check.get("branch") or "").strip()
    if requirement_id and not case_requirement_ids and branch and branch not in evidence:
        return False
    if branch and len(set(case_requirement_ids)) > 1:
        compact_branch = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", branch).lower()
        compact_evidence = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", evidence).lower()
        if compact_branch and compact_branch not in compact_evidence:
            return False

    targets = _acceptance_target_terms(check.get("text"))
    target_evidence = [item for item in evidence_items if not targets or any(term in item for term in targets)]
    if targets and not target_evidence:
        return False
    kind = str(check.get("kind") or classify_requirement_acceptance_check(check.get("text"))).strip().lower()
    compact_items = [re.sub(r"\s+", "", item).lower() for item in target_evidence]
    compact_evidence = re.sub(r"\s+", "", evidence).lower()

    if kind == "reachability":
        action_terms = ("点击", "点按", "轻触", "打开", "选择", "aitap", "aiaction", "aiact")
        terminal_terms = (
            "授权页", "登录页", "列表页", "文件列表", "内容列表", "选择页", "详情页", "结果页",
            "落地页", "提示页", "空态页", "弹窗", "已打开", "已进入", "成功唤起", "稳定可达",
            "无白屏", "未白屏", "无崩溃", "未崩溃", "无crash", "未crash",
        )
        has_target_action = any(any(term in item for term in action_terms) for item in compact_items)
        has_terminal = any(term in compact_evidence for term in terminal_terms)
        has_bounded_transition_terminal = bool(
            "已离开" in compact_evidence
            and any(term in compact_evidence for term in ("页面区域", "页面元素", "页面跳转"))
            and (
                any(term in compact_evidence for term in (
                    "无白屏", "未白屏", "无崩溃", "未崩溃", "无crash", "未crash",
                ))
                or re.search(
                    r"(?:未出现|没有出现|未发生|没有发生)[^；。]{0,20}(?:崩溃|crash|白屏|闪退)",
                    compact_evidence,
                    flags=re.I,
                )
            )
        )
        return has_target_action and (has_terminal or has_bounded_transition_terminal)
    if kind == "relation":
        return any(any(term in item for term in (
            "同级", "层级", "位置", "关系", "并列", "相邻", "对齐", "布局", "排序", "左侧", "右侧",
        )) for item in compact_items)
    if kind == "copy":
        negative_copy_terms = (
            "仅显示图标", "只有图标", "无文字", "无文案", "文字缺失", "文案缺失",
            "不显示文字", "未显示文字", "不显示文案", "未显示文案",
        )
        explicit_copy_terms = (
            "文案", "文字", "文本", "命名", "名称", "完整", "截断", "清晰", "显示为",
        )
        if any(
            not any(negative in item for negative in negative_copy_terms)
            and any(term in item for term in explicit_copy_terms)
            for item in compact_items
        ):
            return True
        assertion_items = []
        for key in ("assertions", "expected", "expected_result", "expectedResult"):
            assertion_items.extend(normalize_text_list(case.get(key)))
        plan = case.get("ai_case_plan") if isinstance(case.get("ai_case_plan"), dict) else {}
        assertion_items.extend(normalize_text_list(plan.get("assertionTarget")))
        return bool(targets) and any(
            any(term in item for term in targets)
            and any(verb in item for verb in ("展示", "显示", "可见", "出现"))
            and not any(negative in item for negative in negative_copy_terms)
            for item in assertion_items
        )
    if kind == "visibility":
        return any(any(term in item for term in ("可见", "展示", "显示", "存在", "出现", "看见")) for item in compact_items)
    check_text = re.sub(r"\s+", "", str(check.get("text") or "")).lower()
    return bool(check_text and check_text in compact_evidence)


def case_matches_requirement(case, requirement_point):
    """判断用例是否匹配需求点。"""
    acceptance = _parse_requirement_acceptance_descriptor(requirement_point)
    if acceptance:
        return case_covers_requirement_acceptance(case, acceptance)
    text = " ".join(normalize_text_list([
        (case or {}).get("coverage"),
        (case or {}).get("requirement_point"),
        (case or {}).get("requirementPoint"),
        (case or {}).get("requirementRefs"),
        (case or {}).get("requirement_refs"),
        (case or {}).get("title"),
        (case or {}).get("scenario"),
    ]))
    point = str(requirement_point or "").strip()
    if not point:
        return False
    case_requirement_ids = set(_planner_requirement_ids(text))
    point_requirement_ids = set(_planner_requirement_ids(point))
    if case_requirement_ids and point_requirement_ids and case_requirement_ids.intersection(point_requirement_ids):
        return True
    point_core = re.sub(r"^REQ[-_ ]?\d+\s*[:：.-]?\s*", "", point, flags=re.I).strip()
    return point in text or (point_core and point_core in text)


def build_skill_coverage_matrix(analysis, scenarios, cases, manual_cases):
    """构建技能覆盖矩阵。"""
    analysis = analysis if isinstance(analysis, dict) else {}
    existing = analysis.get("coverage_matrix") or analysis.get("coverageMatrix") or []
    if isinstance(existing, list) and existing:
        return existing
    points = normalize_text_list(analysis.get("requirement_points"))
    rows = []
    for point in points:
        related_scenarios = [
            item for item in (scenarios or [])
            if isinstance(item, dict) and (
                scenario_requirement_point(item) == point
                or case_matches_requirement({"coverage": scenario_requirement_point(item), "title": item.get("scenario")}, point)
            )
        ]
        auto = [
            first_non_empty(case.get("case_id"), case.get("caseId"), case.get("title"))
            for case in (cases or [])
            if isinstance(case, dict) and case_matches_requirement(case, point)
        ]
        manual = [
            first_non_empty(case.get("case_id"), case.get("caseId"), case.get("title"), case.get("reason"))
            for case in (manual_cases or [])
            if isinstance(case, dict) and case_matches_requirement(case, point)
        ]
        normal = [s.get("scenario") for s in related_scenarios if "正常" in str(s.get("type") or "")]
        negative = [s.get("scenario") for s in related_scenarios if "异常" in str(s.get("type") or "")]
        boundary = [s.get("scenario") for s in related_scenarios if "边界" in str(s.get("type") or "") or "状态" in str(s.get("type") or "")]
        rows.append({
            "feature": first_non_empty((related_scenarios[0] or {}).get("feature") if related_scenarios else "", "需求覆盖"),
            "requirement_point": point,
            "normal_scenarios": normalize_text_list(normal),
            "negative_scenarios": normalize_text_list(negative),
            "boundary_scenarios": normalize_text_list(boundary),
            "auto_cases": normalize_text_list(auto),
            "manual_cases": normalize_text_list(manual),
            "uncovered_reason": "" if auto or manual else "已识别需求点，但尚未生成可追溯用例，需人工补充或重新生成"
        })
    return rows


def _fallback_feature_from_point(point):
    """从需求点中提取稳定的功能名称，避免把 Figma 页面名带入用例标题。"""
    text = str(point or "").strip()
    text = re.sub(r"^REQ[-_ ]?\d+\s*[:：.-]?\s*", "", text, flags=re.I).strip()
    text = re.sub(r"[。；;].*$", "", text).strip()
    for sep in ("：", ":", "-", "—", "，", ","):
        if sep in text:
            left = text.split(sep, 1)[0].strip()
            if 2 <= len(left) <= 18:
                return left
    return text[:18] or "需求点"


def _analysis_text_blob(analysis):
    """把 analysis 中和需求相关的文本压平成字符串，供本地兜底判断。"""
    analysis = analysis if isinstance(analysis, dict) else {}
    parts = []
    for key in (
        "requirement_points", "business_goals", "business_flow", "visible_outcomes",
        "keywords", "summary", "scope", "risk_points", "manual_cases"
    ):
        parts.extend(normalize_text_list(analysis.get(key)))
    return "\n".join(parts)


def _joined_requirement_source(title="", module="", text_assets=None):
    parts = [str(title or ""), str(module or "")]
    for item in normalize_text_list(text_assets):
        text = str(item or "")
        generated_context = any(marker in text for marker in (
            "当前平台采用",
            "YAML 生成",
            "Midscene",
            "现有 YAML",
            "相似成功基线",
            "可信相似基线",
            "生成策略",
            "生成要求",
            "selection_rules",
            "不能继续等待原业务页",
            "第三方授权页",
            "点击百度网盘、微信、相册、相机",
        ))
        if generated_context:
            continue
        parts.append(text)
    return "\n".join(part for part in parts if part)


def _should_fast_path_baidu_entry_visibility(title, module, text_assets):
    """入口可见性需求先生成稳定短链路，避免等待完整 AI skill 后才兜底。"""
    blob = _joined_requirement_source(title, module, text_assets)
    if "百度网盘" not in blob or "入口" not in blob:
        return False
    if any(term in blob for term in ("点击后", "跳转", "授权", "登录", "文件选择", "导入文件", "进入百度网盘", "WebView", "SDK")):
        return False
    return any(term in blob for term in ("首页", "基础打印", "文档打印", "照片打印", "扫描复印", "复印扫描", "可见", "展示", "位置", "并列", "同级"))


def should_fast_path_baidu_entry_visibility(title, module, text_assets):
    return _should_fast_path_baidu_entry_visibility(title, module, text_assets)


def _baidu_netdisk_requirement_points(analysis):
    """百度网盘入口需求使用确定性拆分，避免历史页面名污染用例。"""
    blob = _analysis_text_blob(analysis)
    if "百度网盘" not in blob:
        return []
    display_only = any(word in blob for word in (
        "入口展示", "入口显示", "入口可见", "可见性", "入口位置", "位置校验",
        "同级", "并列", "同级并列", "入口排序", "入口布局",
    ))
    click_flow = any(word in blob for word in (
        "点击触发", "点击后", "跳转", "授权", "登录", "文件选择", "导入文件",
        "进入百度网盘", "百度网盘导入", "WebView", "SDK",
    ))
    if not click_flow:
        return [
            ("文档打印", "文档打印首页展示百度网盘入口，并校验入口位于本地文档之后及同级并列关系。"),
            ("普通照片打印", "普通照片打印导入方式中展示百度网盘入口，并校验入口位置及同级并列关系。"),
            ("普通证件照", "普通证件照导入方式中展示百度网盘入口，并校验入口位置及同级并列关系。"),
            ("智能证件照", "智能证件照导入方式中展示百度网盘入口，并校验入口位置及同级并列关系。"),
            ("照片拼版", "照片拼版导入方式中展示百度网盘入口，并校验入口位置及同级并列关系。"),
            ("扫描复印", "复印扫描首页展示百度网盘入口，并校验入口位置及同级并列关系。"),
        ]
    return [
        ("文档打印", "文档打印首页展示百度网盘入口，入口位于本地文档之后，点击后进入百度网盘导入或授权流程。"),
        ("普通照片打印", "普通照片打印导入方式中展示百度网盘入口，点击后进入百度网盘导入或授权流程。"),
        ("普通证件照", "普通证件照导入方式中展示百度网盘入口，点击后进入百度网盘导入或授权流程。"),
        ("智能证件照", "智能证件照导入方式中展示百度网盘入口，点击后进入百度网盘导入或授权流程。"),
        ("照片拼版", "照片拼版导入方式中展示百度网盘入口，点击后进入百度网盘导入或授权流程。"),
        ("扫描复印", "复印扫描首页展示百度网盘入口，点击后进入百度网盘导入或授权流程。"),
    ]


def _baidu_netdisk_point_is_display_only(point):
    text = str(point or "")
    if "百度网盘" not in text:
        return False
    display_terms = ("入口展示", "入口显示", "入口可见", "可见性", "入口位置", "位置", "同级", "并列", "排序", "布局")
    external_terms = ("点击后", "跳转", "授权", "登录", "文件选择", "导入文件", "进入百度网盘", "WebView", "SDK")
    return any(term in text for term in display_terms) and not any(term in text for term in external_terms)


def _fallback_scenarios_from_analysis(title, module, analysis, targets=None, error=""):
    """AI 场景设计超时/空结果时的本地场景兜底。"""
    targets = targets or {}
    explicit_points = _baidu_netdisk_requirement_points(analysis)
    if not explicit_points:
        points = normalize_text_list((analysis or {}).get("requirement_points"))
        if not points:
            points = normalize_text_list([
                (analysis or {}).get("summary"),
                title,
            ])
        explicit_points = [(_fallback_feature_from_point(point), point) for point in points if str(point or "").strip()]

    max_scenarios = max(1, min(
        safe_int(targets.get("target_scenarios"), len(explicit_points) or 1),
        safe_int(targets.get("max_cases"), len(explicit_points) or 1),
        len(explicit_points) or 1,
    ))
    scenarios = []
    for index, (feature, point) in enumerate(explicit_points[:max_scenarios], start=1):
        if _baidu_netdisk_point_is_display_only(point):
            scenario_name = f"{feature}百度网盘入口可见性及同级并列校验"
            business_path = f"进入{feature} -> 查看目标入口 -> 校验入口位置和同级并列关系"
        elif "百度网盘" in str(point):
            scenario_name = f"{feature}百度网盘入口展示与点击验证"
            business_path = f"进入{feature} -> 查看目标入口 -> 点击入口 -> 校验进入后续流程"
        else:
            scenario_name = f"{feature}主流程验证"
            business_path = f"进入{feature} -> 完成需求主流程"
        scenarios.append({
            "feature": feature,
            "scenario": scenario_name,
            "type": "正常流程",
            "requirement_point": point,
            "business_path": business_path,
            "priority": "P0" if index <= 3 else "P1",
            "automation_feasible": True,
            "source": "local_fallback_after_ai_timeout",
            "fallback_reason": error,
        })
    return scenarios


BAIDU_NETDISK_POST_CLICK_WAIT = (
    "等待百度网盘授权页、登录页、文件选择页、空状态页或提示页打开，"
    "页面出现返回、搜索、确定、暂无数据、文件列表任一稳定信号"
)
BAIDU_NETDISK_POST_CLICK_ASSERT = (
    "点击百度网盘入口后进入百度网盘相关页面或出现可识别提示，"
    "未白屏、未闪退、未停留在原入口页"
)

FALLBACK_APP_HOME_HINTS = {
    "com.xbxxhz.box": "底部「首页」、基础打印、文档打印、照片打印或扫描复印等入口",
    "com.kfb.model": "底部导航、AI建模、图片建模、模型库或课程等入口",
}


def _known_task_apps():
    try:
        from task_server.services import sonic_service
        return sonic_service.sonic_notify_known_apps()
    except Exception:
        return [
            {"package": "com.xbxxhz.box", "name": "小白学习打印", "aliases": ["小白学习"]},
            {"package": "com.kfb.model", "name": "3D 打印", "aliases": ["智小白3D"]},
        ]


def _fallback_app_context(title="", module="", app_package="", app_name=""):
    """Resolve current app display context without hardcoding it in case steps."""
    package = str(app_package or "").strip()
    name = str(app_name or "").strip()
    scope = " ".join(normalize_text_list([title, module])).lower()
    apps = [item for item in _known_task_apps() if isinstance(item, dict)]
    if not package:
        for app in apps:
            terms = normalize_text_list([app.get("name"), app.get("package"), app.get("aliases")])
            if any(term and str(term).lower() in scope for term in terms):
                package = str(app.get("package") or "").strip()
                name = name or str(app.get("name") or "").strip()
                break
    if not package and any(term in scope for term in ("基础打印", "文档打印", "照片打印", "证件照", "扫描复印", "复印扫描", "百度网盘", "小白学习")):
        package = "com.xbxxhz.box"
    if not package and any(term in scope for term in ("3d", "ai建模", "图片建模", "文字建模", "语音创作", "标牌", "印章")):
        package = "com.kfb.model"
    if not name:
        for app in apps:
            if package and str(app.get("package") or "").strip() == package:
                name = str(app.get("name") or "").strip()
                break
    return {
        "app_package": package,
        "app_name": name or "当前 App",
        "home_hint": FALLBACK_APP_HOME_HINTS.get(package, "底部「首页」或当前 App 首页核心入口"),
    }


def _fallback_home_wait(app_context):
    app_context = app_context if isinstance(app_context, dict) else {}
    app_name = str(app_context.get("app_name") or "当前 App").strip()
    home_hint = str(app_context.get("home_hint") or "底部「首页」或当前 App 首页核心入口").strip()
    return f"启动 App，并等待{app_name}首页加载完成，能看到{home_hint}"


def _fallback_baidu_feature_kind(scenario):
    scenario = scenario if isinstance(scenario, dict) else {}
    text = " ".join(normalize_text_list([
        scenario.get("feature"),
        scenario.get("scenario"),
        scenario.get("requirement_point"),
        scenario.get("business_path"),
    ]))
    if "文档打印" in text:
        return "document"
    if any(term in text for term in ("照片拼版", "图片拼版")):
        return "photo_collage"
    if "智能证件照" in text:
        return "smart_id_photo"
    if any(term in text for term in ("证件照", "一寸照", "1寸")):
        return "id_photo"
    if any(term in text for term in ("普通照片", "照片打印", "5寸照片")):
        return "photo"
    if any(term in text for term in ("扫描复印", "复印扫描", "扫描仪扫描")):
        return "scan"
    return ""


def _fallback_steps_for_scenario(scenario, app_context=None):
    """生成保守、低断言密度的自动化步骤。"""
    feature = first_non_empty((scenario or {}).get("feature"), "目标功能")
    point = str((scenario or {}).get("requirement_point") or "")
    home_wait = _fallback_home_wait(app_context)
    if "百度网盘" in point:
        feature_text = str(feature or "")
        feature_kind = _fallback_baidu_feature_kind(scenario)
        display_only = _baidu_netdisk_point_is_display_only(point)
        steps = [
            home_wait,
            "如当前不在首页，返回或点击底部「首页」回到首页",
        ]
        assertion = f"{feature_text or feature}页面展示「百度网盘」入口，且入口与同级导入方式并列显示"
        if feature_kind == "document":
            steps.extend([
                "点击首页或底部导航中名称为「文档打印」的入口",
                "等待文档打印页面或文档导入入口区域加载完成，并看到「本地导入」「相册导入」「微信导入」或「本地文档」入口",
                "等待「百度网盘」入口可见",
            ])
            assertion = "文档打印页面展示「百度网盘」入口，入口与「本地文档」同级且位于其后"
        elif feature_kind in ("photo", "id_photo", "smart_id_photo", "photo_collage"):
            steps.extend([
                "点击首页或底部导航中名称为「照片打印」的入口",
                "等待照片打印页面加载完成，并看到普通照片、证件照或照片拼版入口",
            ])
            if feature_kind == "photo":
                steps.append("点击名称为「5寸照片」的普通照片打印入口")
                target_page = "普通照片打印"
            elif feature_kind == "smart_id_photo":
                steps.append("点击名称为「智能证件照」的入口")
                target_page = "智能证件照"
            elif feature_kind == "id_photo":
                steps.append("点击名称包含「证件照」或「一寸照」文字的入口")
                target_page = "证件照"
            else:
                steps.append("点击名称为「照片拼版」或「图片拼版」的入口")
                target_page = "照片拼版"
            steps.extend([
                f"等待{target_page}导入页面加载完成，并看到导入方式区域",
                "等待「百度网盘」入口可见",
            ])
            assertion = f"{target_page}导入页面展示「百度网盘」入口，且与其他导入方式同级显示"
        elif feature_kind == "scan":
            steps.extend([
                "点击首页中名称为「扫描复印」或「扫描仪扫描」的入口",
                "等待扫描复印页面或复印扫描导入页面加载完成",
                "等待「百度网盘」入口可见",
            ])
            assertion = "扫描复印页面展示「百度网盘」入口，且与其他导入方式同级显示"
        else:
            steps.extend([
                f"点击首页中名称为「{feature_text or feature}」的入口",
                f"等待{feature_text or feature}页面加载完成",
                "等待「百度网盘」入口可见",
            ])
        if display_only:
            return steps, [assertion]
        steps.extend([
            "点击「百度网盘」入口",
            BAIDU_NETDISK_POST_CLICK_WAIT,
        ])
        return steps, [BAIDU_NETDISK_POST_CLICK_ASSERT]
    return [
        home_wait,
        f"进入{feature}相关页面",
        f"等待{feature}页面加载完成",
        "执行当前需求主流程操作",
    ], [
        f"{feature}主流程可完成，页面没有异常弹窗、空白页或加载失败"
    ]


def _classify_automation_filter_failure(error):
    """Classify the failed AI boundary without treating every failure as a timeout."""
    chain = []
    current = error if isinstance(error, BaseException) else None
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = str(error or "")
    lowered = text.lower()
    if "json 语法修复" in lowered or "json syntax repair" in lowered:
        return "invalid_json"
    if any(isinstance(item, (TimeoutError, socket.timeout)) for item in chain) or any(
        marker in lowered for marker in ("timeout", "timed out", "超时", "超过截止时间")
    ):
        return "timeout"
    if "ai gateway 返回非 json 响应" in lowered:
        return "failure"
    if any(isinstance(item, json.JSONDecodeError) for item in chain) or any(
        marker in lowered
        for marker in (
            "jsondecodeerror",
            "expecting ',' delimiter",
            "expecting ':' delimiter",
            "expecting property name",
            "expecting value",
            "unterminated string",
            "invalid control character",
            "extra data",
        )
    ):
        return "invalid_json"
    return "failure"


def _automation_filter_fallback_copy(failure_type):
    failure_type = str(failure_type or "failure").strip().lower()
    if failure_type == "timeout":
        return (
            "local_fallback_after_ai_timeout",
            "AI skill 超时后按需求点生成的保守用例；仅保留主流程和低密度断言。",
        )
    if failure_type == "invalid_json":
        return (
            "local_fallback_after_ai_invalid_json",
            "AI skill 返回的 JSON 语法无效且同模型有界修复失败后，按需求点生成保守用例；仅供评审。",
        )
    return (
        "local_fallback_after_ai_failure",
        "AI skill 调用失败后按需求点生成的保守用例；仅保留主流程和低密度断言。",
    )


def _fallback_automation_filter_from_scenarios(
    title,
    module,
    analysis,
    scenarios,
    targets=None,
    error="",
    app_package="",
    app_name="",
    failure_type="",
):
    """AI 自动化筛选失败时，生成继续走静态检查但不能自动执行的保守用例。"""
    targets = targets or generation_volume_targets(analysis, mode="full")
    scenarios = [item for item in (scenarios or []) if isinstance(item, dict)]
    app_context = _fallback_app_context(title, module, app_package=app_package, app_name=app_name)
    failure_type = str(failure_type or "").strip() or (
        _classify_automation_filter_failure(error) if error else "timeout"
    )
    fallback_source, fallback_reason = _automation_filter_fallback_copy(failure_type)
    max_cases = max(1, min(
        safe_int(targets.get("target_automation_cases"), len(scenarios) or 1),
        safe_int(targets.get("max_cases"), len(scenarios) or 1),
        len(scenarios) or 1,
    ))
    smoke_limit = _smoke_first_batch_limit(targets)
    cases = []
    for index, scenario in enumerate(scenarios[:max_cases], start=1):
        steps, assertions = _fallback_steps_for_scenario(scenario, app_context=app_context)
        feature = first_non_empty(scenario.get("feature"), f"需求{index}")
        cases.append({
            "case_id": f"TC-{index:03d}",
            "title": first_non_empty(scenario.get("scenario"), f"{feature}主流程验证"),
            "priority": first_non_empty(scenario.get("priority"), "P0" if index <= smoke_limit else "P1"),
            "flag": ["冒烟"] if index <= smoke_limit else [],
            "smoke": index <= smoke_limit,
            "scenario": first_non_empty(scenario.get("scenario"), feature),
            "coverage": scenario_requirement_point(scenario),
            "requirement_point": scenario_requirement_point(scenario),
            "business_path": first_non_empty(scenario.get("business_path"), f"进入{feature} -> 完成需求主流程"),
            "preconditions": [f"已安装并登录{app_context.get('app_name') or '当前 App'}", "网络正常"],
            "steps": steps,
            "assertions": assertions,
            "expected_result": assertions[0] if assertions else "",
            "automation_reason": fallback_reason,
            "executionLevel": "needs_review" if error else "executable",
            "source": fallback_source,
        })

    manual_cases = []
    if "埋点" in _analysis_text_blob(analysis):
        manual_cases.append({
            "case_id": "MANUAL-TRACKING-001",
            "title": "百度网盘入口埋点上报校验",
            "reason": "埋点需要日志或埋点平台核对，默认不直接创建 Runner 任务。",
            "coverage": "百度网盘入口点击埋点",
            "executionLevel": "manual",
        })

    return {
        "cases": cases,
        "manual_cases": manual_cases,
        "review": {
            "automation_filter_skill": fallback_source,
            "fallback_source": fallback_source,
            "fallback_failure_type": failure_type,
            "fallback_reason": str(error or ""),
            "generation_targets": targets,
            "actual_case_count": len(cases),
            "manual_case_count": len(manual_cases),
            "assertion_density": "每条自动化用例保留 1 条最终业务结果断言。",
        }
    }


def _compact_analysis_for_automation_filter(analysis):
    """Keep only fields needed to decide UI automation suitability."""
    analysis = analysis if isinstance(analysis, dict) else {}
    result = {}
    list_limits = {
        "business_goals": 10,
        "entry_points": 12,
        "state_assumptions": 8,
        "data_assumptions": 8,
        "visible_outcomes": 12,
        "risks": 10,
        "requirement_points": 16,
        "missing_inputs": 10,
        "blockers": 8,
        "assumptions": 10,
    }
    for key, limit in list_limits.items():
        values = normalize_text_list(analysis.get(key))[:limit]
        if values:
            result[key] = values
    for key in ("confidence", "readiness_score", "readiness_level", "source_quality"):
        if key in analysis:
            result[key] = copy.deepcopy(analysis.get(key))
    return result


def _compact_scenario_for_automation_filter(scenario):
    scenario = scenario if isinstance(scenario, dict) else {}
    result = {}
    for key in (
        "feature", "requirement_point", "requirementPoint", "scenario", "type",
        "business_path", "businessPath", "expected", "expected_result",
        "automation_suitable", "automationSuitable", "reason", "priority", "risk",
    ):
        value = scenario.get(key)
        if value not in (None, "", []):
            result[key] = copy.deepcopy(value)
    for key in ("preconditions", "steps", "assertions", "data_requirements", "tags"):
        values = normalize_text_list(scenario.get(key))[:8]
        if values:
            result[key] = values
    return result


def call_skill_scenario_designer(
    title,
    module,
    analysis,
    yaml_reference_context="",
    mode="full",
    model_config=None,
    targets=None,
    runtime_trace=None,
):
    """调用 AI skill: scenario_designer。"""
    targets = dict(targets) if isinstance(targets, dict) else generation_volume_targets(analysis, mode=mode)
    payload = {
        "title": title,
        "module": module,
        "analysis": analysis,
        "generation_targets": targets,
        "yaml_reference_context": yaml_reference_context,
    }
    try:
        result = run_ai_skill(
            "scenario_designer",
            payload,
            timeout=AI_SCENARIO_DESIGNER_TIMEOUT_SECONDS,
            respect_global_timeout=False,
            retry_count=0,
            model_config=model_config,
            runtime_trace=runtime_trace,
        )
        scenarios = result.get("scenarios") or []
    except Exception as exc:
        return _fallback_scenarios_from_analysis(title, module, analysis, targets=targets, error=str(exc))
    if not isinstance(scenarios, list) or not scenarios:
        return _fallback_scenarios_from_analysis(title, module, analysis, targets=targets, error="scenario_designer 未产出场景")
    return scenarios


def call_skill_automation_filter(
    title,
    module,
    analysis,
    scenarios,
    yaml_reference_context="",
    mode="full",
    model_config=None,
    app_package="",
    app_name="",
    targets=None,
    runtime_trace=None,
):
    """调用 AI skill: automation_filter。"""
    targets = dict(targets) if isinstance(targets, dict) else generation_volume_targets(analysis, mode=mode)
    compact_analysis = _compact_analysis_for_automation_filter(analysis)
    compact_scenarios = [
        _compact_scenario_for_automation_filter(item)
        for item in (scenarios or [])
        if isinstance(item, dict)
    ]
    compact_yaml_reference = str(yaml_reference_context or "")[:6000]
    input_review = {
        "analysis_chars": len(json.dumps(compact_analysis, ensure_ascii=False)),
        "scenario_count": len(compact_scenarios),
        "scenario_chars": len(json.dumps(compact_scenarios, ensure_ascii=False)),
        "yaml_reference_chars": len(compact_yaml_reference),
        "target_automation_cases": safe_int(targets.get("target_automation_cases"), 0),
        "timeout_seconds": AI_AUTOMATION_FILTER_TIMEOUT_SECONDS,
        "max_output_tokens": AI_AUTOMATION_FILTER_MAX_TOKENS,
        "rule": "automation_filter 只接收自动化适用性判断所需字段和短版 Top3 基线，完整需求分析仍保留在最终 payload。",
    }
    payload = {
        "title": title,
        "module": module,
        "analysis": compact_analysis,
        "scenarios": compact_scenarios,
        "generation_targets": targets,
        "yaml_reference_context": compact_yaml_reference,
        "automation_rules": {
            "allowed_actions": ["点击", "输入", "等待", "断言", "返回", "滚动", "处理弹窗", "回到首页"],
            "manual_by_default": ["真实支付", "删除", "切账号", "清数据", "后台造数", "接口 Mock", "系统权限预置", "断网/弱网", "排队/并发状态", "真实外设", "纯设计稿对比"],
            "assertion_required": True,
            "assertion_density": "每条自动化用例只写 1 条最终业务结果断言；过程校验写入 steps 的等待/检查动作，不要把每个验收点都塞进 assertions",
            "scope_guard": "每条 cases 必须映射当前 analysis.requirement_points/business_goals；需求未提到的历史记录、缓存、慢加载、超时、干扰、重复点击、防抖、旧入口不存在等扩展场景只能进入 manual_cases/needs_review，不得作为自动执行 YAML。",
            "smoke_selection": "smoke=true 必须基于当前需求主链显式筛选；不要把 P1、入口、展示、基础等规则候选自动当成冒烟。冒烟候选池小需求通常 3 条以内，中大需求最多 5-8 条；Runner 首批自动下发最多 3 条。"
        }
    }
    def fallback(error):
        failure_type = _classify_automation_filter_failure(error)
        result = _fallback_automation_filter_from_scenarios(
            title,
            module,
            analysis,
            scenarios,
            targets=targets,
            error=str(error),
            app_package=app_package,
            app_name=app_name,
            failure_type=failure_type,
        )
        result.setdefault("review", {})["automation_filter_input"] = input_review
        return result

    try:
        result = run_ai_skill(
            "automation_filter",
            payload,
            timeout=AI_AUTOMATION_FILTER_TIMEOUT_SECONDS,
            respect_global_timeout=False,
            retry_count=0,
            model_config=model_config,
            runtime_trace=runtime_trace,
            repair_invalid_json=True,
            max_tokens=AI_AUTOMATION_FILTER_MAX_TOKENS,
        )
        cases = result.get("cases") or []
    except Exception as exc:
        return fallback(exc)
    if not isinstance(cases, list) or not cases:
        return fallback("automation_filter 未产出自动化用例")
    review = result.get("review") or {}
    review["automation_filter_skill"] = "automation_filter.v1"
    review["automation_filter_input"] = input_review
    review["generation_targets"] = targets
    review["actual_case_count"] = len(cases)
    return {
        "cases": cases,
        "manual_cases": result.get("manual_cases") or [],
        "review": review
    }


def _smoke_case_id(case, index):
    """Return a stable case id for smoke selection and back-fill when missing."""
    if not isinstance(case, dict):
        return f"TC-{index:03d}"
    case_id = first_non_empty(case.get("case_id"), case.get("caseId"), case.get("id"))
    if not case_id:
        case_id = f"TC-{index:03d}"
        case["case_id"] = case_id
    return str(case_id).strip()


def _compact_case_for_smoke_selector(case, index):
    """Build a compact, model-friendly view of a generated case."""
    case = case if isinstance(case, dict) else {}
    return {
        "case_id": _smoke_case_id(case, index),
        "title": first_non_empty(case.get("title"), case.get("name")),
        "priority": case_priority(case),
        "current_smoke": is_smoke_case(case),
        "scenario": first_non_empty(case.get("scenario"), case.get("scene")),
        "goal": first_non_empty(case.get("goal"), case.get("objective"), case.get("description")),
        "coverage": first_non_empty(case.get("coverage"), case.get("requirement_point"), case.get("requirementPoint")),
        "business_path": first_non_empty(case.get("business_path"), case.get("businessPath"), case.get("path")),
        "expected_result": first_non_empty(case.get("expected_result"), case.get("expectedResult"), case.get("expected")),
        "automation_reason": first_non_empty(case.get("automation_reason"), case.get("automationReason")),
        "data_requirements": first_non_empty(case.get("data_requirements"), case.get("dataRequirements"), case.get("test_data"), case.get("testData")),
        "preconditions": normalize_text_list(case.get("preconditions") or case.get("precondition"))[:4],
        "steps": normalize_text_list(case.get("steps"))[:8],
        "assertions": normalize_text_list(case.get("assertions") or case.get("expects") or case.get("expected"))[:4],
        "tags": case_tags(case)[:8],
    }


def _smoke_selection_target_limit(targets):
    limit = safe_int((targets or {}).get("smoke_cases"), 3)
    return max(1, min(8, limit or 3))


def _smoke_first_batch_limit(targets):
    """首批 Runner 冒烟固定最多 3 条；完整冒烟池仍按需求规模保留。"""
    return max(1, min(3, _smoke_selection_target_limit(targets)))


def _local_smoke_case_score(case, index, analysis=None, yaml_reference_context=""):
    """本地规则筛选冒烟，避免额外模型调用和关键词粗暴提升。"""
    case = case if isinstance(case, dict) else {}
    analysis = analysis if isinstance(analysis, dict) else {}
    text = " ".join(normalize_text_list([
        case.get("title"),
        case.get("name"),
        case.get("scenario"),
        case.get("goal"),
        case.get("coverage"),
        case.get("requirement_point"),
        case.get("business_path"),
        case.get("expected_result"),
        case.get("automation_reason"),
        case.get("baseline_match"),
        case.get("baselineMatch"),
        case.get("matched_baseline"),
        case.get("matchedBaseline"),
        case.get("yaml_reference"),
        case.get("yamlReference"),
        case.get("steps"),
        case.get("assertions"),
        case.get("tags"),
    ])).lower()
    score = max(0, 100 - index)
    priority = case_priority(case).upper()
    score += {"P0": 40, "HIGH": 35, "P1": 24, "P2": 10}.get(priority, 0)
    if is_smoke_case(case):
        score += 20
    if any(str(case.get(key) or "").strip() for key in ("business_path", "businessPath", "start_page", "startPage")):
        score += 18
    if any(word in text for word in ("主链", "主流程", "正常", "入口", "核心", "完成", "成功")):
        score += 16
    baseline_fields = (
        case.get("baselineMatched"),
        case.get("baseline_matched"),
        case.get("baseline_match"),
        case.get("baselineMatch"),
        case.get("matched_baseline"),
        case.get("matchedBaseline"),
        case.get("yaml_reference"),
        case.get("yamlReference"),
    )
    if any(bool(item) for item in baseline_fields) or (yaml_reference_context and any(word in text for word in ("基线", "参考", "稳定", "可执行"))):
        score += 10
    steps = normalize_text_list(case.get("steps"))
    assertions = normalize_text_list(case.get("assertions") or case.get("expects") or case.get("expected"))
    if 2 <= len(steps) <= 8:
        score += 12
    elif len(steps) > 12:
        score -= 18
    if assertions or str(case.get("expected_result") or "").strip():
        score += 10
    negative_terms = (
        "历史", "干扰", "异常", "失败", "超时", "慢加载", "骨架屏", "空态", "防抖",
        "重复点击", "权限", "断网", "弱网", "删除", "支付", "mock", "后台造数", "外部",
    )
    if any(term in text for term in negative_terms):
        score -= 45
    requirement_points = normalize_text_list(analysis.get("requirement_points"))
    if requirement_points:
        matched = sum(1 for point in requirement_points if point and str(point).lower() in text)
        if matched:
            score += min(24, matched * 8)
        elif index > 1:
            score -= 12
    return score


def _local_smoke_selector_result(title, module, analysis, cases, targets, yaml_reference_context=""):
    """本地冒烟选择：首批最多 3 条，优先主链/P0/基线稳定写法。"""
    ranked = []
    for index, case in enumerate(cases or [], start=1):
        if not isinstance(case, dict):
            continue
        case_id = _smoke_case_id(case, index)
        ranked.append((
            _local_smoke_case_score(case, index, analysis=analysis, yaml_reference_context=yaml_reference_context),
            -index,
            case_id,
        ))
    ranked.sort(reverse=True)
    limit = _smoke_first_batch_limit(targets)
    selected = [case_id for score, _idx, case_id in ranked if score > 0][:limit]
    if not selected and ranked:
        selected = [ranked[0][2]]
    return _normalize_smoke_selector_result({
        "smoke_case_ids": selected,
        "review": {
            "normal_chain_covered": bool(selected),
            "selection_reason": "本地规则按主业务链、P0、基线依据、步骤稳定性和执行评分选择首批冒烟。",
            "missing_normal_chain_reason": "" if selected else "没有可用于首批执行的自动化用例。",
            "rejected_case_ids": [],
            "scored_candidates": [
                {"case_id": case_id, "score": score}
                for score, _idx, case_id in ranked[:8]
            ],
        },
    }, cases, targets, source="local_smoke_gate.v1")


def _normalize_smoke_selector_result(result, cases, targets, *, source):
    """Normalize and filter smoke selector output against existing cases."""
    cases = [case for case in (cases or []) if isinstance(case, dict)]
    id_order = [_smoke_case_id(case, index) for index, case in enumerate(cases, start=1)]
    valid = set(id_order)
    limit = _smoke_first_batch_limit(targets)
    selected: List[str] = []
    invalid: List[str] = []
    for raw in normalize_text_list((result or {}).get("smoke_case_ids")):
        case_id = str(raw or "").strip()
        if not case_id:
            continue
        if case_id in valid and case_id not in selected:
            selected.append(case_id)
        elif case_id not in valid and case_id not in invalid:
            invalid.append(case_id)
    selected = selected[:limit]
    review = dict((result or {}).get("review") or {})
    review.update({
        "selector_source": source,
        "selected_case_ids": selected,
        "selected_count": len(selected),
        "target_smoke_cases": limit,
        "smoke_pool_limit": _smoke_selection_target_limit(targets),
        "invalid_case_ids": invalid,
        "rule": "冒烟可由 AI 推荐，但平台最终校验 case id、数量和首批上限；AI 不可用时回退本地规则。首批最多 3 条。",
    })
    return {
        "smoke_case_ids": selected,
        "review": review,
    }


def _fallback_smoke_selection_from_existing(cases, targets, error=""):
    """Fallback to explicit AI/user smoke marks only; never infer from keywords."""
    selected: List[str] = []
    for index, case in enumerate(cases or [], start=1):
        if isinstance(case, dict) and is_smoke_case(case):
            selected.append(_smoke_case_id(case, index))
    selected = selected[:_smoke_selection_target_limit(targets)]
    return _normalize_smoke_selector_result({
        "smoke_case_ids": selected,
        "review": {
            "normal_chain_covered": bool(selected),
            "selection_reason": "smoke_selector 不可用时，仅沿用 automation_filter 已明确标记的冒烟；没有再用关键词/P0/P1 推断。",
            "missing_normal_chain_reason": "" if selected else "未获得 AI 二次筛选结果，且生成用例中没有明确 smoke=true 的候选。",
            "selector_error": error,
            "rejected_case_ids": [],
        }
    }, cases, targets, source="fallback_explicit_smoke_only")


def _set_case_smoke(case, enabled):
    """Set smoke flag and keep visible tags consistent."""
    if not isinstance(case, dict):
        return
    case["smoke"] = bool(enabled)
    tags = [tag for tag in case_tags(case) if "冒烟" not in tag and "smoke" not in tag.lower()]
    flags = [flag for flag in normalize_text_list(case.get("flags") or []) if "冒烟" not in flag and "smoke" not in flag.lower()]
    if enabled:
        tags.append("冒烟")
    case["tags"] = tags
    if flags:
        case["flags"] = flags
    elif "flags" in case:
        case.pop("flags", None)
    if case.get("flag") and ("冒烟" in str(case.get("flag")) or "smoke" in str(case.get("flag")).lower()):
        case.pop("flag", None)


def apply_smoke_selection_to_cases(cases, selection, targets):
    """Apply final AI smoke selection to cases and clear previous smoke drift."""
    cases = [case for case in (cases or []) if isinstance(case, dict)]
    normalized = _normalize_smoke_selector_result(selection or {}, cases, targets, source=((selection or {}).get("review") or {}).get("selector_source") or "smoke_selector")
    selected = set(normalized.get("smoke_case_ids") or [])
    for index, case in enumerate(cases, start=1):
        case_id = _smoke_case_id(case, index)
        _set_case_smoke(case, case_id in selected)
        if case_id in selected:
            case["smoke_selection_rank"] = (normalized.get("smoke_case_ids") or []).index(case_id) + 1
        else:
            case.pop("smoke_selection_rank", None)
    return cases, normalized.get("review") or {}


def call_skill_smoke_selector(
    title,
    module,
    analysis,
    scenarios,
    cases,
    manual_cases=None,
    yaml_reference_context="",
    mode="full",
    model_config=None,
    targets=None,
    runtime_trace=None,
):
    """AI 推荐冒烟 + 平台准入校验；失败时回退本地规则。"""
    targets = dict(targets) if isinstance(targets, dict) else generation_volume_targets(analysis, mode=mode)
    cases = [case for case in (cases or []) if isinstance(case, dict)]
    if AI_SMOKE_SELECTOR_ENABLED and cases:
        compact_cases = [_compact_case_for_smoke_selector(case, index) for index, case in enumerate(cases, start=1)]
        payload = {
            "title": title,
            "module": module,
            "analysis": analysis if isinstance(analysis, dict) else {},
            "scenarios": scenarios or [],
            "cases": compact_cases,
            "manual_cases": manual_cases or [],
            "generation_targets": targets,
            "yaml_reference_context": str(yaml_reference_context or "")[:4000],
            "selection_rules": {
                "first_batch_limit": _smoke_first_batch_limit(targets),
                "must_cover_normal_chain": True,
                "prefer": ["P0/P1", "主业务链", "基线依据", "短步骤", "可独立执行", "低外部依赖"],
                "avoid": ["异常边界", "外部授权", "系统文件选择器", "真实支付/删除", "弱网/超时", "历史缓存", "强账号数据"],
            },
        }
        try:
            result = run_ai_skill(
                "smoke_selector",
                payload,
                timeout=AI_SMOKE_SELECTOR_TIMEOUT_SECONDS,
                respect_global_timeout=False,
                retry_count=0,
                model_config=model_config,
                runtime_trace=runtime_trace,
            )
            normalized = _normalize_smoke_selector_result(result, cases, targets, source="smoke_selector.v1")
            if normalized.get("smoke_case_ids"):
                return normalized
            local = _local_smoke_selector_result(
                title,
                module,
                analysis if isinstance(analysis, dict) else {},
                cases,
                targets,
                yaml_reference_context=yaml_reference_context,
            )
            review = local.setdefault("review", {})
            review["selector_source"] = "local_smoke_gate_after_empty_ai"
            review["ai_smoke_selector_empty"] = True
            return local
        except Exception as exc:
            local = _local_smoke_selector_result(
                title,
                module,
                analysis if isinstance(analysis, dict) else {},
                cases,
                targets,
                yaml_reference_context=yaml_reference_context,
            )
            review = local.setdefault("review", {})
            review["selector_source"] = "local_smoke_gate_after_ai_error"
            review["ai_smoke_selector_error"] = str(exc)
            return local
    return _local_smoke_selector_result(
        title,
        module,
        analysis if isinstance(analysis, dict) else {},
        cases,
        targets,
        yaml_reference_context=yaml_reference_context,
    )


def select_smoke_cases_for_payload(
    title,
    module,
    payload,
    mode="full",
    yaml_reference_context="",
    model_config=None,
    targets=None,
    runtime_trace=None,
):
    """Run final smoke selection on a normalized cases payload."""
    normalized = normalize_cases_payload(payload)
    cases = normalized.get("cases") or []
    eligible_cases = [
        case for case in cases
        if str(case.get("executionLevel") or case.get("level") or "executable").strip().lower() == "executable"
    ]
    targets = dict(targets) if isinstance(targets, dict) else generation_volume_targets(normalized.get("analysis") or {}, mode=mode)
    try:
        selection = call_skill_smoke_selector(
            title or normalized.get("title"),
            module or normalized.get("module"),
            normalized.get("analysis") or {},
            normalized.get("scenarios") or [],
            eligible_cases,
            normalized.get("manual_cases") or [],
            yaml_reference_context=yaml_reference_context,
            mode=mode,
            model_config=model_config,
            targets=targets,
            runtime_trace=runtime_trace,
        )
    except Exception as exc:
        selection = _fallback_smoke_selection_from_existing(eligible_cases, targets, error=str(exc))
    cases, smoke_review = apply_smoke_selection_to_cases(cases, selection, targets)
    normalized["cases"] = cases
    review = normalized.setdefault("review", {})
    review["smoke_selector_skill"] = smoke_review.get("selector_source") or "local_smoke_gate.v1"
    review["smoke_selection"] = smoke_review
    review["smoke_case_ids"] = smoke_review.get("selected_case_ids") or []
    review["smoke_eligible_case_count"] = len(eligible_cases)
    if isinstance(runtime_trace, dict):
        review.setdefault("skill_model_traces", {})["smoke_selector"] = _model_config_trace(
            model_config,
            runtime_trace,
        )
    return normalized


def build_cases_payload_from_skills(
    title,
    module,
    text_assets,
    mode="full",
    model_config=None,
    app_package="",
    app_name="",
    allow_entry_visibility_fast_path=True,
    generation_scope_plan=None,
    require_ai_core=False,
    requirement_contract=None,
):
    """通过 AI skills pipeline 生成用例 payload。"""
    mode = str(mode or "full").strip().lower()
    yaml_reference_context = extract_yaml_reference_context(text_assets)
    skill_model_traces = {}
    if allow_entry_visibility_fast_path and _should_fast_path_baidu_entry_visibility(title, module, text_assets):
        analysis = _fallback_requirement_analysis(
            title,
            module,
            text_assets,
            error="deterministic_baidu_entry_visibility_fast_path",
        )
        targets = generation_targets_for_scope(analysis, mode=mode, scope_plan=generation_scope_plan)
        scenarios = _fallback_scenarios_from_analysis(
            title,
            module,
            analysis,
            targets=targets,
            error="deterministic_baidu_entry_visibility_fast_path",
        )
        filtered = _fallback_automation_filter_from_scenarios(
            title,
            module,
            analysis,
            scenarios,
            targets=targets,
            error="",
            app_package=app_package,
            app_name=app_name,
        )
        payload = {
            "title": title,
            "module": module,
            "analysis": analysis,
            "scenarios": scenarios,
            "cases": filtered.get("cases") or [],
            "manual_cases": filtered.get("manual_cases") or [],
            "review": filtered.get("review") or {},
        }
        review = payload.setdefault("review", {})
        review["generation_mode"] = mode
        review["generation_targets"] = targets
        review["skill_pipeline"] = "deterministic_baidu_entry_visibility.v1 -> smoke_selector.v1/platform_gate"
        review["fast_path_reason"] = "入口展示类需求先生成稳定短链路，AI/Figma 视觉校准作为后续补充，不阻塞首批冒烟"
        review["yaml_reference_context_used_by_skills"] = bool(yaml_reference_context)
        review["requirement_readiness"] = {
            "score": analysis.get("readiness_score"),
            "level": analysis.get("readiness_level"),
            "confidence": analysis.get("confidence"),
            "missing_inputs": analysis.get("missing_inputs") or [],
            "blockers": analysis.get("blockers") or [],
            "questions": analysis.get("questions") or [],
        }
        normalized = normalize_cases_payload(payload)
        local_selection = _local_smoke_selector_result(
            title,
            module,
            normalized.get("analysis") or {},
            normalized.get("cases") or [],
            targets,
            yaml_reference_context=yaml_reference_context,
        )
        selected_cases, smoke_review = apply_smoke_selection_to_cases(
            normalized.get("cases") or [],
            local_selection,
            targets,
        )
        normalized["cases"] = selected_cases
        review = normalized.setdefault("review", {})
        review["smoke_selector_skill"] = "local_smoke_gate.v1"
        review["smoke_selection"] = smoke_review
        review["smoke_case_ids"] = smoke_review.get("selected_case_ids") or []
        review["skill_pipeline"] = "deterministic_baidu_entry_visibility.v1 -> local_smoke_gate.v1"
        validate_ai_skill_output("cases_payload", normalized)
        return normalized
    requirement_runtime_trace = {}
    analysis = call_skill_requirement_analyzer(
        title,
        module,
        text_assets,
        model_config=model_config,
        requirement_contract=requirement_contract,
        runtime_trace=requirement_runtime_trace,
    )
    skill_model_traces["requirement_analyzer"] = _model_config_trace(
        model_config,
        requirement_runtime_trace,
    )
    targets = generation_targets_for_scope(analysis, mode=mode, scope_plan=generation_scope_plan)
    if require_ai_core and analysis.get("fallback_reason"):
        return {
            "title": title,
            "module": module,
            "analysis": analysis,
            "scenarios": [],
            "cases": [],
            "manual_cases": [],
            "review": {
                "generation_mode": mode,
                "generation_targets": targets,
                "skill_pipeline": "requirement_analyzer.v1",
                "skill_model_traces": skill_model_traces,
                "core_ai_failure": {
                    "stage": "requirement_analyzer",
                    "reason": str(analysis.get("fallback_reason") or "requirement_analyzer 未产出 AI 结果")[:500],
                },
                "downstream_skipped": ["scenario_designer", "automation_filter", "smoke_selector", "visual_grounder"],
            },
        }
    if yaml_reference_context:
        analysis["yaml_reference_context_available"] = True
        analysis["yaml_reference_rule"] = (
            "后续场景设计和自动化筛选必须参考平台已有 YAML 步骤经验；只学习动作组织、等待策略和断言密度，不复制历史业务断言。"
        )
    scenario_runtime_trace = {}
    scenarios = call_skill_scenario_designer(
        title,
        module,
        analysis,
        yaml_reference_context=yaml_reference_context,
        mode=mode,
        model_config=model_config,
        targets=targets,
        runtime_trace=scenario_runtime_trace,
    )
    skill_model_traces["scenario_designer"] = _model_config_trace(
        model_config,
        scenario_runtime_trace,
    )
    scenario_fallback = next((
        item for item in scenarios
        if isinstance(item, dict) and (
            str(item.get("source") or "").startswith("local_fallback")
            or item.get("fallback_reason")
        )
    ), None)
    if require_ai_core and scenario_fallback:
        reason = str(scenario_fallback.get("fallback_reason") or "scenario_designer 未产出 AI 结果")
        return {
            "title": title,
            "module": module,
            "analysis": analysis,
            "scenarios": scenarios,
            "cases": [],
            "manual_cases": [],
            "review": {
                "generation_mode": mode,
                "generation_targets": targets,
                "skill_pipeline": "requirement_analyzer.v1 -> scenario_designer.v1",
                "skill_model_traces": skill_model_traces,
                "yaml_reference_context_used_by_skills": bool(yaml_reference_context),
                "core_ai_failure": {"stage": "scenario_designer", "reason": reason[:500]},
                "downstream_skipped": ["automation_filter", "smoke_selector", "visual_grounder"],
            },
        }
    automation_runtime_trace = {}
    filtered = call_skill_automation_filter(
        title,
        module,
        analysis,
        scenarios,
        yaml_reference_context=yaml_reference_context,
        mode=mode,
        model_config=model_config,
        app_package=app_package,
        app_name=app_name,
        targets=targets,
        runtime_trace=automation_runtime_trace,
    )
    skill_model_traces["automation_filter"] = _model_config_trace(
        model_config,
        automation_runtime_trace,
    )
    cases = filtered.get("cases") or []
    manual_cases = filtered.get("manual_cases") or []
    analysis["coverage_matrix"] = build_skill_coverage_matrix(analysis, scenarios, cases, manual_cases)
    payload = {
        "title": title,
        "module": module,
        "analysis": analysis,
        "scenarios": scenarios,
        "cases": cases,
        "manual_cases": manual_cases,
        "review": filtered.get("review") or {}
    }
    review = payload.setdefault("review", {})
    review["generation_mode"] = mode
    review["generation_targets"] = targets
    review["skill_pipeline"] = "requirement_analyzer.v1 -> scenario_designer.v1 -> automation_filter.v1"
    review["skill_model_traces"] = skill_model_traces
    review["yaml_reference_context_used_by_skills"] = bool(yaml_reference_context)
    if yaml_reference_context:
        review["yaml_reference_context_rule"] = "用例库参考已传入 scenario_designer 和 automation_filter，用于学习平台步骤组织和断言密度。"
    review["requirement_readiness"] = {
        "score": analysis.get("readiness_score"),
        "level": analysis.get("readiness_level"),
        "confidence": analysis.get("confidence"),
        "missing_inputs": analysis.get("missing_inputs") or [],
        "blockers": analysis.get("blockers") or [],
        "questions": analysis.get("questions") or [],
    }
    normalized = normalize_cases_payload(payload)
    smoke_runtime_trace = {}
    normalized = select_smoke_cases_for_payload(
        title,
        module,
        normalized,
        mode=mode,
        yaml_reference_context=yaml_reference_context,
        model_config=model_config,
        targets=targets,
        runtime_trace=smoke_runtime_trace,
    )
    review = normalized.setdefault("review", {})
    review["skill_pipeline"] = "requirement_analyzer.v1 -> scenario_designer.v1 -> automation_filter.v1 -> smoke_selector.v1/platform_gate"
    validate_ai_skill_output("cases_payload", normalized)
    return normalized


# ---------------------------------------------------------------------------
# AI decision skills used before YAML generation
# ---------------------------------------------------------------------------

def _model_config_trace(model_config, runtime_trace=None):
    model_config = model_config if isinstance(model_config, dict) else {}
    runtime_trace = runtime_trace if isinstance(runtime_trace, dict) else {}
    selected_provider_id = model_config.get("providerId") or model_config.get("provider") or ""
    selected_model = model_config.get("model") or model_config.get("modelName") or ""
    trace = {
        "selectedProviderId": selected_provider_id,
        "selectedModel": selected_model,
        "providerId": runtime_trace.get("providerId") or selected_provider_id,
        "model": runtime_trace.get("model") or selected_model,
        "fallbackUsed": bool(runtime_trace.get("fallbackUsed")),
        "fallbackIndex": safe_int(runtime_trace.get("fallbackIndex"), 0),
        "fallbackReason": str(runtime_trace.get("fallbackReason") or "")[:500],
        "source": runtime_trace.get("source") or "configured",
        "invoked": bool(runtime_trace),
        "strict": bool(AI_SKILLS_STRICT_MODEL),
    }
    if runtime_trace.get("error"):
        trace["error"] = str(runtime_trace.get("error"))[:500]
    if runtime_trace.get("imageCount") is not None:
        trace["imageCount"] = safe_int(runtime_trace.get("imageCount"), 0)
    if runtime_trace.get("finishReason") is not None:
        trace["finishReason"] = str(runtime_trace.get("finishReason") or "")
    if isinstance(runtime_trace.get("usage"), dict):
        trace["usage"] = copy.deepcopy(runtime_trace.get("usage"))
    if runtime_trace.get("jsonRepairAttempted") is not None:
        trace["jsonRepairAttempted"] = bool(runtime_trace.get("jsonRepairAttempted"))
        trace["jsonRepairSucceeded"] = bool(runtime_trace.get("jsonRepairSucceeded"))
    if isinstance(runtime_trace.get("jsonRepair"), dict):
        trace["jsonRepair"] = copy.deepcopy(runtime_trace.get("jsonRepair"))
    return trace


def _baseline_candidate_id(item, index=0):
    if not isinstance(item, dict):
        return f"base_{index + 1:03d}"
    raw = first_non_empty(item.get("id"), item.get("case_id"), item.get("file"), item.get("path"), item.get("title"))
    return clean_id(raw, f"base_{index + 1:03d}")


def _compact_baseline_candidate(item, index=0):
    item = item if isinstance(item, dict) else {}
    snippet = str(item.get("snippet") or "")
    start_page = str(item.get("startPage") or item.get("start_page") or "").strip()
    if not start_page and snippet:
        match = re.search(r"#\s*baseline\.start_page\s*:\s*(.+)", snippet)
        if match:
            start_page = str(match.group(1) or "").strip().strip("\"'")
    return {
        "id": _baseline_candidate_id(item, index),
        "title": item.get("title") or item.get("file") or "",
        "module": item.get("module") or "",
        "file": item.get("file") or "",
        "path": item.get("path") or item.get("baseline_path") or "",
        "score": safe_int(item.get("score"), 0),
        "matched_terms": item.get("matched_terms") or [],
        "retrievalQueries": item.get("retrievalQueries") or [],
        "retrievalRoles": item.get("retrievalRoles") or [],
        "retrievalBranchIds": item.get("retrievalBranchIds") or [],
        "retrievalAnchors": item.get("retrievalAnchors") or [],
        "eligibleBranchIds": item.get("eligibleBranchIds") or [],
        "branchEvidence": item.get("branchEvidence") or [],
        "selectedBranchId": (
            item.get("ai_selected_branch_id")
            or item.get("aiSelectedBranchId")
            or item.get("selectedBranchId")
            or ""
        ),
        "selectedBranchName": (
            item.get("ai_selected_branch_name")
            or item.get("aiSelectedBranchName")
            or item.get("selectedBranchName")
            or ""
        ),
        "actions": item.get("actions") or [],
        "startPage": start_page,
        "businessPath": item.get("businessPath") or item.get("baseline_path") or "",
        "lastRunStatus": item.get("lastRunStatus") or "",
        "failureRate": item.get("failureRate") or 0,
        "baselineUsable": item.get("baselineUsable") is True,
        "trusted": item.get("trusted") is True,
        "sourceKind": item.get("sourceKind") or "",
        "sourceTrust": safe_int(item.get("sourceTrust"), 0),
        "verificationStatus": item.get("verificationStatus") or "",
        "provenancePath": item.get("provenancePath") or item.get("file") or "",
        "snippet": snippet[:1600],
    }


def _normalize_required_baseline_branches(required_branches, limit=3):
    normalized = []
    seen = set()
    for index, raw in enumerate(required_branches or []):
        item = raw if isinstance(raw, dict) else {"name": str(raw or ""), "query": str(raw or "")}
        branch_id = clean_id(item.get("id") or item.get("branchId") or f"branch_{index + 1:03d}", f"branch_{index + 1:03d}")
        name = str(item.get("name") or item.get("branch") or branch_id).strip()
        query = str(item.get("query") or item.get("retrievalQuery") or name).strip()[:2000]
        if not query or branch_id in seen:
            continue
        seen.add(branch_id)
        normalized.append({
            "id": branch_id,
            "name": name,
            "query": query,
            "anchors": normalize_text_list(item.get("anchors") or item.get("anchorTerms")),
            "source": str(item.get("source") or "agent_business_flow").strip(),
        })
        if len(normalized) >= max(1, min(3, safe_int(limit, 3))):
            break
    sibling_names = [item["name"] for item in normalized]
    for item in normalized:
        anchors = item.get("anchors") or baseline_branch_anchor_terms(item["name"], sibling_names)
        item["anchors"] = list(dict.fromkeys(
            re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(anchor or "")).lower()
            for anchor in anchors
            if len(re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(anchor or ""))) >= 2
        ))[:4]
    return normalized


def _baseline_candidate_branch_evidence_text(candidate):
    candidate = candidate if isinstance(candidate, dict) else {}
    values = [
        candidate.get("title"),
        candidate.get("module"),
        candidate.get("file"),
        candidate.get("path"),
        candidate.get("businessPath"),
        candidate.get("provenancePath"),
        candidate.get("snippet"),
        candidate.get("actions"),
    ]
    text = "\n".join(normalize_text_list(values)).lower()
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text)


def _baseline_candidate_direct_navigation_evidence_text(candidate):
    """Prefer actual visible-text navigation over broad candidate metadata."""
    candidate = candidate if isinstance(candidate, dict) else {}
    snippet = str(candidate.get("snippet") or "")
    action_values = []
    for match in re.finditer(
        r"^\s*-\s*(?:aiTap|ai|aiAction|aiAct)\s*:\s*(.+?)\s*$",
        snippet,
        flags=re.M,
    ):
        value = str(match.group(1) or "").strip().strip("\"'")
        if value:
            action_values.append(value)
    if action_values:
        text = "\n".join(action_values).lower()
    else:
        text = "\n".join(normalize_text_list([
            candidate.get("title"),
            candidate.get("file"),
            candidate.get("path"),
            candidate.get("provenancePath"),
            candidate.get("businessPath"),
            snippet,
        ])).lower()
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text)


def _annotate_baseline_branch_eligibility(candidates, required_branches):
    counts = {item["id"]: 0 for item in required_branches}
    for candidate in candidates:
        candidate_query_keys = {
            re.sub(r"\s+", " ", str(query or "")).strip().lower()
            for query in (candidate.get("retrievalQueries") or [])
            if str(query or "").strip()
        }
        candidate_branch_ids = {
            str(branch_id or "").strip()
            for branch_id in (candidate.get("retrievalBranchIds") or [])
            if str(branch_id or "").strip()
        }
        evidence_text = _baseline_candidate_branch_evidence_text(candidate)
        direct_navigation_text = _baseline_candidate_direct_navigation_evidence_text(candidate)
        eligible_ids = []
        evidence_rows = []
        for branch in required_branches:
            branch_query = re.sub(r"\s+", " ", branch["query"]).strip().lower()
            matched_anchors = [
                anchor for anchor in (branch.get("anchors") or [])
                if anchor and anchor in evidence_text
            ]
            direct_anchors = [
                anchor for anchor in (branch.get("anchors") or [])
                if anchor and anchor in direct_navigation_text
            ]
            retrieved_for_branch = branch["id"] in candidate_branch_ids or branch_query in candidate_query_keys
            if not retrieved_for_branch or not matched_anchors or not direct_anchors:
                continue
            eligible_ids.append(branch["id"])
            counts[branch["id"]] += 1
            evidence_rows.append({
                "branchId": branch["id"],
                "matchedAnchors": matched_anchors[:3],
                "directNavigationAnchors": direct_anchors[:3],
            })
        candidate["eligibleBranchIds"] = eligible_ids
        candidate["branchEvidence"] = evidence_rows
    return counts


def call_skill_baseline_reranker(
    title,
    module,
    query_text,
    candidates,
    model_config=None,
    limit=3,
    required_branches=None,
):
    """Use AI to choose the most relevant cached baseline examples, with local fallback."""
    candidates = [
        _compact_baseline_candidate(item, idx)
        for idx, item in enumerate(candidates or [])
        if isinstance(item, dict) and item.get("baselineUsable") is True and item.get("trusted") is True
    ]
    limit = max(1, min(3, safe_int(limit, 3)))
    requested_required_branches = _normalize_required_baseline_branches(required_branches, limit=limit)
    has_required_branch_contract = bool(requested_required_branches)
    eligible_branch_candidate_counts = _annotate_baseline_branch_eligibility(
        candidates,
        requested_required_branches,
    )
    required_branches = [
        item for item in requested_required_branches
        if eligible_branch_candidate_counts.get(item["id"], 0) > 0
    ]
    unavailable_required_branches = [
        item for item in requested_required_branches if item not in required_branches
    ]
    required_branch_by_id = {item["id"]: item for item in required_branches}
    local_selected_ids = {item["id"] for item in candidates[:limit]}
    model_runtime_trace = {}
    trace = {
        "enabled": True,
        "candidate_count": len(candidates),
        "selected_count": min(limit, len(candidates)),
        "fallback": False,
        "requested_required_branch_count": len(requested_required_branches),
        "required_branch_count": len(required_branches),
        "required_branch_ids": [item["id"] for item in required_branches],
        "unavailable_required_branch_ids": [item["id"] for item in unavailable_required_branches],
        "eligible_branch_candidate_counts": eligible_branch_candidate_counts,
        **_model_config_trace(model_config, model_runtime_trace),
    }
    if not candidates:
        trace.update({"selected_count": 0, "fallback": True, "error": "no_candidates"})
        return {"selected": [], "trace": trace, "review": {"selection_reason": "没有相似基线候选"}}
    request = {
        "title": title,
        "module": module,
        "queryText": str(query_text or "")[:6000],
        "limit": limit,
        "requiredBranches": required_branches,
        "candidates": candidates[:20],
        "rules": {
            "choose_from_candidates_only": True,
            "max_selected": limit,
            "avoid_unrelated_external_flow": True,
            "fallback_when_irrelevant": True,
            "prefer_complementary_roles": ["navigation_path", "capability_pattern", "assertion_pattern"],
            "preserve_explicit_business_branches": True,
            "cover_required_branches_before_role_diversity": True,
            "one_selected_candidate_per_required_branch": True,
            "branch_id_must_be_in_candidate_eligible_branch_ids": True,
            "cite_candidate_provenance_exactly": True,
        },
    }

    def parse_selection(result):
        selected_rows = []
        id_to_candidate = {item["id"]: item for item in candidates}
        invalid_citation_count = 0
        invalid_branch_count = 0
        covered_branch_ids = []
        selected_candidate_ids = set()
        used_local_fallback = False
        for selected in result.get("selected") or []:
            if not isinstance(selected, dict):
                continue
            selected_id = clean_id(selected.get("id") or selected.get("candidateId") or "", "")
            if not selected_id or selected_id not in id_to_candidate or selected_id in selected_candidate_ids:
                continue
            candidate = id_to_candidate[selected_id]
            cited_path = str(selected.get("candidatePath") or selected.get("provenancePath") or "").replace("\\", "/").strip()
            expected_paths = {
                str(candidate.get("file") or "").replace("\\", "/").strip(),
                str(candidate.get("provenancePath") or "").replace("\\", "/").strip(),
            }
            if cited_path and cited_path not in expected_paths:
                invalid_citation_count += 1
                continue
            branch_id = clean_id(selected.get("branchId") or selected.get("branch_id") or "", "")
            branch = required_branch_by_id.get(branch_id)
            if has_required_branch_contract and (
                not branch
                or branch_id not in (candidate.get("eligibleBranchIds") or [])
                or branch_id in covered_branch_ids
            ):
                invalid_branch_count += 1
                continue
            row = dict(candidate)
            row["ai_selected_reason"] = selected.get("reason") or ""
            row["ai_confidence"] = selected.get("confidence")
            row["ai_selected_role"] = selected.get("role") or ""
            row["ai_selected_branch_id"] = branch_id
            row["ai_selected_branch_name"] = (branch or {}).get("name") or ""
            row["ai_cited_path"] = cited_path or candidate.get("provenancePath") or candidate.get("file") or ""
            selected_rows.append(row)
            selected_candidate_ids.add(selected_id)
            if branch_id:
                covered_branch_ids.append(branch_id)
            if len(selected_rows) >= limit:
                break
        if not selected_rows and not has_required_branch_contract:
            selected_rows = [dict(item) for item in candidates if item["id"] in local_selected_ids][:limit]
            used_local_fallback = True
        repairable_missing_branch_ids = [
            item["id"] for item in required_branches if item["id"] not in covered_branch_ids
        ]
        missing_branch_ids = [item["id"] for item in unavailable_required_branches] + repairable_missing_branch_ids
        return selected_rows, {
            "invalid_citation_count": invalid_citation_count,
            "invalid_branch_count": invalid_branch_count,
            "covered_branch_ids": covered_branch_ids,
            "missing_branch_ids": missing_branch_ids,
            "repairable_missing_branch_ids": repairable_missing_branch_ids,
            "branch_coverage_ok": not missing_branch_ids,
            "used_local_fallback": used_local_fallback,
        }

    try:
        result = run_ai_skill(
            "baseline_reranker",
            request,
            timeout=AI_BASELINE_RERANKER_TIMEOUT_SECONDS,
            temperature=0.0,
            respect_global_timeout=False,
            retry_count=0,
            model_config=model_config,
            runtime_trace=model_runtime_trace,
        )
        selected_rows, selection_audit = parse_selection(result)
        trace["branch_repair_attempted"] = False
        if selection_audit.get("repairable_missing_branch_ids"):
            trace["branch_repair_attempted"] = True
            repair_request = dict(request)
            repair_request["candidates"] = [
                item for item in request["candidates"]
                if item.get("eligibleBranchIds")
            ]
            repair_request["previousSelection"] = result.get("selected") or []
            repair_request["selectionValidationIssues"] = [
                "Top3 未覆盖以下 AI 首批业务分支。仅可将 branchId 分配给 eligibleBranchIds 明确包含该分支的候选；"
                "每个分支必须选择一条自身 title/businessPath/actions 与该分支一致的候选："
                + "、".join(
                    required_branch_by_id[item].get("name") or item
                    for item in selection_audit["repairable_missing_branch_ids"]
                    if item in required_branch_by_id
                )
            ]
            try:
                repaired_result = run_ai_skill(
                    "baseline_reranker",
                    repair_request,
                    timeout=AI_BASELINE_RERANKER_TIMEOUT_SECONDS,
                    temperature=0.0,
                    respect_global_timeout=False,
                    retry_count=0,
                    model_config=model_config,
                    runtime_trace=model_runtime_trace,
                )
                repaired_rows, repaired_audit = parse_selection(repaired_result)
                if len(repaired_audit.get("covered_branch_ids") or []) > len(selection_audit.get("covered_branch_ids") or []):
                    result = repaired_result
                    selected_rows = repaired_rows
                    selection_audit = repaired_audit
            except Exception as repair_exc:
                trace["branch_repair_error"] = str(repair_exc)
        if selection_audit.get("used_local_fallback"):
            trace["fallback"] = True
            trace["error"] = "ai_selected_none_or_invalid"
        trace["selected_count"] = len(selected_rows)
        trace.update(_model_config_trace(model_config, model_runtime_trace))
        trace.update(selection_audit)
        trace["branch_repair_succeeded"] = bool(
            trace.get("branch_repair_attempted") and selection_audit.get("branch_coverage_ok")
        )
        trace["selection_roles"] = [item.get("ai_selected_role") for item in selected_rows if item.get("ai_selected_role")]
        return {"selected": selected_rows, "trace": trace, "review": result.get("review") or {}}
    except Exception as exc:
        selected_rows = [] if has_required_branch_contract else [dict(item) for item in candidates[:limit]]
        trace.update(_model_config_trace(model_config, model_runtime_trace))
        trace.update({
            "fallback": True,
            "selected_count": len(selected_rows),
            "error": str(exc),
            "covered_branch_ids": [],
            "missing_branch_ids": [item["id"] for item in requested_required_branches],
            "branch_coverage_ok": not has_required_branch_contract,
            "branch_repair_attempted": False,
            "branch_repair_succeeded": False,
        })
        reason = "AI 选择失败，保留分支覆盖门禁" if has_required_branch_contract else "AI 选择失败，回退本地 TopN"
        return {"selected": selected_rows, "trace": trace, "review": {"selection_reason": reason}}


def _clamp_scope_size(value, fallback=3):
    raw = safe_int(value, fallback)
    if raw <= 3:
        return 3, "small"
    if raw <= 5:
        return 5, "medium"
    return 8, "large"


def call_skill_execution_scope_planner(title, module, text_assets, selected_baselines, model_config=None):
    """Let AI suggest generation scope, while platform clamps to 3/5/8 and smoke<=3."""
    local_targets = generation_volume_targets({"requirement_points": normalize_text_list(text_assets)}, mode="full")
    fallback_count = safe_int(local_targets.get("target_automation_cases"), 3)
    target_count, size = _clamp_scope_size(fallback_count, 3)
    model_runtime_trace = {}
    trace = {
        "enabled": True,
        "fallback": False,
        **_model_config_trace(model_config, model_runtime_trace),
    }
    request = {
        "title": title,
        "module": module,
        "requirementText": "\n\n".join(normalize_text_list(text_assets))[:8000],
        "selectedBaselines": [_compact_baseline_candidate(item, idx) for idx, item in enumerate(selected_baselines or [])],
        "platformLimits": {
            "caseCounts": [3, 5, 8],
            "maxSmokeCount": 3,
            "continueThreshold": 0.5,
        },
    }
    try:
        result = run_ai_skill(
            "execution_scope_planner",
            request,
            timeout=AI_EXECUTION_SCOPE_PLANNER_TIMEOUT_SECONDS,
            temperature=0.0,
            respect_global_timeout=False,
            retry_count=0,
            model_config=model_config,
            runtime_trace=model_runtime_trace,
        )
        trace.update(_model_config_trace(model_config, model_runtime_trace))
        target_count, size = _clamp_scope_size(result.get("targetCaseCount"), target_count)
        smoke_count = max(1, min(3, safe_int(result.get("smokeCount"), min(3, target_count))))
        plan = {
            "size": size,
            "targetCaseCount": target_count,
            "smokeCount": smoke_count,
            "continueThreshold": 0.5,
            "reason": result.get("reason") or "AI 根据需求规模和相似基线规划生成范围",
            "businessFlow": normalize_text_list(result.get("businessFlow") or result.get("business_flow"))[:8],
            "trace": {**trace, "targetCaseCount": target_count, "smokeCount": smoke_count},
        }
        return plan
    except Exception as exc:
        trace.update(_model_config_trace(model_config, model_runtime_trace))
        trace.update({"fallback": True, "error": str(exc)})
        return {
            "size": size,
            "targetCaseCount": target_count,
            "smokeCount": min(3, target_count),
            "continueThreshold": 0.5,
            "reason": "AI 范围规划失败，回退平台 3/5/8 规则",
            "businessFlow": [],
            "trace": trace,
        }


def _planner_case_id(case, index=0, origin_level="automatic"):
    case = case if isinstance(case, dict) else {}
    explicit = str(case.get("case_id") or case.get("id") or "").strip()
    if explicit:
        return explicit
    prefix = "MC" if origin_level == "manual" else "TC"
    return f"{prefix}-{index + 1:03d}"


def _planner_case_origin_level(case, default="automatic"):
    """Preserve whether a candidate was AI-generated across planner passes."""
    case = case if isinstance(case, dict) else {}
    classification = (
        case.get("ai_case_classification")
        if isinstance(case.get("ai_case_classification"), dict)
        else {}
    )
    value = str(
        case.get("originExecutionLevel")
        or case.get("origin_execution_level")
        or classification.get("originLevel")
        or classification.get("origin_level")
        or default
    ).strip().lower()
    return "manual" if value == "manual" else "automatic"


def _compact_case_for_plan(case, index=0, origin_level="automatic"):
    case = case if isinstance(case, dict) else {}
    assertions = normalize_text_list(
        case.get("assertions")
        or case.get("expected_result")
        or case.get("expected")
    )
    return {
        "case_id": _planner_case_id(case, index, origin_level=origin_level),
        "title": case.get("title") or case.get("case_name") or "",
        "priority": case_priority(case),
        "smoke": bool(is_smoke_case(case)),
        "scenario": case.get("scenario") or "",
        "coverage": case.get("coverage") or case.get("requirement_point") or "",
        "steps": normalize_text_list(case.get("steps"))[:8],
        "assertions": assertions[:4],
        "originLevel": origin_level,
        "currentLevel": str(case.get("executionLevel") or case.get("execution_level") or "").strip(),
        "requirementRefs": normalize_text_list(
            case.get("requirementRefs") or case.get("requirement_refs")
        )[:8],
        "previousReason": case.get("automation_reason") or case.get("reason") or "",
        "suggestedSetup": case.get("suggested_setup") or "",
    }


def _planner_requirement_ids(value):
    """Extract canonical REQ ids without trusting model-provided cross-case mappings."""
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    result = []
    for match in re.finditer(r"\bREQ[-_ ]?0*(\d+)\b", str(text or ""), flags=re.I):
        requirement_id = f"REQ-{int(match.group(1)):03d}"
        if requirement_id not in result:
            result.append(requirement_id)
    return result


def _planner_requirement_point_map(points):
    mapping = {}
    for point in normalize_text_list(points):
        for requirement_id in _planner_requirement_ids(point):
            mapping.setdefault(requirement_id, point)
    return mapping


def _source_case_requirement_ids(case):
    case = case if isinstance(case, dict) else {}
    # Every field here belongs to the candidate before planner classification.
    # Keep their union so AI-authored cross-cutting mappings survive, while the
    # downstream guard still rejects requirement IDs invented by the planner.
    return _planner_requirement_ids([
        case.get("coverage"),
        case.get("requirement_point"),
        case.get("requirementPoint"),
        case.get("source_requirement_point"),
        case.get("sourceRequirementPoint"),
        case.get("requirementRefs"),
        case.get("requirement_refs"),
    ])


def _ground_planner_requirement_refs(case, classification, requirement_points):
    """Keep planner mappings on the candidate's original requirement boundary."""
    case = case if isinstance(case, dict) else {}
    classification = classification if isinstance(classification, dict) else {}
    point_map = _planner_requirement_point_map(requirement_points)
    source_ids = _source_case_requirement_ids(case)
    proposed_refs = normalize_text_list(
        classification.get("requirementRefs")
        or classification.get("requirement_refs")
        or classification.get("coverage")
    )[:8]
    proposed_ids = _planner_requirement_ids(proposed_refs)
    guarded = bool(source_ids and proposed_ids and any(item not in source_ids for item in proposed_ids))

    if source_ids:
        grounded_ids = list(source_ids)
    else:
        grounded_ids = [item for item in proposed_ids if not point_map or item in point_map]
    refs = [point_map.get(requirement_id, requirement_id) for requirement_id in grounded_ids]
    if not refs and not source_ids:
        refs = proposed_refs
    return list(dict.fromkeys(ref for ref in refs if str(ref or "").strip())), guarded


def executable_yaml_portfolio_audit(payload, targets=None):
    """Audit the final AI-selected executable portfolio before YAML conversion."""
    normalized = normalize_cases_payload(payload)
    analysis = normalized.get("analysis") if isinstance(normalized.get("analysis"), dict) else {}
    requirement_points = normalize_text_list(analysis.get("requirement_points"))
    planned_targets = dict(targets) if isinstance(targets, dict) else generation_volume_targets(analysis, mode="full")
    all_cases = [item for item in (normalized.get("cases") or []) if isinstance(item, dict)]
    executable_cases = [
        item for item in all_cases
        if str(item.get("executionLevel") or item.get("execution_level") or "").strip().lower() == "executable"
    ]
    unresolved_cases = [item for item in all_cases if item not in executable_cases]
    missing_requirement_points = [
        point for point in requirement_points
        if not any(case_matches_requirement(case, point) for case in executable_cases)
    ]
    acceptance_checks = [
        item for item in (analysis.get("requirement_acceptance_checks") or [])
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    covered_acceptance_checks = [
        check for check in acceptance_checks
        if any(
            _case_covers_acceptance_in_portfolio(case, check, acceptance_checks)
            for case in executable_cases
        )
    ]
    missing_acceptance_checks = [
        check for check in acceptance_checks
        if check not in covered_acceptance_checks
    ]
    missing_acceptance_descriptors = [
        requirement_acceptance_descriptor(check) for check in missing_acceptance_checks
    ]
    missing_points = list(dict.fromkeys(missing_requirement_points + missing_acceptance_descriptors))
    target_min = max(0, safe_int(planned_targets.get("min_automation_cases"), 0))
    executable_ids = [
        _planner_case_id(case, idx, origin_level="automatic")
        for idx, case in enumerate(executable_cases)
    ]
    unresolved_ids = [
        _planner_case_id(case, idx, origin_level="automatic")
        for idx, case in enumerate(unresolved_cases)
    ]
    target_shortfall = max(0, target_min - len(executable_cases))
    reasons = []
    advisories = []
    if not executable_cases:
        reasons.append("没有 executable 候选")
    if missing_requirement_points:
        reasons.append("显式需求点尚未由 executable 候选覆盖")
    if missing_acceptance_checks:
        reasons.append("显式需求的验收维度尚未由 executable 步骤和断言覆盖")
    if target_shortfall:
        advisories.append(
            f"executable 候选 {len(executable_cases)} 条，低于 AI 规划目标 {target_min} 条；"
            "数量目标不作为硬门禁，不为凑数引入低价值或不稳定用例"
        )
    if unresolved_cases:
        reasons.append(f"仍有 {len(unresolved_cases)} 条自动候选停留在非终态分类")
    return {
        "ok": not reasons,
        "requirementPointCount": len(requirement_points),
        "requirementPoints": requirement_points[:12],
        "missingRequirementPoints": missing_points[:12],
        "acceptanceCheckCount": len(acceptance_checks),
        "coveredAcceptanceCheckCount": len(covered_acceptance_checks),
        "coveredAcceptanceCheckIds": [str(item.get("id") or "") for item in covered_acceptance_checks[:40]],
        "missingAcceptanceCheckCount": len(missing_acceptance_checks),
        "missingAcceptanceChecks": [
            {
                "id": item.get("id") or "",
                "requirementId": item.get("requirementId") or "",
                "branch": item.get("branch") or "",
                "kind": item.get("kind") or "general",
                "text": item.get("text") or "",
                "descriptor": requirement_acceptance_descriptor(item),
            }
            for item in missing_acceptance_checks[:24]
        ],
        "targetExecutableCount": target_min,
        "targetMet": target_shortfall == 0,
        "targetShortfall": target_shortfall,
        "advisories": advisories,
        "executableCount": len(executable_cases),
        "executableCaseIds": executable_ids[:20],
        "unresolvedAutomaticCount": len(unresolved_cases),
        "unresolvedAutomaticCaseIds": unresolved_ids[:20],
        "reasons": reasons,
    }


def _case_has_branch_execution_evidence(case, branch):
    branch_key = _compact_branch_text(branch)
    if not branch_key:
        return True
    evidence = "\n".join(_case_acceptance_evidence_items(case))
    evidence_key = _compact_branch_text(evidence)
    return branch_key in evidence_key


def _trusted_selected_baseline_for_branch(selected_baselines, branch):
    branch_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(branch or "")).lower()
    if not branch_key:
        return None
    for baseline in selected_baselines or []:
        if not isinstance(baseline, dict):
            continue
        branch_name = (
            baseline.get("selectedBranchName")
            or baseline.get("ai_selected_branch_name")
            or baseline.get("aiSelectedBranchName")
            or ""
        )
        branch_name_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(branch_name)).lower()
        trusted = bool(
            baseline.get("baselineUsable") is True and baseline.get("trusted") is True
        ) or (
            str(baseline.get("sourceKind") or "").strip() == "verified_execution"
            and str(baseline.get("verificationStatus") or "").strip() == "execution_success"
        )
        if trusted and branch_key in branch_name_key:
            return baseline
    return None


def _bounded_landing_tail(case, target_terms):
    """Reduce an AI-authored external transition to one click and one observation."""
    case = case if isinstance(case, dict) else {}
    if _case_has_deep_external_action(case):
        return None
    steps = normalize_text_list(case.get("steps"))
    click_index = next((
        index for index in range(len(steps) - 1, -1, -1)
        if "点击" in steps[index]
        and "坐标" not in steps[index]
        and (
            any(term in steps[index] for term in target_terms)
            or (not target_terms and any(term in steps[index] for term in ("入口", "按钮", "第三方")))
        )
    ), -1)
    if click_index < 0:
        return None
    observations = []
    for raw_step in steps[click_index + 1:]:
        step = str(raw_step or "").strip()
        if not step:
            continue
        if step.startswith(("记录", "保留记录", "收集证据")):
            # Manual test narration is not a device action and must not leak
            # into the bounded Runner tail.
            continue
        if step.startswith(("等待", "观察", "检查", "验证", "校验", "断言")):
            observations.append(re.sub(r"^(?:等待|观察|检查|验证|校验|断言)", "检查", step))
            continue
        confirmation = step[2:].strip() if step.startswith("确认") else ""
        confirmation_is_observation = bool(
            confirmation
            and any(term in confirmation for term in (
                "可见", "显示", "出现", "存在", "加载", "完成", "已", "无", "未", "没有",
                "页面", "列表", "弹窗", "跳转", "离开", "区域", "元素", "状态", "结果", "正确",
            ))
            and not any(term in step for term in (
                "确认打印", "确认支付", "确认上传", "确认提交", "确认删除", "确认下载",
                "确认保存", "确认发送", "确认下单", "确认选择", "确认授权", "确认登录",
            ))
        )
        if confirmation_is_observation:
            observations.append("检查" + step[2:])
            continue
        return None
    outcomes = normalize_text_list(
        case.get("assertions") or case.get("expected_result") or case.get("expected")
    )
    if not observations and outcomes:
        observations = ["检查" + outcomes[0]]
    if not observations:
        return None
    observation_text = "；".join(
        re.sub(r"^(?:等待|观察|检查|验证|校验|断言)", "", item).strip("；， ")
        for item in observations
        if str(item or "").strip()
    )
    if not observation_text:
        return None
    assertion_target = "；".join(dict.fromkeys(
        [item for item in outcomes + [observation_text] if str(item or "").strip()]
    ))
    return {
        "flow": [steps[click_index], f"检查{observation_text}"],
        "assertionTarget": assertion_target,
    }


def _bounded_landing_tail_is_executable(tail, requirement_refs):
    tail = tail if isinstance(tail, dict) else {}
    probe = {
        "steps": normalize_text_list(tail.get("flow")),
        "assertions": normalize_text_list(tail.get("assertionTarget")),
        "requirementRefs": normalize_text_list(requirement_refs),
        "ai_case_plan": {"baselineGrounded": True, "pathPlanApplied": True},
    }
    return _case_is_bounded_external_landing_check(probe)


def _merge_bounded_landing_tails(tails):
    """Compose alternative AI-observed first-screen outcomes for one target click."""
    usable = [item for item in (tails or []) if isinstance(item, dict)]
    if not usable:
        return None
    click_step = next((
        normalize_text_list(item.get("flow"))[0]
        for item in usable
        if normalize_text_list(item.get("flow"))
    ), "")
    outcomes = []
    for item in usable:
        outcome = re.sub(
            r"(?:点击|点按|轻触)",
            "操作",
            str(item.get("assertionTarget") or "").strip(),
        )
        for phrase in (
            "检查是否正常", "确认是否正常", "页面正常", "功能正常",
            "验证功能正常", "验证页面正确", "检查页面正确", "页面正确",
            "结果正常", "状态正常",
        ):
            outcome = outcome.replace(phrase, "")
        outcome = re.sub(r"[，,；;]{2,}", "；", outcome).strip(" ，,；;")
        if outcome and outcome not in outcomes:
            outcomes.append(outcome)
        if len(outcomes) >= 3:
            break
    if not click_step or not outcomes:
        return None

    # A model may describe one concrete first screen (for example a content
    # list) instead of enumerating every possible auth/login state. Keep that
    # AI-authored state and add only a target-bound visible landing alternative;
    # the existing bounded-landing scorer still requires a concrete state plus
    # an explicit crash/blank-screen stability claim before accepting it.
    source_outcome_text = "；".join(outcomes)
    stability_observed = bool(re.search(
        r"(?:无|未|没有|不)(?:App)?(?:崩溃|Crash|白屏|闪退)|"
        r"(?:未出现|没有出现|未发生|没有发生)[^；。]{0,20}(?:崩溃|Crash|白屏|闪退)",
        source_outcome_text,
        flags=re.I,
    ))
    quoted_targets = [
        item.strip()
        for item in re.findall(r"[「『\"'‘]([^」』\"'’]+)[」』\"'’]", click_step)
        if item.strip()
    ]
    target_label = quoted_targets[-1] if quoted_targets else re.sub(
        r"^(?:点击|点按|轻触)\s*", "", click_step,
    ).strip(" ：，,。")
    target_label = re.sub(r"(?:入口|按钮|图标|icon)$", "", target_label, flags=re.I).strip()
    if target_label and stability_observed:
        branded_landing = (
            f"操作「{target_label}」后已离开来源页，且「{target_label}」落地页的"
            "页面区域或页面元素可见，无崩溃、无白屏"
        )
        if branded_landing not in outcomes:
            outcomes.append(branded_landing)
    assertion_target = "以下任一首个稳定状态可见：" + "；或：".join(outcomes)
    return {
        "flow": [click_step, f"检查{assertion_target}"],
        "assertionTarget": assertion_target,
        "sourceCaseIds": list(dict.fromkeys(
            str(item.get("sourceCaseId") or "").strip()
            for item in usable
            if str(item.get("sourceCaseId") or "").strip()
        )),
    }


def _bounded_candidate_precondition(case, baseline):
    preconditions = normalize_text_list(
        (case or {}).get("preconditions") or (case or {}).get("precondition")
    )
    if preconditions:
        return preconditions[0]
    return str((baseline or {}).get("startPage") or "").strip()


_BASELINE_DATA_ACTION_TERMS = (
    "导入", "上传", "选择文件", "选择照片", "手机图库", "系统相册", "勾选",
    "去打印", "立即打印", "确认打印", "开始打印", "下载", "保存", "发送",
    "确认授权", "同意授权", "输入账号", "输入密码", "输入验证码",
)


def _trusted_baseline_source_navigation_flow(baseline, target_terms, branch):
    """Extract the visible-text source-page prefix from one trusted selected baseline."""
    baseline = baseline if isinstance(baseline, dict) else {}
    trusted = bool(
        baseline.get("baselineUsable") is True and baseline.get("trusted") is True
    ) or (
        str(baseline.get("sourceKind") or "").strip() == "verified_execution"
        and str(baseline.get("verificationStatus") or "").strip() == "execution_success"
    )
    if not trusted:
        return []
    snippet = str(baseline.get("snippet") or "")
    if not snippet:
        return []
    normalized_targets = {
        re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(term or "")).lower()
        for term in (target_terms or [])
        if str(term or "").strip()
    }
    flow = []
    for match in re.finditer(
        r"^\s*-\s*(aiTap|aiWaitFor|aiScroll)\s*:\s*(.+?)\s*$",
        snippet,
        flags=re.M,
    ):
        action = str(match.group(1) or "").strip()
        value = str(match.group(2) or "").strip().strip("\"'")
        if not value:
            continue
        compact_value = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).lower()
        if action == "aiTap":
            if any(term and term in compact_value for term in normalized_targets):
                break
            if any(term in value for term in _BASELINE_DATA_ACTION_TERMS):
                break
            tap_target = re.sub(r"^(?:点击|点按|轻触|选择|进入|打开)\s*", "", value).strip()
            quoted_targets = list(dict.fromkeys(
                item.strip()
                for item in re.findall(r"[「『“\"'‘]([^」』”\"'’]+)[」』”\"'’]", tap_target)
                if item.strip()
            ))
            if len(quoted_targets) == 1 and any(marker in tap_target for marker in ("名称为", "名为")):
                flow.append(f"点击名称为「{quoted_targets[0]}」的入口")
            elif quoted_targets:
                flow.append(f"点击{tap_target}")
            else:
                flow.append(f"点击「{tap_target}」")
        elif action == "aiWaitFor":
            flow.append(value if value.startswith("等待") else f"等待{value}")
            if any(term and term in compact_value for term in normalized_targets):
                break
        elif action == "aiScroll":
            flow.append(f"滑动页面，直到{value}")
        if len(flow) >= 6:
            break
    probe = {"steps": flow}
    if len(flow) < 2 or not _case_has_branch_execution_evidence(probe, branch):
        return []
    return flow


def _navigation_action_target_key(step):
    """Return a comparable visible target for one human-readable navigation action."""
    text = str(step or "").strip()
    if not re.match(
        r"^(?:(?:请|然后|再)\s*)*(?:点击|点按|轻触|进入|打开|选择|切换|前往)",
        text,
    ):
        return ""
    quoted = [
        item.strip()
        for item in re.findall(r"[「『“\"'‘]([^」』”\"'’]+)[」』”\"'’]", text)
        if item.strip()
    ]
    target = quoted[-1] if quoted else re.sub(
        r"^(?:(?:请|然后|再)\s*)*(?:点击|点按|轻触|进入|打开|选择|切换|前往)\s*",
        "",
        text,
    )
    target = re.sub(r"^(?:首页或底部导航中|首页中|底部导航中)?(?:名称为|名为)?", "", target)
    target = re.sub(r"(?:的)?(?:入口|按钮)$", "", target)
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", target).lower()


def _navigation_target_keys_match(left, right):
    left = str(left or "").strip()
    right = str(right or "").strip()
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 2 and shorter in longer


def _normalize_visual_current_page_evidence(items):
    """Keep auditable, visible-text evidence emitted by the visual AI."""
    normalized = []
    seen = set()
    confidence_names = {"high": 0.9, "medium": 0.6, "low": 0.3}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("caseId") or item.get("case_id") or "").strip()
        requirement_id = str(item.get("requirementId") or item.get("requirement_id") or "").strip()
        branch = str(item.get("branch") or "").strip()
        page_title = str(item.get("pageTitle") or item.get("page_title") or "").strip()
        navigation_leaf = str(item.get("navigationLeaf") or item.get("navigation_leaf") or "").strip()
        target_text = str(item.get("targetText") or item.get("target_text") or "").strip()
        parent_path = normalize_text_list(item.get("parentPath") or item.get("parent_path"))[:6]
        raw_confidence = item.get("confidence")
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = confidence_names.get(str(raw_confidence or "").strip().lower(), 0.0)
        confidence = max(0.0, min(1.0, confidence))
        same_branch = item.get("sameBranch") is True or item.get("same_branch") is True
        branch_key = _navigation_action_target_key(f"点击「{branch}」")
        page_title_key = _navigation_action_target_key(f"点击「{page_title}」")
        navigation_leaf_key = _navigation_action_target_key(f"点击「{navigation_leaf}」")
        target_text_key = _navigation_action_target_key(f"点击「{target_text}」")
        page_title_is_concrete_leaf = bool(
            same_branch
            and confidence >= 0.75
            and page_title_key
            and navigation_leaf_key
            and branch_key
            and _navigation_target_keys_match(navigation_leaf_key, branch_key)
            and not _navigation_target_keys_match(page_title_key, navigation_leaf_key)
            and not _navigation_target_keys_match(page_title_key, branch_key)
            and not _navigation_target_keys_match(page_title_key, target_text_key)
            and not re.search(r"(?:^|\b)(?:frame|group|node|copy)(?:\b|\s*\d*$)|画板|节点|备份", page_title, re.I)
        )
        leaf_derived_from_page_title = False
        original_navigation_leaf = ""
        if page_title_is_concrete_leaf:
            original_navigation_leaf = navigation_leaf
            if not any(
                _navigation_target_keys_match(
                    _navigation_action_target_key(f"点击「{label}」"),
                    navigation_leaf_key,
                )
                for label in parent_path
            ):
                parent_path.append(navigation_leaf)
                parent_path = parent_path[-6:]
            navigation_leaf = page_title
            leaf_derived_from_page_title = True
        if (
            not navigation_leaf
            or not target_text
            or not branch
            or not parent_path
            or not (case_id or requirement_id)
        ):
            continue
        unsafe_text = "\n".join(parent_path + [navigation_leaf])
        if (
            len(navigation_leaf) > 48
            or re.search(r"(?:xpath|selector|coordinate|坐标|x\s*=|y\s*=|\d+\s*[,，]\s*\d+)", unsafe_text, re.I)
            or _source_navigation_has_alternative_destinations(
                [f"点击「{value}」" for value in parent_path + [navigation_leaf]]
            )
        ):
            continue
        record = {
            "caseId": case_id,
            "requirementId": requirement_id,
            "branch": branch,
            "pageTitle": page_title,
            "parentPath": parent_path,
            "navigationLeaf": navigation_leaf,
            "targetText": target_text,
            "sameBranch": same_branch,
            "confidence": round(confidence, 4),
            "source": str(item.get("source") or "visual_grounder").strip() or "visual_grounder",
        }
        if leaf_derived_from_page_title:
            record["leafDerivedFromPageTitle"] = True
            record["originalNavigationLeaf"] = original_navigation_leaf
        key = (
            case_id,
            requirement_id,
            re.sub(r"\s+", "", branch).lower(),
            tuple(re.sub(r"\s+", "", value).lower() for value in parent_path),
            re.sub(r"\s+", "", navigation_leaf).lower(),
            re.sub(r"\s+", "", target_text).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(record)
    return normalized


def _current_visual_page_evidence_for_case(normalized, case, case_id, branch, target_terms):
    """Select current-frame evidence only when the AI mapped it to this exact branch."""
    review = (normalized or {}).get("review") if isinstance((normalized or {}).get("review"), dict) else {}
    evidence_items = _normalize_visual_current_page_evidence(review.get("current_page_evidence"))
    case_requirement_ids = set(_source_case_requirement_ids(case))
    branch_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(branch or "")).lower()
    target_keys = {
        re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(term or "")).lower()
        for term in (target_terms or [])
        if str(term or "").strip()
    }
    eligible = []
    for item in evidence_items:
        if item.get("sameBranch") is not True or float(item.get("confidence") or 0) < 0.75:
            continue
        evidence_case_id = str(item.get("caseId") or "").strip()
        evidence_requirement_id = str(item.get("requirementId") or "").strip()
        evidence_requirement_ids = set(_acceptance_requirement_ids(evidence_requirement_id))
        requirement_matches = bool(
            evidence_requirement_ids
            and case_requirement_ids
            and evidence_requirement_ids.intersection(case_requirement_ids)
        )
        evidence_branch_key = re.sub(
            r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(item.get("branch") or "")
        ).lower()
        branch_matches = bool(
            branch_key
            and evidence_branch_key
            and _navigation_target_keys_match(branch_key, evidence_branch_key)
        )
        case_matches = bool(
            evidence_case_id
            and evidence_case_id == str(case_id or "").strip()
        )
        if evidence_case_id and not case_matches and not (requirement_matches and branch_matches):
            continue
        if evidence_requirement_id and not requirement_matches:
            continue
        if not case_matches and not requirement_matches and not branch_matches:
            continue
        target_key = re.sub(
            r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(item.get("targetText") or "")
        ).lower()
        if target_keys and target_key and not any(
            _navigation_target_keys_match(target_key, value) for value in target_keys
        ):
            continue
        leaf_key = _navigation_action_target_key(f"点击「{item.get('navigationLeaf') or ''}」")
        if not leaf_key or any(_navigation_target_keys_match(leaf_key, value) for value in target_keys):
            continue
        eligible.append(item)
    def evidence_rank(item):
        item_branch_key = re.sub(
            r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(item.get("branch") or "")
        ).lower()
        return (
            item.get("leafDerivedFromPageTitle") is not True,
            str(item.get("caseId") or "").strip() == str(case_id or "").strip(),
            bool(branch_key and item_branch_key == branch_key),
            float(item.get("confidence") or 0),
            len(item.get("parentPath") or []),
        )

    eligible.sort(key=evidence_rank, reverse=True)
    # Python's stable sort preserves visual batch/Figma page order for equally
    # grounded explicit variants, so repeated runs choose the same source page.
    return copy.deepcopy(eligible[0]) if eligible else {}


def _adapt_trusted_navigation_to_visual_evidence(baseline_flow, evidence, branch):
    """Replace only the historical leaf after a visual AI proves the shared parent path."""
    flow = normalize_text_list(baseline_flow)
    evidence = evidence if isinstance(evidence, dict) else {}
    leaf = str(evidence.get("navigationLeaf") or "").strip()
    leaf_key = _navigation_action_target_key(f"点击「{leaf}」")
    target_key = _navigation_action_target_key(
        f"点击「{str(evidence.get('targetText') or '').strip()}」"
    )
    actions = [
        (index, key)
        for index, step in enumerate(flow)
        for key in [_navigation_action_target_key(step)]
        if key
    ]
    if not leaf_key or not actions:
        return flow, False
    existing_leaf_indexes = [
        index
        for index, key in actions
        if _navigation_target_keys_match(key, leaf_key)
    ]
    if existing_leaf_indexes:
        first_leaf_index = min(existing_leaf_indexes)
        first_target_check_index = next((
            index
            for index, step in enumerate(flow)
            if index < first_leaf_index
            and target_key
            and target_key in re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(step or "")).lower()
            and not _navigation_target_keys_match(_navigation_action_target_key(step), target_key)
        ), None)
        if first_target_check_index is None:
            return flow, False
        leaf_step = flow[first_leaf_index]
        reordered = [
            step for index, step in enumerate(flow)
            if index not in set(existing_leaf_indexes)
        ]
        reordered.insert(first_target_check_index, leaf_step)
        if (
            len(reordered) > 7
            or _source_navigation_has_alternative_destinations(reordered)
            or not _case_has_branch_execution_evidence({"steps": reordered}, branch)
        ):
            return flow, False
        return reordered, reordered != flow
    parent_keys = []
    for label in normalize_text_list(evidence.get("parentPath")):
        key = _navigation_action_target_key(label) or _navigation_action_target_key(f"点击「{label}」")
        if key and "首页" not in key:
            parent_keys.append(key)
    if not parent_keys:
        return flow, False
    matched_positions = []
    before_position = len(actions)
    for parent_key in reversed(parent_keys):
        candidates = [
            position for position in range(before_position)
            if _navigation_target_keys_match(actions[position][1], parent_key)
        ]
        if not candidates:
            return flow, False
        matched_position = max(candidates)
        matched_positions.append(matched_position)
        before_position = matched_position
    parent_action_position = matched_positions[0]
    parent_step_index = actions[parent_action_position][0]
    next_action_step_index = next(
        (step_index for step_index, _key in actions[parent_action_position + 1:]),
        len(flow),
    )
    historical_leaf_key = (
        actions[parent_action_position + 1][1]
        if parent_action_position + 1 < len(actions) else ""
    )
    stable_parent_waits = []
    for step in flow[parent_step_index + 1:next_action_step_index]:
        compact_step = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(step or "")).lower()
        if historical_leaf_key and historical_leaf_key in compact_step:
            continue
        stable_parent_waits.append(step)
    adapted = flow[:parent_step_index + 1] + stable_parent_waits + [f"点击「{leaf}」"]
    if (
        parent_step_index >= len(adapted)
        or len(adapted) < 2
        or len(adapted) > 7
        or _source_navigation_has_alternative_destinations(adapted)
        or not _case_has_branch_execution_evidence({"steps": adapted}, branch)
    ):
        return flow, False
    return adapted, adapted != flow


def _candidate_source_navigation_flow(case, target_terms, branch):
    """Keep the AI candidate's concrete path up to, but not including, the target control."""
    case = case if isinstance(case, dict) else {}
    normalized_targets = {
        re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(term or "")).lower()
        for term in (target_terms or [])
        if str(term or "").strip()
    }
    flow = []
    for raw_step in normalize_text_list(case.get("steps"))[:8]:
        step = str(raw_step or "").strip()
        compact = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", step).lower()
        if any(term and term in compact for term in normalized_targets):
            break
        if (
            "首页" in step
            and any(marker in step for marker in ("如当前不在", "若当前不在", "如果当前不在"))
            and "返回" in step
        ):
            continue
        flow.append(step)
    action_count = sum(1 for step in flow if _navigation_action_target_key(step))
    if (
        len(flow) < 2
        or action_count < 1
        or _source_navigation_has_alternative_destinations(flow)
        or not _case_has_branch_execution_evidence({"steps": flow}, branch)
    ):
        return []
    return flow


def _candidate_navigation_specificity(case, target_terms, branch):
    """Prefer a safe AI path that explicitly selects its current leaf over an assumed landing."""
    flow = _candidate_source_navigation_flow(case, target_terms, branch)
    keys = [key for key in (_navigation_action_target_key(step) for step in flow) if key]
    return len(keys), len(set(keys)), -len(flow)


def _adapt_trusted_navigation_to_candidate(
    baseline_flow,
    candidate_case,
    target_terms,
    branch,
):
    """Reuse the successful parent path while replacing a divergent historical leaf."""
    baseline_flow = normalize_text_list(baseline_flow)
    candidate_flow = _candidate_source_navigation_flow(candidate_case, target_terms, branch)
    baseline_actions = [
        (index, key)
        for index, step in enumerate(baseline_flow)
        for key in [_navigation_action_target_key(step)]
        if key
    ]
    candidate_actions = [
        (index, key)
        for index, step in enumerate(candidate_flow)
        for key in [_navigation_action_target_key(step)]
        if key
    ]
    if len(candidate_actions) < 2 or not baseline_actions:
        return baseline_flow, False

    positions = [-1]
    matched_count = 0
    matched_baseline_position = -1
    for _candidate_step_index, candidate_key in candidate_actions:
        next_positions = sorted({
            baseline_position
            for previous in positions
            for baseline_position in range(previous + 1, len(baseline_actions))
            if _navigation_target_keys_match(
                candidate_key,
                baseline_actions[baseline_position][1],
            )
        })
        if not next_positions:
            break
        positions = next_positions
        matched_count += 1
        matched_baseline_position = max(next_positions)
    if matched_count < 1 or matched_count >= len(candidate_actions):
        return baseline_flow, False

    baseline_cut = baseline_actions[matched_baseline_position][0]
    candidate_cut = candidate_actions[matched_count - 1][0]
    adapted = baseline_flow[:baseline_cut + 1] + candidate_flow[candidate_cut + 1:]
    if (
        len(adapted) < 2
        or len(adapted) > 7
        or _source_navigation_has_alternative_destinations(adapted)
        or not _case_has_branch_execution_evidence({"steps": adapted}, branch)
    ):
        return baseline_flow, False
    return adapted, adapted != baseline_flow


def _ensure_trusted_home_start_guard(flow, baseline, precondition=""):
    """Preserve a visible home-ready checkpoint before the first baseline tap."""
    normalized = normalize_text_list(flow)
    baseline = baseline if isinstance(baseline, dict) else {}
    start_page = str(baseline.get("startPage") or baseline.get("start_page") or "").strip()
    if not start_page:
        match = re.search(
            r"#\s*baseline\.start_page\s*:\s*(.+)",
            str(baseline.get("snippet") or ""),
        )
        if match:
            start_page = str(match.group(1) or "").strip().strip("\"'")
    start_context = f"{start_page}\n{precondition}"
    if "首页" not in start_context:
        return normalized
    early_steps = "\n".join(normalized[:2])
    has_home_guard = (
        ("首页" in early_steps and any(term in early_steps for term in ("等待", "稳定", "加载", "可见")))
        or ("启动" in early_steps and any(term in early_steps for term in ("App", "APP", "应用")))
    )
    if has_home_guard:
        return normalized
    return ["启动App并等待首页加载完成"] + normalized


def _source_navigation_has_alternative_destinations(flow, allow_terminal_wait_alternatives=False):
    """Reject source paths that leave the concrete destination for Runner to guess."""
    steps = normalize_text_list(flow)
    action_pattern = re.compile(
        r"^(?:(?:请|然后|再|依次|分别|逐一|每个|各个|所有|全部)\s*)*"
        r"(?:点击|点按|轻触|进入|打开|选择|切换|前往)"
    )
    for index, step in enumerate(steps):
        text = str(step or "").strip()
        if (
            text.startswith("等待")
            and allow_terminal_wait_alternatives
            and not any(action_pattern.match(str(item or "").strip()) for item in steps[index + 1:])
        ):
            continue
        if not re.match(
            r"^(?:(?:请|然后|再|依次|分别|逐一|每个|各个|所有|全部)\s*)*"
            r"(?:点击|点按|轻触|进入|打开|选择|切换|前往|等待)",
            text,
        ):
            continue
        compact = re.sub(r"\s+", "", text)
        if any(marker in compact for marker in (
            "任一", "任意", "任选", "其中一个", "其中任意一个",
        )):
            return True
        quoted = re.findall(r"[「『“\"'‘]([^」』”\"'’]+)[」』”\"'’]", text)
        if len(quoted) > 1 and any(term in compact for term in (
            "或", "/", "／", "依次", "分别", "逐一", "每个", "各个", "所有", "全部",
        )):
            return True
        if any(marker in compact for marker in ("依次", "分别", "逐一", "每个", "各个", "所有", "全部")) and any(
            separator in compact for separator in ("、", "和", "及")
        ):
            return True
    return False


def _baseline_navigation_matches_landing_source(flow, landing_tail, branch):
    """Do not join a sibling baseline to a tail that names a different source leaf."""
    flow_text = "\n".join(normalize_text_list(flow))
    tail_text = str((landing_tail or {}).get("assertionTarget") or "")
    source_pages = []
    for match in re.finditer(r"已离开(?:[「『\"']([^」』\"']+)[」』\"']|([^，；。]{1,24}?))页", tail_text):
        page = str(match.group(1) or match.group(2) or "").strip()
        if page and page not in source_pages:
            source_pages.append(page)
    branch_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(branch or "")).lower()
    flow_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", flow_text).lower()
    for page in source_pages:
        page_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", page).lower()
        if not page_key:
            continue
        if page_key in flow_key or (branch_key and page_key in branch_key):
            continue
        return False
    return True


def _bounded_convergence_evidence(
    normalized,
    automatic_records,
    audit,
    selected_baselines=None,
    manual_records=None,
):
    """Compose a trusted source-page path with an AI-authored bounded landing tail."""
    normalized = normalized if isinstance(normalized, dict) else {}
    audit = audit if isinstance(audit, dict) else {}
    automatic_records = [item for item in (automatic_records or []) if isinstance(item, dict)]
    manual_records = [item for item in (manual_records or []) if isinstance(item, dict)]
    missing_checks = [
        item for item in (audit.get("missingAcceptanceChecks") or [])
        if isinstance(item, dict)
    ]
    unresolved_case_ids = {
        str(item or "").strip()
        for item in (audit.get("unresolvedAutomaticCaseIds") or [])
        if str(item or "").strip()
    }
    unresolved_requirement_ids = set()
    for record in automatic_records:
        compact = (record or {}).get("compact") if isinstance((record or {}).get("compact"), dict) else {}
        if str(compact.get("case_id") or "").strip() not in unresolved_case_ids:
            continue
        raw = (record or {}).get("raw") if isinstance((record or {}).get("raw"), dict) else {}
        unresolved_requirement_ids.update(_source_case_requirement_ids(raw))
    evidence_checks = list(missing_checks)
    known_check_ids = {
        str(item.get("id") or "").strip() for item in evidence_checks
        if str(item.get("id") or "").strip()
    }
    for check in (normalized.get("analysis") or {}).get("requirement_acceptance_checks") or []:
        if not isinstance(check, dict):
            continue
        check_id = str(check.get("id") or "").strip()
        if (
            str(check.get("requirementId") or "").strip() in unresolved_requirement_ids
            and check_id
            and check_id not in known_check_ids
        ):
            evidence_checks.append(check)
            known_check_ids.add(check_id)
    reachability_checks = [
        item for item in missing_checks
        if str(item.get("kind") or "").strip().lower() == "reachability"
    ]
    sources = {}
    for case in normalized.get("cases") or []:
        if not isinstance(case, dict):
            continue
        plan = case.get("ai_case_plan") if isinstance(case.get("ai_case_plan"), dict) else {}
        if (
            str(case.get("executionLevel") or case.get("execution_level") or "").strip().lower() == "executable"
            and plan.get("baselineGrounded") is True
            and plan.get("pathPlanApplied") is True
        ):
            for requirement_id in _acceptance_requirement_ids([
                case.get("coverage"), case.get("requirementRefs"), case.get("requirement_refs"),
            ]):
                sources.setdefault(requirement_id, []).append(case)
    evidence_by_id = {}
    requirement_ids = list(dict.fromkeys(
        str(item.get("requirementId") or "").strip()
        for item in evidence_checks
        if str(item.get("requirementId") or "").strip()
    ))
    source_candidate_records = automatic_records + manual_records
    for requirement_id in requirement_ids:
        source_checks = [
            item for item in evidence_checks
            if str(item.get("requirementId") or "").strip() == requirement_id
            and str(item.get("kind") or "").strip().lower() in ("visibility", "relation", "copy")
        ]
        if not source_checks:
            continue
        branch = next((
            str(item.get("branch") or "").strip()
            for item in source_checks
            if str(item.get("branch") or "").strip()
        ), "")
        branch_baseline = _trusted_selected_baseline_for_branch(selected_baselines, branch)
        if not branch_baseline:
            continue
        target_terms = _acceptance_target_terms("；".join(
            str(item.get("text") or "").strip() for item in source_checks
        ))
        navigation_flow = _trusted_baseline_source_navigation_flow(
            branch_baseline,
            target_terms,
            branch,
        )
        if (
            len(navigation_flow) < 2
            or _source_navigation_has_alternative_destinations(navigation_flow)
        ):
            continue
        matching_records = [
            item for item in source_candidate_records
            if requirement_id in _acceptance_requirement_ids([
                ((item or {}).get("raw") or {}).get("coverage"),
                ((item or {}).get("raw") or {}).get("requirementRefs"),
                ((item or {}).get("raw") or {}).get("requirement_refs"),
            ])
            and _case_has_branch_execution_evidence((item or {}).get("raw") or {}, branch)
            and not _case_has_deep_external_action((item or {}).get("raw") or {})
        ]
        matching_records.sort(key=lambda item: (
            str(((item or {}).get("compact") or {}).get("case_id") or "").strip() not in unresolved_case_ids,
            -sum(
                1 for check in source_checks
                if case_covers_requirement_acceptance((item or {}).get("raw") or {}, check)
            ),
            tuple(
                -value for value in _candidate_navigation_specificity(
                    (item or {}).get("raw") or {},
                    target_terms,
                    branch,
                )[:2]
            ),
            len(normalize_text_list(((item or {}).get("raw") or {}).get("steps"))),
        ))
        if not matching_records:
            continue
        source_record = matching_records[0]
        source_case = (source_record or {}).get("raw") or {}
        navigation_flow, current_leaf_adapted = _adapt_trusted_navigation_to_candidate(
            navigation_flow,
            source_case,
            target_terms,
            branch,
        )
        current_leaf_evidence = _current_visual_page_evidence_for_case(
            normalized,
            source_case,
            str(((source_record or {}).get("compact") or {}).get("case_id") or "").strip(),
            branch,
            target_terms,
        )
        navigation_flow, visual_leaf_adapted = _adapt_trusted_navigation_to_visual_evidence(
            navigation_flow,
            current_leaf_evidence,
            branch,
        )
        current_leaf_adapted = current_leaf_adapted or visual_leaf_adapted
        precondition = _bounded_candidate_precondition(source_case, branch_baseline)
        navigation_flow = _ensure_trusted_home_start_guard(
            navigation_flow,
            branch_baseline,
            precondition,
        )
        requirement_refs = normalize_text_list(
            source_case.get("requirementRefs")
            or source_case.get("requirement_refs")
            or source_case.get("coverage")
        )[:8]
        case_id = str(((source_record or {}).get("compact") or {}).get("case_id") or "").strip()
        baseline_id = str(branch_baseline.get("id") or "").strip()
        source_page_checks = [
            item for item in ((normalized.get("analysis") or {}).get("requirement_acceptance_checks") or [])
            if isinstance(item, dict)
            and str(item.get("requirementId") or "").strip() == requirement_id
            and str(item.get("kind") or "").strip().lower() in ("visibility", "relation", "copy")
        ]
        source_assertion_candidates = []
        for value in (
            source_case.get("assertions"),
            source_case.get("expected"),
            source_case.get("expected_result"),
            source_case.get("expectedResult"),
            (source_case.get("ai_case_plan") or {}).get("assertionTarget")
            if isinstance(source_case.get("ai_case_plan"), dict) else None,
        ):
            source_assertion_candidates.extend(normalize_text_list(value))
        preserved_assertions = []
        preserved_check_indexes = set()
        for assertion in dict.fromkeys(source_assertion_candidates):
            assertion_probe = {
                "assertions": [assertion],
                "requirementRefs": requirement_refs,
            }
            covered_indexes = {
                index for index, check in enumerate(source_page_checks)
                if case_covers_requirement_acceptance(assertion_probe, check)
            }
            if not covered_indexes.difference(preserved_check_indexes):
                continue
            preserved_assertions.append(assertion)
            preserved_check_indexes.update(covered_indexes)
        contract_items = preserved_assertions[:4]
        contract_items.extend(
            str(item.get("text") or "").strip()
            for item in source_checks
            if str(item.get("text") or "").strip()
        )
        check_contract = "；".join(dict.fromkeys(contract_items))
        target_label = str((target_terms or [""])[0] or "").strip()
        assertion_target = (
            f"「{target_label}」满足以下要求：{check_contract}"
            if target_label else check_contract
        )
        if not case_id or not baseline_id or not precondition or not requirement_refs or not assertion_target:
            continue
        flow = navigation_flow + [f"等待并校验{assertion_target}"]
        probe = {
            "steps": flow,
            "assertions": [assertion_target],
            "requirementRefs": requirement_refs,
            "ai_case_plan": {"baselineGrounded": True, "pathPlanApplied": True},
        }
        if len(flow) > 8:
            continue
        covered_checks = [
            item for item in source_checks
            if case_covers_requirement_acceptance(probe, item)
        ]
        if not covered_checks:
            continue
        evidence_by_id[case_id] = {
            "eligible": True,
            "kind": "source_ui_assertion",
            "title": str(((source_record or {}).get("compact") or {}).get("title") or "").strip(),
            "sourceCaseId": case_id,
            "tailSourceCaseId": "",
            "source": "selected_branch_baseline_ui_assertion",
            "baselineId": baseline_id,
            "precondition": precondition,
            "flow": flow,
            "assertionTarget": assertion_target,
            "requirementRefs": requirement_refs,
            "originLevel": str(((source_record or {}).get("compact") or {}).get("originLevel") or ""),
            "manualPromotionEligible": (
                str(((source_record or {}).get("compact") or {}).get("originLevel") or "").strip().lower()
                == "manual"
            ),
            "currentLeafAdapted": current_leaf_adapted,
            "currentLeafSourceCaseId": (
                str(current_leaf_evidence.get("caseId") or case_id).strip()
                if current_leaf_adapted else ""
            ),
            "currentLeafEvidenceSource": (
                current_leaf_evidence.get("source") if visual_leaf_adapted else "ai_candidate"
            ) if current_leaf_adapted else "",
            "currentLeafEvidence": current_leaf_evidence if visual_leaf_adapted else {},
            "acceptanceCheckIds": [
                str(item.get("id") or "").strip()
                for item in covered_checks
                if str(item.get("id") or "").strip()
            ],
        }
    donor_records = automatic_records + manual_records
    for check in reachability_checks:
        requirement_id = str(check.get("requirementId") or "").strip()
        branch = str(check.get("branch") or "").strip()
        source_case = next((
            case for case in (sources.get(requirement_id) or [])
            if _case_has_branch_execution_evidence(case, branch)
        ), None)
        source_alternates = [
            item for item in source_candidate_records
            if requirement_id in _acceptance_requirement_ids([
                ((item or {}).get("raw") or {}).get("coverage"),
                ((item or {}).get("raw") or {}).get("requirementRefs"),
                ((item or {}).get("raw") or {}).get("requirement_refs"),
            ])
            and _case_has_branch_execution_evidence((item or {}).get("raw") or {}, branch)
            and not _case_has_deep_external_action((item or {}).get("raw") or {})
        ]
        source_alternates.sort(
            key=lambda item: _candidate_navigation_specificity(
                (item or {}).get("raw") or {},
                _acceptance_target_terms(check.get("text")),
                branch,
            ),
            reverse=True,
        )
        strongest_alternate = source_alternates[0] if source_alternates else None
        if source_case and strongest_alternate and _candidate_navigation_specificity(
            (strongest_alternate or {}).get("raw") or {},
            _acceptance_target_terms(check.get("text")),
            branch,
        )[:2] > _candidate_navigation_specificity(
            source_case or {},
            _acceptance_target_terms(check.get("text")),
            branch,
        )[:2]:
            source_case = (strongest_alternate or {}).get("raw") or {}
        source_evidence_plan = next((
            item for item in evidence_by_id.values()
            if isinstance(item, dict)
            and str(item.get("kind") or "") == "source_ui_assertion"
            and requirement_id in _acceptance_requirement_ids(item.get("requirementRefs"))
        ), None)
        branch_baseline = _trusted_selected_baseline_for_branch(selected_baselines, branch)
        requirement_missing_checks = [
            item for item in missing_checks
            if str(item.get("requirementId") or "").strip() == requirement_id
        ]
        matching_automatic_records = [
            item for item in automatic_records
            if requirement_id in _acceptance_requirement_ids([
                ((item or {}).get("raw") or {}).get("coverage"),
                ((item or {}).get("raw") or {}).get("requirementRefs"),
                ((item or {}).get("raw") or {}).get("requirement_refs"),
            ])
            and _case_has_branch_execution_evidence((item or {}).get("raw") or {}, branch)
        ]
        source_only_checks = [
            item for item in requirement_missing_checks
            if str(item.get("kind") or "").strip().lower() in ("visibility", "relation", "copy")
        ]
        matching_automatic_records.sort(key=lambda item: (
            -sum(
                1 for source_check in source_only_checks
                if case_covers_requirement_acceptance((item or {}).get("raw") or {}, source_check)
            ),
            str(((item or {}).get("compact") or {}).get("currentLevel") or "").strip().lower() == "executable",
            len(normalize_text_list(((item or {}).get("raw") or {}).get("steps"))),
        ))
        for donor_record in donor_records:
            raw_case = (donor_record or {}).get("raw") or {}
            donor_case_id = str(((donor_record or {}).get("compact") or {}).get("case_id") or "").strip()
            current_leaf_adapted = False
            current_leaf_source_case_id = ""
            current_leaf_evidence_source = ""
            current_leaf_evidence = {}
            requirement_refs = normalize_text_list(
                raw_case.get("requirementRefs") or raw_case.get("requirement_refs") or raw_case.get("coverage")
            )[:8]
            if not requirement_refs or requirement_id not in _acceptance_requirement_ids([
                raw_case.get("coverage"), raw_case.get("requirementRefs"), raw_case.get("requirement_refs"),
            ]) or not _case_has_branch_execution_evidence(raw_case, branch):
                continue
            targets = _acceptance_target_terms(check.get("text"))
            landing_tail = _bounded_landing_tail(raw_case, targets)
            if not landing_tail:
                continue
            landing_tail["sourceCaseId"] = donor_case_id
            if not _bounded_landing_tail_is_executable(landing_tail, requirement_refs):
                supporting_tails = [landing_tail]
                merged_tail = _merge_bounded_landing_tails(supporting_tails)
                if _bounded_landing_tail_is_executable(merged_tail, requirement_refs):
                    landing_tail = merged_tail
                else:
                    for support_record in donor_records:
                        if support_record is donor_record:
                            continue
                        support_case = (support_record or {}).get("raw") or {}
                        support_tail = _bounded_landing_tail(support_case, targets)
                        if not support_tail:
                            continue
                        support_tail["sourceCaseId"] = str(
                            ((support_record or {}).get("compact") or {}).get("case_id") or ""
                        ).strip()
                        supporting_tails.append(support_tail)
                        merged_tail = _merge_bounded_landing_tails(supporting_tails)
                        if _bounded_landing_tail_is_executable(merged_tail, requirement_refs):
                            landing_tail = merged_tail
                            break
                if not _bounded_landing_tail_is_executable(landing_tail, requirement_refs):
                    continue
            source_evidence_case = source_case
            source_record = None
            source_evidence_includes_assertion = False
            if source_case:
                source_plan = source_case.get("ai_case_plan") or {}
                source_navigation_flow = normalize_text_list(source_plan.get("flow") or source_case.get("steps"))
                navigation_flow = list(source_navigation_flow)
                baseline_id = str(source_plan.get("baselineId") or "").strip()
                precondition = str(source_plan.get("precondition") or "").strip()
                source_case_id = str(source_case.get("case_id") or "").strip()
                source_record = next((
                    item for item in source_candidate_records
                    if (item or {}).get("raw") is source_case
                    or str(((item or {}).get("compact") or {}).get("case_id") or "").strip() == source_case_id
                ), None)
                evidence_source = "executable_source_case"
                selected_baseline = next((
                    item for item in (selected_baselines or [])
                    if isinstance(item, dict)
                    and str(item.get("id") or "").strip() == baseline_id
                ), None)
                if not selected_baseline and branch_baseline:
                    selected_baseline = branch_baseline
                    baseline_id = str(branch_baseline.get("id") or "").strip()
                    precondition = (
                        precondition
                        or _bounded_candidate_precondition(source_case, branch_baseline)
                    )
                baseline_navigation_flow = _trusted_baseline_source_navigation_flow(
                    selected_baseline,
                    targets,
                    branch,
                )
                if baseline_navigation_flow and _baseline_navigation_matches_landing_source(
                    baseline_navigation_flow,
                    landing_tail,
                    branch,
                ):
                    baseline_navigation_flow, current_leaf_adapted = (
                        _adapt_trusted_navigation_to_candidate(
                            baseline_navigation_flow,
                            source_evidence_case,
                            targets,
                            branch,
                        )
                    )
                    if current_leaf_adapted:
                        current_leaf_source_case_id = source_case_id
                    start_guard = next((
                        step for step in source_navigation_flow
                        if "启动" in step and any(term in step for term in ("等待", "加载", "首页"))
                    ), "")
                    navigation_flow = ([start_guard] if start_guard else []) + baseline_navigation_flow
                    evidence_source = "selected_baseline_actions"
            elif source_evidence_plan:
                source_case_id = str(source_evidence_plan.get("sourceCaseId") or "").strip()
                source_record = next((
                    item for item in source_candidate_records
                    if str(((item or {}).get("compact") or {}).get("case_id") or "").strip()
                    == source_case_id
                ), None)
                if not source_record:
                    continue
                source_evidence_case = (source_record or {}).get("raw") or {}
                navigation_flow = normalize_text_list(source_evidence_plan.get("flow"))
                baseline_id = str(source_evidence_plan.get("baselineId") or "").strip()
                precondition = str(source_evidence_plan.get("precondition") or "").strip()
                evidence_source = "selected_branch_baseline_ui_assertion"
                source_evidence_includes_assertion = True
                current_leaf_adapted = source_evidence_plan.get("currentLeafAdapted") is True
                current_leaf_source_case_id = str(
                    source_evidence_plan.get("currentLeafSourceCaseId") or ""
                ).strip()
            else:
                if not branch_baseline or not matching_automatic_records:
                    continue
                source_record = matching_automatic_records[0]
                source_evidence_case = (source_record or {}).get("raw") or {}
                navigation_flow = normalize_text_list(source_evidence_case.get("steps"))
                source_click_index = next((
                    index for index, step in enumerate(navigation_flow)
                    if "点击" in step and any(term in step for term in targets)
                ), -1)
                if source_click_index >= 0:
                    navigation_flow = navigation_flow[:source_click_index]
                precondition = (
                    _bounded_candidate_precondition(source_evidence_case, branch_baseline)
                    or _bounded_candidate_precondition(raw_case, branch_baseline)
                )
                baseline_id = str(branch_baseline.get("id") or "").strip()
                source_case_id = str(
                    ((source_record or {}).get("compact") or {}).get("case_id") or ""
                ).strip()
                evidence_source = "selected_branch_baseline"
            current_leaf_evidence = _current_visual_page_evidence_for_case(
                normalized,
                source_evidence_case,
                source_case_id,
                branch,
                targets,
            )
            navigation_flow, visual_leaf_adapted = _adapt_trusted_navigation_to_visual_evidence(
                navigation_flow,
                current_leaf_evidence,
                branch,
            )
            if visual_leaf_adapted:
                current_leaf_adapted = True
                current_leaf_source_case_id = str(
                    current_leaf_evidence.get("caseId") or source_case_id
                ).strip()
                current_leaf_evidence_source = current_leaf_evidence.get("source") or "visual_grounder"
            elif current_leaf_adapted:
                current_leaf_evidence_source = "ai_candidate"
            navigation_flow = _ensure_trusted_home_start_guard(
                navigation_flow,
                branch_baseline,
                precondition,
            )
            if _source_navigation_has_alternative_destinations(navigation_flow):
                continue
            donor_is_automatic = any(donor_record is item for item in automatic_records)
            donor_requirement_ids = set(_acceptance_requirement_ids([
                raw_case.get("coverage"),
                raw_case.get("requirementRefs"),
                raw_case.get("requirement_refs"),
            ]))
            shared_cross_branch_tail = len(donor_requirement_ids) > 1 and source_record is not None
            target_record = (
                source_record
                if shared_cross_branch_tail
                else (donor_record if donor_is_automatic else source_record)
            )
            case_id = str(((target_record or {}).get("compact") or {}).get("case_id") or "").strip()
            if not case_id or (not donor_is_automatic and not source_record):
                continue
            if shared_cross_branch_tail:
                requirement_refs = normalize_text_list(
                    source_evidence_case.get("requirementRefs")
                    or source_evidence_case.get("requirement_refs")
                    or source_evidence_case.get("coverage")
                )[:8]
                if requirement_id not in _acceptance_requirement_ids(requirement_refs):
                    continue
            if not baseline_id or not precondition or len(navigation_flow) < 2:
                continue
            if (
                navigation_flow
                and navigation_flow[-1].startswith(("校验", "验证", "检查", "断言"))
                and any(term in navigation_flow[-1] for term in targets)
            ):
                navigation_flow.pop()
            source_navigation_probe = {
                "steps": navigation_flow,
                "assertions": normalize_text_list(source_evidence_case.get("assertions")),
                "requirementRefs": requirement_refs,
            }
            supplemental_checks = [
                item for item in requirement_missing_checks
                if str(item.get("kind") or "").strip().lower() in ("visibility", "relation", "copy")
                and not case_covers_requirement_acceptance(source_navigation_probe, item)
            ]
            supplemental_text = "；".join(
                str(item.get("text") or "").strip()
                for item in supplemental_checks
                if str(item.get("text") or "").strip()
            )
            source_outcomes = normalize_text_list(
                source_evidence_case.get("assertions")
                or source_evidence_case.get("expected_result")
                or source_evidence_case.get("expected")
            )
            source_assertion_steps = (
                [f"校验{source_outcomes[0]}"]
                if source_outcomes and not source_evidence_includes_assertion else []
            )
            if (
                source_assertion_steps
                and navigation_flow
                and str(navigation_flow[-1] or "").strip().startswith("等待")
            ):
                # The following visible-source assertion is itself a bounded wait;
                # keeping both would spend a second model observation on one state.
                navigation_flow = navigation_flow[:-1]
            landing_flow = normalize_text_list(landing_tail.get("flow"))
            full_evidence_flow = (
                navigation_flow
                + ([supplemental_text] if supplemental_text else [])
                + source_assertion_steps
                + landing_flow
            )
            flow = list(full_evidence_flow)
            assertion_target = landing_tail["assertionTarget"]
            if (
                len(flow) > 8
                and landing_flow
                and not _navigation_action_target_key(landing_flow[-1])
                and assertion_target
            ):
                # assertionTarget becomes the final aiWaitFor/aiAssert pair; do not
                # duplicate the same landing observation inside the human flow.
                flow = flow[:-1]
            probe = {
                "steps": full_evidence_flow,
                "assertions": [assertion_target],
                "requirementRefs": requirement_refs,
                "ai_case_plan": {"baselineGrounded": True, "pathPlanApplied": True},
            }
            if len(flow) > 8 or not _case_is_bounded_external_landing_check(probe):
                continue
            covered_checks = [
                item for item in requirement_missing_checks
                if case_covers_requirement_acceptance(probe, item)
            ]
            if str(check.get("id") or "").strip() not in {
                str(item.get("id") or "").strip() for item in covered_checks
            }:
                continue
            evidence_by_id[case_id] = {
                "eligible": True,
                "kind": "bounded_landing",
                "title": (
                    f"{branch}{targets[0]}点击后首个可见页校验"
                    if branch and targets
                    else str(((target_record or {}).get("compact") or {}).get("title") or "").strip()
                ),
                "sourceCaseId": source_case_id,
                "tailSourceCaseId": donor_case_id,
                "source": evidence_source,
                "sharedTailBoundToBranchSource": shared_cross_branch_tail,
                "baselineId": baseline_id,
                "precondition": precondition,
                "flow": flow,
                "assertionTarget": assertion_target,
                "requirementRefs": requirement_refs,
                "originLevel": str(((target_record or {}).get("compact") or {}).get("originLevel") or ""),
                "manualPromotionEligible": (
                    str(((target_record or {}).get("compact") or {}).get("originLevel") or "").strip().lower()
                    == "manual"
                ),
                "currentLeafAdapted": current_leaf_adapted,
                "currentLeafSourceCaseId": current_leaf_source_case_id,
                "currentLeafEvidenceSource": current_leaf_evidence_source,
                "currentLeafEvidence": current_leaf_evidence if visual_leaf_adapted else {},
                "landingEvidenceCaseIds": normalize_text_list(
                    landing_tail.get("sourceCaseIds") or donor_case_id
                ),
                "acceptanceCheckIds": [
                    str(item.get("id") or "").strip()
                    for item in covered_checks
                    if str(item.get("id") or "").strip()
                ],
            }
            break
    return evidence_by_id


def _focus_executable_convergence_candidates(
    normalized,
    automatic_records,
    manual_records,
    planning_context,
    selected_baselines=None,
):
    """Limit the final AI pass to the current portfolio and one alternate per gap."""
    context = copy.deepcopy(planning_context) if isinstance(planning_context, dict) else {}
    audit = context.get("portfolioAudit") if isinstance(context.get("portfolioAudit"), dict) else {}
    if str(context.get("pass") or "").strip() != "coverage_convergence":
        automatic = [item["compact"] for item in automatic_records]
        manual = [item["compact"] for item in manual_records]
        return automatic, manual, context, {
            "enabled": False,
            "fullCandidateCount": len(automatic) + len(manual),
            "focusedCandidateCount": len(automatic) + len(manual),
            "focusedCandidateIds": [item.get("case_id") for item in automatic + manual],
            "outsideFocusCandidateIds": [],
        }

    preserved_executable_ids = {
        str(item or "").strip()
        for item in (audit.get("executableCaseIds") or [])
        if str(item or "").strip()
    }
    focus_ids = {
        str(item or "").strip()
        for item in (audit.get("unresolvedAutomaticCaseIds") or [])
        if str(item or "").strip()
    }
    missing_points = normalize_text_list(audit.get("missingRequirementPoints"))
    for missing_check in audit.get("missingAcceptanceChecks") or []:
        if not isinstance(missing_check, dict):
            continue
        descriptor = str(
            missing_check.get("descriptor")
            or requirement_acceptance_descriptor(missing_check)
        ).strip()
        if descriptor and descriptor not in missing_points:
            missing_points.append(descriptor)
    for point in missing_points:
        for record in automatic_records:
            candidate_id = str(record["compact"].get("case_id") or "").strip()
            if candidate_id in focus_ids:
                continue
            if case_matches_requirement(record["raw"], point):
                focus_ids.add(candidate_id)
                break
    bounded_evidence_by_id = _bounded_convergence_evidence(
        normalized,
        automatic_records,
        audit,
        selected_baselines=selected_baselines,
        manual_records=manual_records,
    )
    focus_ids.update(bounded_evidence_by_id)
    focused_automatic = [
        item for item in automatic_records
        if not focus_ids or str(item["compact"].get("case_id") or "").strip() in focus_ids
    ]
    if not focused_automatic:
        focused_automatic = list(automatic_records)
    target_count = max(0, safe_int(audit.get("targetExecutableCount"), 0))
    executable_count = max(0, safe_int(audit.get("executableCount"), 0))
    focused_manual = []
    selected_manual_ids = set()
    for record in focused_automatic:
        candidate_id = str(record["compact"].get("case_id") or "").strip()
        if candidate_id in bounded_evidence_by_id:
            record["compact"]["convergenceEvidence"] = bounded_evidence_by_id[candidate_id]

    # One alternate per uncovered requirement keeps the final request bounded while
    # still allowing AI to recover a better candidate than the unresolved automatic one.
    focus_points = missing_points
    for point in focus_points:
        matching_manual_records = [
            record for record in manual_records
            if case_matches_requirement(record["raw"], point)
        ]
        matching_manual_records.sort(key=lambda record: (
            str(record["compact"].get("case_id") or "").strip() not in bounded_evidence_by_id,
            len(normalize_text_list(record["compact"].get("steps"))),
        ))
        for record in matching_manual_records:
            candidate_id = str(record["compact"].get("case_id") or "").strip()
            if candidate_id in selected_manual_ids:
                continue
            focused_manual.append(record)
            selected_manual_ids.add(candidate_id)
            break

    for record in focused_manual:
        candidate_id = str(record["compact"].get("case_id") or "").strip()
        if candidate_id in bounded_evidence_by_id:
            record["compact"]["convergenceEvidence"] = bounded_evidence_by_id[candidate_id]

    candidate_selection_mode = "missing_requirement_alternates" if focus_points else "none"

    automatic = [item["compact"] for item in focused_automatic]
    manual = [item["compact"] for item in focused_manual]
    full_ids = [
        str(item["compact"].get("case_id") or "").strip()
        for item in automatic_records + manual_records
        if str(item["compact"].get("case_id") or "").strip()
    ]
    focused_ids = [
        str(item.get("case_id") or "").strip()
        for item in automatic + manual
        if str(item.get("case_id") or "").strip()
    ]
    focus = {
        "enabled": True,
        "policy": "platform_preserves_executable_ai_resolves_gaps_one_manual_alternate_per_requirement",
        "fullCandidateCount": len(automatic_records) + len(manual_records),
        "focusedCandidateCount": len(automatic) + len(manual),
        "focusedAutomaticCount": len(automatic),
        "focusedManualCount": len(manual),
        "focusedCandidateIds": focused_ids,
        "preservedExecutableCandidateIds": sorted(preserved_executable_ids),
        "outsideFocusCandidateIds": [item for item in full_ids if item not in set(focused_ids)],
        "missingRequirementPoints": missing_points[:12],
        "targetExecutableCount": target_count,
        "currentExecutableCount": executable_count,
        "candidateSelectionMode": candidate_selection_mode,
        "boundedLandingCandidateIds": sorted(bounded_evidence_by_id),
        "boundedEvidenceCandidateIds": sorted(bounded_evidence_by_id),
    }
    context["focus"] = focus
    return automatic, manual, context, focus


def _compact_executable_convergence_context(analysis, source_evidence):
    """Keep the final AI pass focused on acceptance gaps and usable evidence."""
    analysis = analysis if isinstance(analysis, dict) else {}
    source_evidence = source_evidence if isinstance(source_evidence, dict) else {}
    compact_analysis = {
        key: copy.deepcopy(analysis.get(key))
        for key in (
            "requirement_points",
            "requirement_acceptance_checks",
            "requirement_contract",
            "visible_outcomes",
            "visual_notes",
            "ui_notes",
        )
        if analysis.get(key) not in (None, "", [], {})
    }
    compact_source = {
        key: copy.deepcopy(source_evidence.get(key))
        for key in (
            "mode",
            "requirementText",
            "figmaSoftEvidence",
            "visualBatchJudgements",
            "figmaPageCount",
            "figmaImageCount",
            "executionContext",
            "policy",
        )
        if source_evidence.get(key) not in (None, "", [], {})
    }
    for key in ("requirementText", "figmaSoftEvidence"):
        if isinstance(compact_source.get(key), str):
            compact_source[key] = compact_source[key][:6000]
    return compact_analysis, compact_source


def _existing_executable_plan_item(case, case_id, title):
    """Rehydrate a previously approved path when a focused convergence omits it."""
    case = case if isinstance(case, dict) else {}
    case_plan = case.get("ai_case_plan") if isinstance(case.get("ai_case_plan"), dict) else {}
    assertions = normalize_text_list(case.get("assertions") or case.get("expected_result"))
    preconditions = normalize_text_list(case.get("preconditions"))
    return {
        "caseId": case_id,
        "title": title,
        "baselineId": str(case_plan.get("baselineId") or "").strip(),
        "baselineGrounded": case_plan.get("baselineGrounded") is True,
        "precondition": str(case_plan.get("precondition") or (preconditions[0] if preconditions else "")).strip(),
        "flow": normalize_text_list(case_plan.get("flow") or case.get("steps"))[:8],
        "assertionTarget": str(case_plan.get("assertionTarget") or (assertions[0] if assertions else "")).strip(),
        "requirementRefs": normalize_text_list(
            case.get("requirementRefs") or case.get("requirement_refs") or case.get("coverage")
        )[:8],
        "executableReason": str(
            case_plan.get("executableReason") or case.get("automation_reason") or "保留上一轮已通过门禁的可执行短链路"
        ).strip(),
        "batch": case_plan.get("batch") or ("smoke" if is_smoke_case(case) else "remaining"),
        "preservedFromInitialPlan": True,
    }


def _ground_executable_plan_items(items, candidate_by_id, candidate_by_title):
    grounded = []
    rejected = 0
    seen = set()
    for raw_item in items or []:
        item = raw_item if isinstance(raw_item, dict) else {"caseId": str(raw_item or "").strip()}
        requested_case_id = str(item.get("caseId") or item.get("case_id") or item.get("id") or "").strip()
        requested_title = str(item.get("title") or item.get("caseTitle") or "").strip()
        source_case = candidate_by_id.get(requested_case_id) or candidate_by_title.get(requested_title)
        if source_case is None and requested_case_id:
            source_case = candidate_by_title.get(requested_case_id)
        if source_case is None:
            rejected += 1
            continue
        case_id = str(source_case.get("case_id") or "").strip()
        if case_id in seen:
            continue
        seen.add(case_id)
        normalized_item = dict(item)
        normalized_item["caseId"] = case_id
        normalized_item["title"] = source_case.get("title") or ""
        grounded.append(normalized_item)
    return grounded, rejected


def _convergence_evidence_fallback_plan(
    normalized,
    candidates,
    candidate_eligibility_by_id,
    allowed_baseline_ids,
    verified_baseline_ids,
    planning_context,
    convergence_focus,
    trace,
    error,
):
    """Keep validated upstream AI evidence usable when the final AI call is unavailable."""
    if (
        str((planning_context or {}).get("pass") or "").strip() != "coverage_convergence"
        or not candidate_eligibility_by_id
    ):
        return None
    candidate_by_id = {
        str(item.get("case_id") or "").strip(): item
        for item in candidates or []
        if str(item.get("case_id") or "").strip()
    }
    eligible_ids = {
        case_id for case_id, evidence in candidate_eligibility_by_id.items()
        if case_id in candidate_by_id
        and isinstance(evidence, dict)
        and evidence.get("eligible") is True
        and str(evidence.get("baselineId") or "").strip() in allowed_baseline_ids
        and normalize_text_list(evidence.get("acceptanceCheckIds"))
    }
    if not eligible_ids:
        return None
    existing_cases = []
    for case in normalized.get("cases") or []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or "").strip()
        if (
            case_id
            and case_id not in eligible_ids
            and str(case.get("executionLevel") or "").strip().lower() == "executable"
        ):
            existing_cases.append(_existing_executable_plan_item(
                case,
                case_id,
                str(case.get("title") or "").strip(),
            ))
    evidence_candidates = [
        {
            "caseId": case_id,
            "title": candidate_by_id[case_id].get("title") or "",
            "reason": (
                "最终收敛 AI 不可用；保留为待复核分类，"
                "仅由平台已验证的同分支基线与 AI 有界首屏证据决定是否接管"
            ),
            "requirementRefs": candidate_by_id[case_id].get("requirementRefs") or [],
        }
        for case_id in sorted(eligible_ids)
    ]
    fallback_trace = dict(trace or {})
    fallback_trace.update({
        "fallback": True,
        "evidence_fallback": True,
        "evidence_fallback_case_count": len(evidence_candidates),
        "error": str(error or "final_convergence_unavailable"),
    })
    return {
        "cases": existing_cases,
        "needs_review_cases": evidence_candidates,
        "draft_cases": [],
        "manual_cases": [],
        "review": {
            "planning_reason": (
                "最终收敛 AI 调用未完成；不增加模型重试，仅对已通过同分支成功基线、"
                "显式验收映射及来源页可见断言/有界外部首屏检查的上游 AI 候选启用证据降级"
            ),
        },
        "authoritative": False,
        "evidenceFallback": True,
        "trace": fallback_trace,
        "allowedBaselineIds": sorted(allowed_baseline_ids),
        "verifiedBaselineIds": sorted(verified_baseline_ids or []),
        "requirementPoints": normalize_text_list(
            (normalized.get("analysis") or {}).get("requirement_points")
        )[:12],
        "planningContext": planning_context if isinstance(planning_context, dict) else {},
        "focusedCandidateIds": convergence_focus.get("focusedCandidateIds") or [],
        "convergenceFocus": convergence_focus,
        "candidateEligibilityById": {
            case_id: copy.deepcopy(candidate_eligibility_by_id[case_id])
            for case_id in sorted(eligible_ids)
        },
    }


def call_skill_executable_yaml_planner(
    title,
    module,
    payload,
    selected_baselines,
    scope_plan,
    model_config=None,
    source_evidence=None,
    planning_context=None,
):
    """Plan executable cases before YAML conversion. Fallback preserves current payload."""
    normalized = normalize_cases_payload(payload)
    compact_baselines = [
        _compact_baseline_candidate(item, idx)
        for idx, item in enumerate(selected_baselines or [])
    ]
    verified_baseline_ids = {
        str(item.get("id") or "").strip()
        for item in compact_baselines
        if str(item.get("id") or "").strip()
        and str(item.get("sourceKind") or "").strip() == "verified_execution"
        and str(item.get("verificationStatus") or "").strip() == "execution_success"
    }
    automatic_records = []
    manual_records = []
    for default_origin, cases in (
        ("automatic", normalized.get("cases") or []),
        ("manual", normalized.get("manual_cases") or []),
    ):
        for idx, case in enumerate(cases):
            origin_level = _planner_case_origin_level(case, default_origin)
            record = {
                "raw": case,
                "compact": _compact_case_for_plan(case, idx, origin_level=origin_level),
            }
            (manual_records if origin_level == "manual" else automatic_records).append(record)
    automatic_candidates, manual_candidates, planning_context, convergence_focus = (
        _focus_executable_convergence_candidates(
            normalized,
            automatic_records,
            manual_records,
            planning_context,
            selected_baselines=compact_baselines,
        )
    )
    candidates = automatic_candidates + manual_candidates
    candidate_eligibility_by_id = {
        str(item.get("case_id") or "").strip(): copy.deepcopy(item.get("convergenceEvidence"))
        for item in candidates
        if str(item.get("case_id") or "").strip()
        and isinstance(item.get("convergenceEvidence"), dict)
        and item["convergenceEvidence"].get("eligible") is True
    }
    model_runtime_trace = {}
    trace = {
        "enabled": True,
        "fallback": False,
        "planning_pass": str((planning_context or {}).get("pass") or "initial"),
        "candidate_count": len(candidates),
        "full_candidate_count": len(automatic_records) + len(manual_records),
        "focused_candidate_count": len(candidates),
        "convergence_focus": bool(convergence_focus.get("enabled")),
        "automatic_candidate_count": len(automatic_candidates),
        "manual_candidate_count": len(manual_candidates),
        **_model_config_trace(model_config, model_runtime_trace),
    }
    if not candidates:
        trace.update({"fallback": True, "error": "no_cases"})
        return {
            "cases": [], "needs_review_cases": [], "draft_cases": [], "manual_cases": [],
            "authoritative": False, "trace": trace,
            "verifiedBaselineIds": sorted(verified_baseline_ids),
            "planningContext": planning_context if isinstance(planning_context, dict) else {},
            "focusedCandidateIds": convergence_focus.get("focusedCandidateIds") or [],
            "convergenceFocus": convergence_focus,
            "candidateEligibilityById": candidate_eligibility_by_id,
        }
    allowed_baseline_ids = {str(item.get("id") or "").strip() for item in compact_baselines if str(item.get("id") or "").strip()}
    convergence_pass = str((planning_context or {}).get("pass") or "").strip() == "coverage_convergence"
    request_analysis = normalized.get("analysis") or {}
    request_scenarios = normalized.get("scenarios") or []
    request_source_evidence = source_evidence if isinstance(source_evidence, dict) else {}
    if convergence_pass:
        request_analysis, request_source_evidence = _compact_executable_convergence_context(
            request_analysis,
            request_source_evidence,
        )
        request_scenarios = []
    request = {
        "title": title,
        "module": module,
        "analysis": request_analysis,
        "scenarios": request_scenarios,
        "cases": candidates,
        "priorManualCandidateCount": len(manual_candidates),
        "selectedBaselines": compact_baselines,
        "scopePlan": scope_plan or {},
        "sourceEvidence": request_source_evidence,
        "planningContext": planning_context if isinstance(planning_context, dict) else {},
        "batchContract": {
            "casesContainAllExecutableCandidates": True,
            "smokeMaxCount": max(1, min(3, safe_int((scope_plan or {}).get("smokeCount"), 3))),
            "remainingBatchRequiredBeyondSmoke": True,
            "manualIsNotSmokeOverflow": True,
        },
        "evidenceContract": {
            "figmaIsSoftReference": True,
            "missingSiblingFrameIsNotManualReason": True,
            "explicitRequirementMayDefineExpectedVisibleUi": True,
            "runnerValidatesProductAssertion": True,
        },
    }
    trace["context_compacted"] = convergence_pass
    trace["request_context_chars"] = len(json.dumps(request, ensure_ascii=False))
    trace["request_candidate_ids"] = [
        str(item.get("case_id") or "").strip()
        for item in candidates
        if str(item.get("case_id") or "").strip()
    ]
    planner_timeout = (
        AI_EXECUTABLE_YAML_EVIDENCE_CONVERGENCE_TIMEOUT_SECONDS
        if str((planning_context or {}).get("pass") or "").strip() == "coverage_convergence"
        and candidate_eligibility_by_id
        else AI_EXECUTABLE_YAML_PLANNER_TIMEOUT_SECONDS
    )
    trace["timeout_seconds"] = planner_timeout
    try:
        result = run_ai_skill(
            "executable_yaml_planner",
            request,
            timeout=planner_timeout,
            temperature=0.0,
            respect_global_timeout=False,
            retry_count=0,
            model_config=model_config,
            runtime_trace=model_runtime_trace,
        )
        trace.update(_model_config_trace(model_config, model_runtime_trace))
        candidate_by_id = {str(item.get("case_id") or "").strip(): item for item in candidates if str(item.get("case_id") or "").strip()}
        candidate_by_title = {str(item.get("title") or "").strip(): item for item in candidates if str(item.get("title") or "").strip()}
        cases, rejected_case_count = _ground_executable_plan_items(
            result.get("cases") or [], candidate_by_id, candidate_by_title
        )
        ungrounded_baseline_count = 0
        for index, item in enumerate(cases):
            baseline_id = str(item.get("baselineId") or item.get("baseline_id") or "").strip()
            baseline_grounded = bool(baseline_id and baseline_id in allowed_baseline_ids)
            if not baseline_grounded:
                ungrounded_baseline_count += 1
            normalized_item = dict(item)
            normalized_item["baselineId"] = baseline_id
            normalized_item["baselineGrounded"] = baseline_grounded
            cases[index] = normalized_item
        classification_groups = {}
        rejected_classification_count = 0
        for key in ("needs_review_cases", "draft_cases", "manual_cases"):
            grounded, rejected = _ground_executable_plan_items(
                result.get(key) or [], candidate_by_id, candidate_by_title
            )
            classification_groups[key] = grounded
            rejected_classification_count += rejected
        trace.update({
            "case_count": len(cases),
            "needs_review_count": len(classification_groups["needs_review_cases"]),
            "draft_count": len(classification_groups["draft_cases"]),
            "manual_count": len(classification_groups["manual_cases"]),
            "smoke_count": len([item for item in cases if str(item.get("batch") or "").lower() == "smoke"]),
            "rejected_case_count": rejected_case_count,
            "rejected_classification_count": rejected_classification_count,
            "ungrounded_baseline_count": ungrounded_baseline_count,
        })
        return {
            "cases": cases,
            **classification_groups,
            "review": result.get("review") or {},
            "authoritative": True,
            "trace": trace,
            "allowedBaselineIds": sorted(allowed_baseline_ids),
            "verifiedBaselineIds": sorted(verified_baseline_ids),
            "requirementPoints": normalize_text_list(
                (normalized.get("analysis") or {}).get("requirement_points")
            )[:12],
            "planningContext": planning_context if isinstance(planning_context, dict) else {},
            "focusedCandidateIds": convergence_focus.get("focusedCandidateIds") or [],
            "convergenceFocus": convergence_focus,
            "candidateEligibilityById": candidate_eligibility_by_id,
        }
    except Exception as exc:
        trace.update(_model_config_trace(model_config, model_runtime_trace))
        trace.update({"fallback": True, "error": str(exc)})
        evidence_fallback = _convergence_evidence_fallback_plan(
            normalized,
            candidates,
            candidate_eligibility_by_id,
            allowed_baseline_ids,
            verified_baseline_ids,
            planning_context,
            convergence_focus,
            trace,
            exc,
        )
        if evidence_fallback:
            return evidence_fallback
        return {
            "cases": [], "needs_review_cases": [], "draft_cases": [], "manual_cases": [],
            "authoritative": False, "trace": trace,
            "verifiedBaselineIds": sorted(verified_baseline_ids),
            "planningContext": planning_context if isinstance(planning_context, dict) else {},
            "focusedCandidateIds": convergence_focus.get("focusedCandidateIds") or [],
            "convergenceFocus": convergence_focus,
            "candidateEligibilityById": candidate_eligibility_by_id,
        }


_DYNAMIC_UI_DATA_PATTERNS = (
    re.compile(r"[\w().（）-]+\.(?:docx?|xlsx?|pptx?|pdf|txt|csv|zip|rar|png|jpe?g|webp|stl|obj)\b", re.I),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{8,}(?!\d)"),
    re.compile(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b"),
)


def _current_requirement_visual_evidence_text(normalized):
    """Return current-source evidence without importing candidate or baseline sample data."""
    normalized = normalized if isinstance(normalized, dict) else {}
    analysis = normalized.get("analysis") if isinstance(normalized.get("analysis"), dict) else {}
    review = normalized.get("review") if isinstance(normalized.get("review"), dict) else {}
    source_items = [
        analysis.get("requirement_points"),
        analysis.get("requirement_contract"),
        analysis.get("requirement_acceptance_checks"),
        review.get("current_page_evidence"),
        review.get("visual_batch_judgements"),
    ]
    return json.dumps(source_items, ensure_ascii=False)


def _unsupported_dynamic_ui_literals(value, normalized):
    """Find data-like literals that exist only in generated/history-derived execution text."""
    text = "\n".join(normalize_text_list(value))
    if not text:
        return []
    current_evidence = _current_requirement_visual_evidence_text(normalized)
    literals = []
    quoted = re.findall(r"[「『“\"'‘]([^」』”\"'’]+)[」』”\"'’]", text)
    candidates = quoted + [match.group(0) for pattern in _DYNAMIC_UI_DATA_PATTERNS for match in pattern.finditer(text)]
    for candidate in candidates:
        literal = str(candidate or "").strip()
        if not literal or literal in current_evidence:
            continue
        if not any(pattern.search(literal) for pattern in _DYNAMIC_UI_DATA_PATTERNS):
            continue
        if literal not in literals:
            literals.append(literal)
    return literals


def _ground_planner_terminal_observation(flow, assertion_target, normalized):
    """Replace a history-only data assertion with the planner's current-case terminal contract."""
    steps = normalize_text_list(flow)[:8]
    unsupported = _unsupported_dynamic_ui_literals(steps, normalized)
    if not unsupported:
        return steps, False, []
    if not assertion_target or _unsupported_dynamic_ui_literals(assertion_target, normalized):
        return steps, False, unsupported
    last_action_index = max(
        (index for index, step in enumerate(steps) if _navigation_action_target_key(step)),
        default=-1,
    )
    changed = False
    grounded = []
    for index, step in enumerate(steps):
        step_literals = [literal for literal in unsupported if literal in step]
        if not step_literals:
            grounded.append(step)
            continue
        is_terminal_observation = bool(
            index > last_action_index
            and str(step or "").strip().startswith(("等待", "观察", "检查", "验证", "校验", "断言"))
        )
        if not is_terminal_observation:
            return steps, False, unsupported
        replacement = f"等待{str(assertion_target).strip()}"
        if not grounded or grounded[-1] != replacement:
            grounded.append(replacement)
        changed = True
    return grounded, changed, unsupported


def _ai_plan_satisfies_bounded_evidence(
    case,
    item,
    bounded_evidence,
    acceptance_checks,
    allowed_baseline_ids,
):
    """Keep a concrete AI-adapted leaf when it satisfies every bounded check."""
    case = case if isinstance(case, dict) else {}
    item = item if isinstance(item, dict) else {}
    bounded_evidence = bounded_evidence if isinstance(bounded_evidence, dict) else {}
    baseline_id = str(item.get("baselineId") or "").strip()
    flow = normalize_text_list(item.get("flow"))[:8]
    assertion_target = str(item.get("assertionTarget") or "").strip()
    requirement_refs = normalize_text_list(item.get("requirementRefs"))[:8]
    if not (
        item.get("baselineGrounded") is True
        and baseline_id in set(allowed_baseline_ids or [])
        and len(flow) >= 2
        and assertion_target
        and requirement_refs
        and not _source_navigation_has_alternative_destinations(
            flow,
            allow_terminal_wait_alternatives=True,
        )
    ):
        return False
    source_requirement_ids = set(_source_case_requirement_ids(case))
    planned_requirement_ids = set(_acceptance_requirement_ids(requirement_refs))
    if not planned_requirement_ids or not planned_requirement_ids.issubset(source_requirement_ids):
        return False
    checks_by_id = {
        str(check.get("id") or "").strip(): check
        for check in (acceptance_checks or [])
        if isinstance(check, dict) and str(check.get("id") or "").strip()
    }
    required_check_ids = normalize_text_list(bounded_evidence.get("acceptanceCheckIds"))
    if not required_check_ids or any(check_id not in checks_by_id for check_id in required_check_ids):
        return False
    probe = {
        "steps": flow,
        "assertions": [assertion_target],
        "requirementRefs": requirement_refs,
    }
    if not _planner_flow_reaches_required_branch(
        case,
        flow,
        item.get("precondition"),
        requirement_refs,
        acceptance_checks,
    ):
        return False
    return all(
        case_covers_requirement_acceptance(probe, checks_by_id[check_id])
        for check_id in required_check_ids
    )


def _planner_flow_reaches_required_branch(
    case,
    flow,
    precondition,
    requirement_refs,
    acceptance_checks,
):
    """Require a visible navigation action when a plan starts from the app home page."""
    case = case if isinstance(case, dict) else {}
    flow = normalize_text_list(flow)
    start_items = []
    for value in (
        precondition,
        case.get("start_page"),
        case.get("startPage"),
        case.get("preconditions"),
        case.get("precondition"),
    ):
        start_items.extend(normalize_text_list(value))
    start_context = "\n".join(start_items)
    if "首页" not in start_context:
        return True
    requirement_ids = set(_acceptance_requirement_ids(requirement_refs))
    mapped_checks = [
        check for check in (acceptance_checks or [])
        if isinstance(check, dict)
        and str(check.get("requirementId") or "").strip() in requirement_ids
    ]
    mapped_branches = list(dict.fromkeys(
        str(check.get("branch") or "").strip()
        for check in mapped_checks
        if str(check.get("branch") or "").strip()
    ))
    branches = [branch for branch in mapped_branches if "首页" not in branch]
    if mapped_branches and not branches:
        return True
    case_context = "\n".join(normalize_text_list([
        case.get("title"),
        case.get("business_path"),
        case.get("businessPath"),
    ]))
    if mapped_checks and not mapped_branches and "首页" in case_context:
        return True
    flow_probe = {"steps": flow}
    if branches:
        return any(
            _case_has_concrete_branch_execution_evidence(flow_probe, branch, branches)
            for branch in branches
        )
    return any(_navigation_action_target_key(step) for step in flow)


def apply_executable_yaml_plan_to_payload(payload, plan):
    """Apply the AI planner's grounded classification and path plan."""
    normalized = normalize_cases_payload(payload)
    plan = plan if isinstance(plan, dict) else {}
    plan_cases = [item for item in (plan.get("cases") or []) if isinstance(item, dict)]
    authoritative = plan.get("authoritative") is True
    evidence_fallback = plan.get("evidenceFallback") is True
    if not plan_cases and not authoritative and not evidence_fallback:
        return normalized
    classifications = (
        ("executable", plan_cases),
        ("needs_review", plan.get("needs_review_cases") or []),
        ("draft", plan.get("draft_cases") or []),
        ("manual", plan.get("manual_cases") or []),
    )
    level_rank = {"executable": 0, "needs_review": 1, "draft": 2, "manual": 3}
    classification_by_id = {}
    classification_by_title = {}
    classification_hits = {}
    for level, items in classifications:
        for item in items:
            if not isinstance(item, dict):
                continue
            case_id = str(item.get("case_id") or item.get("caseId") or "").strip()
            title = str(item.get("title") or "").strip()
            hit_key = case_id or title
            if hit_key:
                classification_hits.setdefault(hit_key, set()).add(level)
            current = classification_by_id.get(case_id) if case_id else classification_by_title.get(title)
            if current is not None and level_rank[current[0]] >= level_rank[level]:
                continue
            entry = (level, item)
            if case_id:
                classification_by_id[case_id] = entry
            if title:
                classification_by_title[title] = entry
    targets = generation_volume_targets(normalized.get("analysis") or {}, mode="full")
    smoke_limit = max(1, min(3, safe_int(plan.get("scopePlan", {}).get("smokeCount"), safe_int(targets.get("smoke_cases"), 3))))
    smoke_used = 0
    allowed_baseline_ids = {
        str(item).strip() for item in (plan.get("allowedBaselineIds") or []) if str(item or "").strip()
    }
    verified_baseline_ids = {
        str(item).strip() for item in (plan.get("verifiedBaselineIds") or []) if str(item or "").strip()
    }
    planning_context = plan.get("planningContext") if isinstance(plan.get("planningContext"), dict) else {}
    convergence_pass = str(planning_context.get("pass") or "").strip() == "coverage_convergence"
    focused_candidate_ids = {
        str(item or "").strip()
        for item in (plan.get("focusedCandidateIds") or [])
        if str(item or "").strip()
    }
    output_cases = []
    manual_cases = []
    candidate_records = []
    for container_level, cases in (
        ("automatic", normalized.get("cases") or []),
        ("manual", normalized.get("manual_cases") or []),
    ):
        for idx, item in enumerate(cases):
            if not isinstance(item, dict):
                continue
            origin_level = _planner_case_origin_level(item, container_level)
            candidate_records.append({
                "case": item,
                "caseId": _planner_case_id(item, idx, origin_level=origin_level),
                "originLevel": origin_level,
                "containerLevel": container_level,
            })
    bounded_evidence_by_id = plan.get("candidateEligibilityById")
    bounded_evidence_by_id = (
        bounded_evidence_by_id
        if convergence_pass and isinstance(bounded_evidence_by_id, dict)
        else {}
    )
    unmentioned_count = 0
    unmentioned_manual_count = 0
    promoted_manual_count = 0
    retained_manual_count = 0
    promotion_guard_failed_count = 0
    requirement_ref_guard_count = 0
    path_mapping_guard_count = 0
    branch_scope_guard_count = 0
    ambiguous_navigation_guard_count = 0
    navigation_path_guard_count = 0
    preserved_executable_count = 0
    outside_focus_preserved_count = 0
    bounded_convergence_override_count = 0
    bounded_convergence_ai_path_count = 0
    convergence_demotion_blocked_count = 0
    redundant_unmentioned_manualized_count = 0
    current_visual_leaf_adapted_count = 0
    dynamic_data_observation_grounded_count = 0
    dynamic_data_guard_count = 0
    unclassified_focused_automatic_ids = set()
    applied_counts = {"executable": 0, "needs_review": 0, "draft": 0, "manual": 0}
    for record in candidate_records:
        case = copy.deepcopy(record["case"])
        origin_level = record["originLevel"]
        case_id = record["caseId"]
        title = str(case.get("title") or "").strip()
        classification = classification_by_id.get(case_id) or classification_by_title.get(title)
        if classification:
            level, item = classification
        else:
            current_level = str(
                case.get("executionLevel") or case.get("execution_level") or record.get("containerLevel") or ""
            ).strip().lower()
            outside_focus = bool(convergence_pass and focused_candidate_ids and case_id not in focused_candidate_ids)
            if convergence_pass and current_level == "executable":
                level = "executable"
                item = _existing_executable_plan_item(case, case_id, title)
                preserved_executable_count += 1
                if not outside_focus:
                    unmentioned_count += 1
            else:
                fallback_level = "manual" if current_level == "manual" or origin_level == "manual" else "needs_review"
                level, item = (fallback_level, {
                    "caseId": case_id,
                    "title": title,
                    "reason": (
                        "最终收敛未选中该人工候选，保留人工级别"
                        if outside_focus and fallback_level == "manual"
                        else (
                            "AI 可执行规划未覆盖原人工候选，保留人工级别"
                            if fallback_level == "manual"
                            else "AI 可执行规划未覆盖该候选，按安全策略进入复核"
                        )
                    ),
                })
                if outside_focus:
                    outside_focus_preserved_count += 1
                else:
                    unmentioned_count += 1
                    if origin_level == "manual":
                        unmentioned_manual_count += 1
                if (
                    convergence_pass
                    and origin_level == "automatic"
                    and case_id in focused_candidate_ids
                    and fallback_level != "manual"
                ):
                    unclassified_focused_automatic_ids.add(case_id)

        current_level = str(
            case.get("executionLevel") or case.get("execution_level") or record.get("containerLevel") or ""
        ).strip().lower()
        if convergence_pass and current_level == "executable" and level != "executable":
            level = "executable"
            item = _existing_executable_plan_item(case, case_id, title)
            item["batch"] = "remaining" if smoke_used >= smoke_limit else item.get("batch")
            convergence_demotion_blocked_count += 1

        bounded_evidence = bounded_evidence_by_id.get(case_id)
        bounded_check_ids = {
            str(value or "").strip()
            for value in ((bounded_evidence or {}).get("acceptanceCheckIds") or [])
            if str(value or "").strip()
        }
        if (
            classification is not None
            and convergence_pass
            and isinstance(bounded_evidence, dict)
            and (
                origin_level == "automatic"
                or bounded_evidence.get("manualPromotionEligible") is True
            )
            and case_id in focused_candidate_ids
            and bounded_evidence.get("eligible") is True
            and bounded_check_ids
            and str(bounded_evidence.get("baselineId") or "").strip() in allowed_baseline_ids
        ):
            model_level = level
            model_reason = str(
                item.get("executableReason") or item.get("reason")
                or item.get("reviewReason") or item.get("manualReason") or ""
            ).strip()
            bounded_meta = {
                "kind": bounded_evidence.get("kind") or "",
                "sourceCaseId": bounded_evidence.get("sourceCaseId") or "",
                "tailSourceCaseId": bounded_evidence.get("tailSourceCaseId") or "",
                "source": bounded_evidence.get("source") or "",
                "currentLeafAdapted": bounded_evidence.get("currentLeafAdapted") is True,
                "currentLeafSourceCaseId": bounded_evidence.get("currentLeafSourceCaseId") or "",
                "currentLeafEvidenceSource": bounded_evidence.get("currentLeafEvidenceSource") or "",
                "currentLeafEvidence": copy.deepcopy(bounded_evidence.get("currentLeafEvidence") or {}),
                "acceptanceCheckIds": sorted(bounded_check_ids),
                "modelLevel": model_level,
                "modelReason": model_reason,
            }
            acceptance_checks = (
                (normalized.get("analysis") or {}).get("requirement_acceptance_checks") or []
            )
            if model_level == "executable" and _ai_plan_satisfies_bounded_evidence(
                case,
                item,
                bounded_evidence,
                acceptance_checks,
                allowed_baseline_ids,
            ):
                level = "executable"
                item = {
                    **item,
                    "title": bounded_evidence.get("title") or item.get("title") or title,
                    "boundedConvergence": {
                        **bounded_meta,
                        "modelPathPreserved": True,
                    },
                }
                bounded_convergence_ai_path_count += 1
            else:
                level = "executable"
                item = {
                    "caseId": case_id,
                    "title": bounded_evidence.get("title") or title,
                    "baselineId": bounded_evidence.get("baselineId") or "",
                    "baselineGrounded": True,
                    "precondition": bounded_evidence.get("precondition") or "",
                    "flow": bounded_evidence.get("flow") or [],
                    "assertionTarget": bounded_evidence.get("assertionTarget") or "",
                    "requirementRefs": bounded_evidence.get("requirementRefs") or [],
                    "executableReason": (
                        "同需求分支成功基线负责真实文字来源页导航，原始需求与上游 AI 候选定义当前页可见断言；"
                        "平台保留后续 YAML、评分、dry-run 与真实 Runner 门禁"
                        if str(bounded_evidence.get("kind") or "") == "source_ui_assertion"
                        else (
                            "同需求分支的成功基线负责来源页导航，上游 AI 候选只验证目标入口后的首个可见终态；"
                            "平台保留后续 YAML、评分、dry-run 与真实 Runner 门禁"
                        )
                    ),
                    "batch": "remaining",
                    "boundedConvergence": bounded_meta,
                }
                bounded_convergence_override_count += 1

        baseline_id = str(item.get("baselineId") or "").strip()
        baseline_grounded = bool(
            item.get("baselineGrounded") is True
            and baseline_id
            and baseline_id in allowed_baseline_ids
        )
        planned_flow = normalize_text_list(item.get("flow"))[:8]
        precondition = str(item.get("precondition") or "").strip()
        assertion_target = str(item.get("assertionTarget") or "").strip()
        requirement_refs, requirement_refs_guarded = _ground_planner_requirement_refs(
            case,
            item,
            plan.get("requirementPoints") or (normalized.get("analysis") or {}).get("requirement_points"),
        )
        if requirement_refs_guarded:
            requirement_ref_guard_count += 1
            path_mapping_guard_count += 1
        path_plan_applied = bool(
            baseline_grounded
            and len(planned_flow) >= 2
            and not requirement_refs_guarded
        )
        acceptance_checks = (
            (normalized.get("analysis") or {}).get("requirement_acceptance_checks") or []
        )
        mapped_requirement_ids = set(_acceptance_requirement_ids(requirement_refs))
        mapped_checks = [
            check for check in acceptance_checks
            if isinstance(check, dict)
            and set(_acceptance_requirement_ids(check.get("requirementId"))).intersection(mapped_requirement_ids)
        ]
        visual_branch = next((
            str(check.get("branch") or "").strip()
            for check in mapped_checks
            if str(check.get("branch") or "").strip()
        ), "")
        visual_target_terms = _acceptance_target_terms("；".join(
            str(check.get("text") or "").strip()
            for check in mapped_checks
            if str(check.get("text") or "").strip()
        ))
        current_visual_evidence = {}
        visual_leaf_adapted = False
        if level == "executable" and baseline_grounded and planned_flow and visual_branch:
            current_visual_evidence = _current_visual_page_evidence_for_case(
                normalized,
                case,
                case_id,
                visual_branch,
                visual_target_terms,
            )
            planned_flow, visual_leaf_adapted = _adapt_trusted_navigation_to_visual_evidence(
                planned_flow,
                current_visual_evidence,
                visual_branch,
            )
            if visual_leaf_adapted:
                current_visual_leaf_adapted_count += 1
        unsupported_assertion_literals = _unsupported_dynamic_ui_literals(assertion_target, normalized)
        planned_flow, data_observation_grounded, unsupported_flow_literals = (
            _ground_planner_terminal_observation(planned_flow, assertion_target, normalized)
        )
        if data_observation_grounded:
            dynamic_data_observation_grounded_count += 1
        if level == "executable" and (unsupported_assertion_literals or (unsupported_flow_literals and not data_observation_grounded)):
            level = "manual" if convergence_pass else "needs_review"
            item = {
                **item,
                "reason": (
                    "执行计划包含只在历史样例中出现、未被当前需求或当前视觉证据支持的动态数据；"
                    "必须改为当前用例的稳定页面/区域终态后才能下发 Runner"
                ),
            }
            path_plan_applied = False
            dynamic_data_guard_count += 1
        if level == "executable" and _source_navigation_has_alternative_destinations(
            planned_flow or case.get("steps") or [],
            allow_terminal_wait_alternatives=True,
        ):
            level = "manual" if convergence_pass else "needs_review"
            item = {
                **item,
                "reason": (
                    "执行路径包含备选、别名或多个业务分支；每个点击/进入步骤必须只有一个具体可见目标，"
                    "跨分支验收必须拆成独立短路径"
                ),
            }
            path_plan_applied = False
            ambiguous_navigation_guard_count += 1
        navigation_path_complete = _planner_flow_reaches_required_branch(
            case,
            planned_flow,
            precondition,
            requirement_refs,
            acceptance_checks,
        )
        if level == "executable" and path_plan_applied and not navigation_path_complete:
            level = "manual" if convergence_pass else "needs_review"
            item = {
                **item,
                "reason": (
                    "规划起点为 App 首页，但执行流只有等待/断言，缺少进入需求业务分支的真实可见文字导航"
                ),
            }
            path_plan_applied = False
            navigation_path_guard_count += 1
        if origin_level == "manual" and level == "executable" and not (
            path_plan_applied and precondition and assertion_target and requirement_refs
        ):
            level = "needs_review"
            item = {
                **item,
                "reason": (
                    str(item.get("reason") or item.get("executableReason") or "").strip()
                    + "；原人工候选升级缺少可信基线路径、明确前置、可见终态或需求映射，已降为 needs_review"
                ).strip("；"),
            }
            promotion_guard_failed_count += 1
        mapped_requirement_ids = _acceptance_requirement_ids(requirement_refs)
        if level == "executable" and len(set(mapped_requirement_ids)) > 1:
            acceptance_checks = [
                check for check in ((normalized.get("analysis") or {}).get("requirement_acceptance_checks") or [])
                if isinstance(check, dict)
                and str(check.get("requirementId") or "").strip() in mapped_requirement_ids
                and str(check.get("branch") or "").strip()
            ]
            planned_scope_case = {
                "steps": planned_flow or case.get("steps") or [],
                "assertions": [assertion_target] if assertion_target else case.get("assertions") or [],
            }
            distinct_branches = list(dict.fromkeys(
                str(check.get("branch") or "").strip()
                for check in acceptance_checks
                if str(check.get("branch") or "").strip()
            ))
            missing_branches = list(dict.fromkeys(
                str(check.get("branch") or "").strip()
                for check in acceptance_checks
                if len({_compact_branch_text(item) for item in distinct_branches}) > 1
                and not _case_has_concrete_branch_execution_evidence(
                    planned_scope_case,
                    check.get("branch"),
                    distinct_branches,
                )
            ))
            if missing_branches:
                level = "manual" if convergence_pass else "needs_review"
                item = {
                    **item,
                    "reason": (
                        "候选映射多个需求分支，但实际步骤缺少分支证据："
                        + "、".join(missing_branches[:4])
                    ),
                }
                branch_scope_guard_count += 1

        applied_counts[level] += 1
        reason = str(
            item.get("reason") or item.get("executableReason") or item.get("reviewReason")
            or item.get("manualReason") or ""
        ).strip()
        case["case_id"] = case_id
        case["originExecutionLevel"] = origin_level
        if requirement_refs:
            case["requirement_refs"] = requirement_refs
            case["requirementRefs"] = requirement_refs
            if not str(case.get("coverage") or "").strip():
                case["coverage"] = "; ".join(requirement_refs)
        if level == "manual":
            manual_item = case
            manual_item["executionLevel"] = "manual"
            _set_case_smoke(manual_item, False)
            manual_item["automation_reason"] = reason or "AI 规划判断当前条件不适合自动执行"
            manual_item["ai_case_classification"] = {
                "level": "manual",
                "reason": manual_item["automation_reason"],
                "originLevel": origin_level,
            }
            manual_cases.append(manual_item)
            if origin_level == "manual":
                retained_manual_count += 1
            continue
        if origin_level == "manual":
            previous_reason = str(case.get("automation_reason") or case.get("reason") or "").strip()
            if previous_reason:
                case["previous_manual_reason"] = previous_reason
            for stale_key in ("reason", "reasons", "level", "score"):
                case.pop(stale_key, None)
            if level == "executable":
                promoted_manual_count += 1
        case["executionLevel"] = level
        case["ai_case_classification"] = {
            "level": level,
            "reason": reason,
            "originLevel": origin_level,
        }
        if reason:
            case["automation_reason"] = reason
        output_cases.append(case)
        if level != "executable":
            case["smoke"] = False
            flags = [flag for flag in normalize_text_list(case.get("flag") or case.get("flags")) if flag != "冒烟"]
            case["flag"] = flags
            continue
        original_flow = normalize_text_list(case.get("steps"))[:8]
        case["ai_case_plan"] = {
            "baselineId": baseline_id,
            "baselineGrounded": baseline_grounded,
            "baselineVerified": bool(
                baseline_grounded
                and path_plan_applied
                and baseline_id in verified_baseline_ids
            ),
            "precondition": precondition,
            "flow": planned_flow,
            "originalFlow": original_flow,
            "pathPlanApplied": path_plan_applied,
            "pathMappingGuarded": requirement_refs_guarded,
            "proposedRequirementRefs": normalize_text_list(
                item.get("requirementRefs") or item.get("requirement_refs") or item.get("coverage")
            )[:8],
            "assertionTarget": assertion_target,
            "executableReason": item.get("executableReason") or "",
            "batch": item.get("batch") or "",
            "boundedConvergence": copy.deepcopy(item.get("boundedConvergence") or {}),
            "currentVisualLeafAdapted": visual_leaf_adapted,
            "currentVisualLeafEvidence": copy.deepcopy(current_visual_evidence),
            "dynamicDataObservationGrounded": data_observation_grounded,
            "unsupportedDynamicLiterals": list(dict.fromkeys(
                unsupported_flow_literals + unsupported_assertion_literals
            ))[:8],
        }
        if precondition and not case.get("preconditions"):
            case["preconditions"] = [precondition]
        bounded_evidence_used = bool(item.get("boundedConvergence"))
        if bounded_evidence_used:
            if str(item.get("title") or "").strip():
                case["title"] = str(item.get("title") or "").strip()
            _set_case_smoke(case, False)
        if assertion_target and (
            path_plan_applied
            or bounded_evidence_used
            or not normalize_text_list(case.get("assertions"))
        ):
            case["assertions"] = [assertion_target]
        if assertion_target and (
            path_plan_applied
            or bounded_evidence_used
            or not str(case.get("expected_result") or "").strip()
        ):
            case["expected_result"] = assertion_target
        if assertion_target and not str(case.get("goal") or "").strip():
            case["goal"] = assertion_target
        if path_plan_applied:
            case["steps"] = planned_flow
            if not str(case.get("business_path") or "").strip():
                case["business_path"] = " -> ".join(planned_flow)
        if item.get("executableReason") and not case.get("automation_reason"):
            case["automation_reason"] = item.get("executableReason")
        can_smoke = bool(
            path_plan_applied
            and item.get("precondition")
            and item.get("assertionTarget")
        )
        if str(item.get("batch") or "").lower() == "smoke" and can_smoke and smoke_used < smoke_limit:
            case["smoke"] = True
            flags = normalize_text_list(case.get("flag") or case.get("flags"))
            if "冒烟" not in flags:
                flags.append("冒烟")
            case["flag"] = flags
            smoke_used += 1
    normalized["cases"] = output_cases
    if convergence_pass and unclassified_focused_automatic_ids:
        provisional_audit = executable_yaml_portfolio_audit(normalized, targets)
        if (
            provisional_audit.get("executableCount")
            and not provisional_audit.get("missingRequirementPoints")
        ):
            retained_output = []
            for case in output_cases:
                case_id = str(case.get("case_id") or "").strip()
                level = str(case.get("executionLevel") or "").strip().lower()
                if case_id not in unclassified_focused_automatic_ids or level == "executable":
                    retained_output.append(case)
                    continue
                reason = (
                    "最终 AI 收敛漏回该候选；显式需求与验收维度已由其他 executable 路径完整覆盖，"
                    "因此保留为人工项，不自动升级也不因冗余候选阻断执行"
                )
                case["executionLevel"] = "manual"
                case["automation_reason"] = reason
                case["ai_case_classification"] = {
                    "level": "manual",
                    "reason": reason,
                    "originLevel": "automatic",
                }
                manual_cases.append(case)
                if level in applied_counts:
                    applied_counts[level] = max(0, applied_counts[level] - 1)
                applied_counts["manual"] += 1
                redundant_unmentioned_manualized_count += 1
            output_cases = retained_output
            normalized["cases"] = output_cases
    deduped_manual = []
    seen_manual = set()
    for item in manual_cases:
        key = str(item.get("case_id") or item.get("id") or item.get("title") or item.get("case_name") or "").strip()
        if key and key in seen_manual:
            continue
        if key:
            seen_manual.add(key)
        deduped_manual.append(item)
    normalized["manual_cases"] = deduped_manual
    review = normalized.setdefault("review", {})
    review["executable_yaml_plan"] = {
        "classificationApplied": authoritative,
        "evidenceFallbackApplied": evidence_fallback,
        "case_count": len(plan_cases),
        "needs_review_count": applied_counts["needs_review"],
        "draft_count": applied_counts["draft"],
        "manual_count": applied_counts["manual"],
        "executable_count": applied_counts["executable"],
        "unmentioned_count": unmentioned_count,
        "unmentioned_manual_count": unmentioned_manual_count,
        "candidate_count": len(candidate_records),
        "manual_candidate_count": sum(1 for item in candidate_records if item["originLevel"] == "manual"),
        "promoted_manual_count": promoted_manual_count,
        "retained_manual_count": retained_manual_count,
        "promotion_guard_failed_count": promotion_guard_failed_count,
        "requirement_ref_guard_count": requirement_ref_guard_count,
        "path_mapping_guard_count": path_mapping_guard_count,
        "branch_scope_guard_count": branch_scope_guard_count,
        "ambiguous_navigation_guard_count": ambiguous_navigation_guard_count,
        "navigation_path_guard_count": navigation_path_guard_count,
        "preserved_executable_count": preserved_executable_count,
        "outside_focus_preserved_count": outside_focus_preserved_count,
        "bounded_convergence_override_count": bounded_convergence_override_count,
        "bounded_convergence_ai_path_count": bounded_convergence_ai_path_count,
        "convergence_demotion_blocked_count": convergence_demotion_blocked_count,
        "redundant_unmentioned_manualized_count": redundant_unmentioned_manualized_count,
        "current_visual_leaf_adapted_count": current_visual_leaf_adapted_count,
        "dynamic_data_observation_grounded_count": dynamic_data_observation_grounded_count,
        "dynamic_data_guard_count": dynamic_data_guard_count,
        "focused_candidate_count": len(focused_candidate_ids),
        "overlap_count": sum(1 for levels in classification_hits.values() if len(levels) > 1),
        "smoke_count": smoke_used,
        "path_plan_applied_count": sum(
            1 for case in (normalized.get("cases") or [])
            if isinstance(case.get("ai_case_plan"), dict) and case["ai_case_plan"].get("pathPlanApplied")
        ),
        "review": plan.get("review") or {},
    }
    return normalized


# ---------------------------------------------------------------------------
# AI Skill: visual_grounder
# ---------------------------------------------------------------------------


_VISUAL_CASE_FIELDS = (
    "case_id", "id", "title", "scenario", "coverage", "requirement_point",
    "requirementRefs", "requirement_refs", "start_page", "business_path",
    "preconditions", "steps", "assertions", "expected_result", "repair_hints",
    "data_requirements", "risk", "priority", "smoke",
)
_VISUAL_SCENARIO_FIELDS = (
    "id", "scenario_id", "feature", "requirement_point", "scenario", "type",
    "business_path",
)
_VISUAL_MANUAL_FIELDS = (
    "case_id", "id", "title", "scenario", "coverage", "requirement_point",
    "reason",
)
_VISUAL_MUTABLE_FIELDS = {
    "start_page", "business_path", "preconditions", "steps", "assertions",
    "expected", "expected_result", "repair_hints", "data_requirements",
}
_VISUAL_EXECUTION_FIELDS = {
    "start_page", "business_path", "preconditions", "steps", "assertions",
    "expected", "expected_result",
}
_VISUAL_TARGET_ABSENCE_RE = re.compile(
    r"(?:未(?:出现|发现|展示|显示)|不(?:存在|展示|显示)|缺(?:少|失)|没有(?:任何|相关|可见)?|无(?:任何|相关|可见)?)"
    r".{0,30}(?:入口|按钮|文案|导入(?:方式|区域)?|控件)"
    r"|(?:入口|按钮|文案|导入(?:方式|区域)?|控件).{0,30}"
    r"(?:未(?:出现|发现|展示|显示)|不(?:存在|展示|显示)|不可见|缺(?:少|失))"
)


def _compact_visual_record(item, fields):
    item = item if isinstance(item, dict) else {}
    return {
        key: copy.deepcopy(item.get(key))
        for key in fields
        if item.get(key) not in (None, "", [], {})
    }


def compact_visual_grounder_base_payload(base_payload):
    """Keep requirement/case evidence while dropping bulky orchestration history."""
    base = normalize_cases_payload(base_payload)
    analysis = base.get("analysis") if isinstance(base.get("analysis"), dict) else {}
    compact_analysis = {
        key: copy.deepcopy(analysis.get(key))
        for key in (
            "requirement_points", "business_goals", "entry_points", "visible_outcomes",
            "state_assumptions", "data_assumptions",
        )
        if analysis.get(key) not in (None, "", [], {})
    }
    compact = {
        "title": base.get("title") or "",
        "module": base.get("module") or "",
        "analysis": compact_analysis,
        "scenarios": [
            _compact_visual_record(item, _VISUAL_SCENARIO_FIELDS)
            for item in (base.get("scenarios") or [])
            if isinstance(item, dict)
        ],
        "cases": [
            _compact_visual_record(item, _VISUAL_CASE_FIELDS)
            for item in (base.get("cases") or [])
            if isinstance(item, dict)
        ],
        "manual_cases": [
            _compact_visual_record(item, _VISUAL_MANUAL_FIELDS)
            for item in (base.get("manual_cases") or [])
            if isinstance(item, dict)
        ],
        "review": {},
    }
    for key in ("businessContext", "promptCenter"):
        if isinstance(base_payload, dict) and base_payload.get(key) not in (None, "", [], {}):
            compact[key] = copy.deepcopy(base_payload.get(key))
    return compact


def _visual_record_key(item, kind):
    item = item if isinstance(item, dict) else {}
    keys = ("case_id", "id", "title")
    if kind == "scenario":
        keys = ("scenario_id", "id", "scenario")
    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return ""


def _visual_patch_inverts_positive_case(base_item, grounded_item):
    """Keep a soft-reference frame from turning a positive requirement into absence."""
    base_item = base_item if isinstance(base_item, dict) else {}
    grounded_item = grounded_item if isinstance(grounded_item, dict) else {}
    grounded_text = "\n".join(normalize_text_list([
        grounded_item.get("start_page"),
        grounded_item.get("business_path"),
        grounded_item.get("steps"),
        grounded_item.get("assertions"),
        grounded_item.get("expected"),
        grounded_item.get("expected_result"),
    ]))
    if not _VISUAL_TARGET_ABSENCE_RE.search(grounded_text):
        return False
    base_text = "\n".join(normalize_text_list([
        base_item.get("title"),
        base_item.get("scenario"),
        base_item.get("goal"),
        base_item.get("steps"),
        base_item.get("assertions"),
        base_item.get("expected"),
        base_item.get("expected_result"),
    ]))
    return not bool(_VISUAL_TARGET_ABSENCE_RE.search(base_text))


def _merge_visual_assertion_contract(base_item, grounded_assertions, acceptance_checks):
    """Keep requirement-backed dimensions that a soft visual delta does not address."""
    base_item = base_item if isinstance(base_item, dict) else {}
    base_assertions = normalize_text_list(base_item.get("assertions"))
    grounded_assertions = normalize_text_list(grounded_assertions)
    requirement_ids = set(_source_case_requirement_ids(base_item))
    relevant_checks = [
        item for item in (acceptance_checks or [])
        if isinstance(item, dict)
        and str(item.get("requirementId") or "").strip() in requirement_ids
    ]
    if not base_assertions or not grounded_assertions or not relevant_checks:
        return grounded_assertions, []

    def assertion_probe(assertions):
        return {
            "coverage": base_item.get("coverage"),
            "requirementRefs": base_item.get("requirementRefs") or base_item.get("requirement_refs"),
            "assertions": normalize_text_list(assertions),
        }

    grounded_probe = assertion_probe(grounded_assertions)
    preserved = []
    preserved_check_ids = []
    for check in relevant_checks:
        if case_covers_requirement_acceptance(grounded_probe, check):
            continue
        for assertion in base_assertions:
            if not case_covers_requirement_acceptance(assertion_probe([assertion]), check):
                continue
            if assertion not in preserved:
                preserved.append(assertion)
            check_id = str(check.get("id") or "").strip()
            if check_id and check_id not in preserved_check_ids:
                preserved_check_ids.append(check_id)
            break
    return list(dict.fromkeys(preserved + grounded_assertions)), preserved_check_ids


def _merge_visual_records(
    base_items,
    grounded_items,
    kind,
    blocked_patches=None,
    acceptance_checks=None,
    preserved_assertions=None,
):
    merged = [copy.deepcopy(item) for item in (base_items or []) if isinstance(item, dict)]
    index_by_key = {
        _visual_record_key(item, kind): index
        for index, item in enumerate(merged)
        if _visual_record_key(item, kind)
    }
    for grounded in (grounded_items or []):
        if not isinstance(grounded, dict):
            continue
        key = _visual_record_key(grounded, kind)
        if key and key in index_by_key:
            target = merged[index_by_key[key]]
            blocked_fields = set()
            if kind in ("case", "manual") and _visual_patch_inverts_positive_case(target, grounded):
                blocked_fields = _VISUAL_EXECUTION_FIELDS
                if isinstance(blocked_patches, list):
                    blocked_patches.append({
                        "kind": kind,
                        "key": key,
                        "fields": sorted(
                            field for field in blocked_fields
                            if grounded.get(field) not in (None, "", [], {})
                        ),
                    })
            for field in _VISUAL_MUTABLE_FIELDS:
                if field in blocked_fields:
                    continue
                if grounded.get(field) not in (None, "", [], {}):
                    value = copy.deepcopy(grounded.get(field))
                    if field == "assertions" and kind in ("case", "manual"):
                        value, preserved_check_ids = _merge_visual_assertion_contract(
                            target,
                            value,
                            acceptance_checks,
                        )
                        if preserved_check_ids and isinstance(preserved_assertions, list):
                            preserved_assertions.append({
                                "kind": kind,
                                "key": key,
                                "acceptanceCheckIds": preserved_check_ids,
                            })
                    target[field] = value
            continue
        # A genuinely new visual branch still needs an explicit source requirement.
        if _source_case_requirement_ids(grounded):
            merged.append(copy.deepcopy(grounded))
    return merged


def merge_visual_grounder_payload(base_payload, grounded_payload):
    """Merge visual corrections without letting one batch erase full planning context."""
    base = normalize_cases_payload(base_payload)
    grounded = copy.deepcopy(grounded_payload) if isinstance(grounded_payload, dict) else {}
    merged = copy.deepcopy(base)
    merged["title"] = base.get("title") or grounded.get("title") or ""
    merged["module"] = base.get("module") or grounded.get("module") or ""
    base_analysis = merged.get("analysis") if isinstance(merged.get("analysis"), dict) else {}
    grounded_analysis = grounded.get("analysis") if isinstance(grounded.get("analysis"), dict) else {}
    if grounded_analysis.get("coverage_matrix") not in (None, "", [], {}):
        base_analysis["coverage_matrix"] = copy.deepcopy(grounded_analysis.get("coverage_matrix"))
    for key in ("visual_notes", "ui_notes"):
        if grounded_analysis.get(key) not in (None, "", [], {}):
            base_analysis[key] = list(dict.fromkeys(
                normalize_text_list(base_analysis.get(key))
                + normalize_text_list(grounded_analysis.get(key))
            ))
    if not base_analysis.get("requirement_points"):
        base_analysis["requirement_points"] = copy.deepcopy(grounded_analysis.get("requirement_points") or [])
    merged["analysis"] = base_analysis
    blocked_patches = []
    preserved_assertions = []
    acceptance_checks = [
        item for item in (base_analysis.get("requirement_acceptance_checks") or [])
        if isinstance(item, dict)
    ]
    base_case_keys = {
        _visual_record_key(item, "case")
        for item in (base.get("cases") or [])
        if isinstance(item, dict) and _visual_record_key(item, "case")
    }
    grounded_case_patches = [
        copy.deepcopy(item) for item in (grounded.get("cases") or []) if isinstance(item, dict)
    ]
    grounded_manual_patches = []
    blocked_reclassifications = []
    for item in (grounded.get("manual_cases") or []):
        if not isinstance(item, dict):
            continue
        key = _visual_record_key(item, "case")
        if key and key in base_case_keys:
            redirected = copy.deepcopy(item)
            reason = str(redirected.get("reason") or "").strip()
            if reason and not str(redirected.get("repair_hints") or "").strip():
                redirected["repair_hints"] = reason
            grounded_case_patches.append(redirected)
            blocked_reclassifications.append({"key": key, "from": "cases", "to": "manual_cases"})
            continue
        grounded_manual_patches.append(copy.deepcopy(item))
    merged["scenarios"] = _merge_visual_records(
        base.get("scenarios"), grounded.get("scenarios"), "scenario", blocked_patches
    )
    merged["cases"] = _merge_visual_records(
        base.get("cases"),
        grounded_case_patches,
        "case",
        blocked_patches,
        acceptance_checks,
        preserved_assertions,
    )
    merged["manual_cases"] = _merge_visual_records(
        base.get("manual_cases"),
        grounded_manual_patches,
        "manual",
        blocked_patches,
        acceptance_checks,
        preserved_assertions,
    )
    review = merged.setdefault("review", {})
    grounded_review = grounded.get("review") if isinstance(grounded.get("review"), dict) else {}
    for key, value in grounded_review.items():
        if key == "current_page_evidence":
            continue
        if value not in (None, "", [], {}):
            review[key] = copy.deepcopy(value)
    current_page_evidence = _normalize_visual_current_page_evidence(
        list(review.get("current_page_evidence") or [])
        + list(grounded_review.get("current_page_evidence") or [])
    )
    if current_page_evidence:
        review["current_page_evidence"] = current_page_evidence
    if blocked_patches:
        previous_guard = review.get("visual_scope_guard") if isinstance(review.get("visual_scope_guard"), dict) else {}
        previous_records = [
            item for item in (previous_guard.get("blockedRecords") or [])
            if isinstance(item, dict)
        ]
        combined_records = previous_records + blocked_patches
        review["visual_scope_guard"] = {
            "blockedPatchCount": len(combined_records),
            "blockedRecords": combined_records[-20:],
            "rule": (
                "Figma/截图是当前 Frame/状态的软参考；局部页面未出现目标入口时，"
                "只保留 AI 冲突说明，不得把正向需求用例改写为入口不存在。"
            ),
        }
    if blocked_reclassifications:
        review["visual_classification_guard"] = {
            "blockedReclassificationCount": len(blocked_reclassifications),
            "blockedRecords": blocked_reclassifications[-20:],
            "rule": (
                "视觉批次是软证据，只能校准路径、文案、断言和冲突提示；"
                "输入自动候选是否转人工由后续可执行规划统一判断。"
            ),
        }
    if preserved_assertions:
        review["visual_acceptance_guard"] = {
            "preservedPatchCount": len(preserved_assertions),
            "preservedRecords": preserved_assertions[-20:],
            "rule": (
                "视觉增量可以补充或修正其实际覆盖的验收维度；"
                "未被当前 Frame 处理的原始需求断言必须继续保留。"
            ),
        }
    return normalize_cases_payload(merged)


def call_visual_grounder_skill(
    title,
    module,
    base_payload,
    visual_text_assets,
    image_assets,
    timeout_seconds=None,
    model_config=None,
):
    """调用 AI skill: visual_grounder。"""
    base_payload = normalize_cases_payload(base_payload)
    compact_base_payload = compact_visual_grounder_base_payload(base_payload)
    compact_visual_text = compact_text_assets(visual_text_assets, max_chars=8000)
    payload = {
        "title": title,
        "module": module,
        "base_payload": compact_base_payload,
        "visual_text_assets": compact_visual_text,
        "image_count": len(image_assets or []),
        "rules": {
            "do_not_delete_requirements": True,
            "return_complete_payload": False,
            "return_visual_delta_only": True,
            "visual_reference_is_soft": True,
            "negative_evidence_is_current_frame_only": True,
            "do_not_invert_positive_requirement_case": True,
            "no_coordinates_or_selectors": True,
            "assertions_must_be_ui_visible": True
        }
    }
    model_runtime_trace = {}
    grounded = run_ai_skill(
        "visual_grounder",
        payload,
        image_assets=image_assets,
        timeout=int(timeout_seconds or 360),
        respect_global_timeout=timeout_seconds is None,
        retry_count=None if timeout_seconds is None else 0,
        temperature=0.1,
        max_tokens=2048,
        output_defaults={
            "title": title,
            "module": module,
            "analysis": {
                "requirement_points": (compact_base_payload.get("analysis") or {}).get("requirement_points") or [],
            },
            "scenarios": [],
            "cases": [],
            "manual_cases": [],
            "review": {},
        },
        model_config=model_config,
        runtime_trace=model_runtime_trace,
    )
    grounded = copy.deepcopy(grounded) if isinstance(grounded, dict) else {}
    grounded_review = grounded.get("review") if isinstance(grounded.get("review"), dict) else {}
    visual_judgement = str(grounded_review.get("visual_grounding_check") or "").strip()
    if not visual_judgement:
        raise ValueError("视觉 AI 未返回当前图片批次的 visual_grounding_check，不能计为解析完成")
    grounded["title"] = grounded.get("title") or title
    grounded["module"] = grounded.get("module") or module
    base_points = ((base_payload.get("analysis") or {}).get("requirement_points") or [])
    if base_points:
        analysis = grounded.setdefault("analysis", {})
        if not analysis.get("requirement_points"):
            analysis["requirement_points"] = base_points
    patch_counts = {
        "scenarios": len(grounded.get("scenarios") or []),
        "cases": len(grounded.get("cases") or []),
        "manualCases": len(grounded.get("manual_cases") or []),
    }
    grounded = merge_visual_grounder_payload(base_payload, grounded)
    review = grounded.setdefault("review", {})
    review["visual_grounder_skill"] = "visual_grounder.v1"
    review["model_trace"] = _model_config_trace(model_config, model_runtime_trace)
    review["visual_case_preservation"] = {
        "policy": "visual_delta_merge_by_id_preserve_full_payload",
        "base_case_count": len(base_payload.get("cases") or []),
        "result_case_count": len(grounded.get("cases") or []),
        "returned_patch_counts": patch_counts,
    }
    review["visual_input_compaction"] = {
        "full_chars": len(json.dumps(base_payload, ensure_ascii=False)),
        "compact_chars": len(json.dumps(compact_base_payload, ensure_ascii=False)),
        "image_count": len(image_assets or []),
        "visual_text_chars": len(compact_visual_text),
        "response_max_tokens": 2048,
        "rule": "输入保留需求点、场景索引、自动用例步骤/断言、当前批次设计稿文本和原图；模型只返回按 ID 关联的视觉增量，平台合并时保留完整规划。",
    }
    validate_ai_skill_output("cases_payload", grounded)
    return grounded


# ---------------------------------------------------------------------------
# AI Skill: coverage_auditor
# ---------------------------------------------------------------------------

def call_coverage_auditor_skill(
    title,
    module,
    payload,
    local_audit=None,
    model_config=None,
    targets=None,
):
    """调用 AI skill: coverage_auditor。"""
    normalized = normalize_cases_payload(payload)
    model_runtime_trace = {}
    targets = dict(targets) if isinstance(targets, dict) else generation_volume_targets(normalized.get("analysis") or {})
    request = {
        "title": title,
        "module": module,
        "payload": normalized,
        "local_audit": local_audit or {},
        "generation_targets": targets,
        "rules": {
            "requirement_points_must_map_to_scenarios": True,
            "requirement_points_must_map_to_cases_or_manual_cases": True,
            "generic_assertions_are_not_allowed": True,
            "min_automation_cases": targets.get("min_automation_cases"),
            "target_automation_cases": targets.get("target_automation_cases")
        }
    }
    result = run_ai_skill(
        "coverage_auditor",
        request,
        timeout=AI_COVERAGE_AUDITOR_TIMEOUT_SECONDS,
        temperature=0.1,
        respect_global_timeout=False,
        retry_count=0,
        model_config=model_config,
        runtime_trace=model_runtime_trace,
    )
    result.setdefault("missing_case_points", result.get("missing_requirement_points") or [])
    result.setdefault("missing_scenario_points", [])
    result.setdefault("generic_assertion_cases", [])
    result.setdefault("duplicate_cases", [])
    result.setdefault("questions", [])
    result["coverage_auditor_skill"] = "coverage_auditor.v1"
    result["model_trace"] = _model_config_trace(model_config, model_runtime_trace)
    result["ok"] = bool(result.get("ok")) or not (
        result.get("missing_requirement_points")
        or result.get("missing_case_points")
        or result.get("missing_scenario_points")
        or result.get("generic_assertion_cases")
        or result.get("duplicate_cases")
    )
    return result


def build_case_coverage_repair_prompt(title, module, payload, audit):
    """构建覆盖度修复 prompt。"""
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    audit_json = json.dumps(audit, ensure_ascii=False, indent=2)
    return f"""
你是资深测试架构师，现在执行第三阶段：覆盖率审查与补全。

目标：保证需求点都被场景和用例覆盖，并且自动化用例的断言贴合业务意图。

硬性要求：
1. 不要重新发散整个需求，只基于已有 JSON 和覆盖率审查结果进行补全/修正。
2. 对 audit.missing_case_points 中的每个需求点，必须补充至少 1 条可执行 cases，或放入 manual_cases 并写清楚为什么不能自动化。
3. 对 audit.missing_scenario_points 中的每个需求点，必须补充 scenarios。
4. 对 audit.generic_assertion_cases 中的用例，必须把断言改成业务意图 + UI 可见信号，不要使用"展示正常/跳转成功/结果符合预期"。
5. 不能删除已有有效 cases；可以去重和合并明显重复用例。
6. 每条新增 case 必须包含 case_id、title、priority、smoke、scenario、goal、coverage、risk、preconditions、steps、assertions、tags、repair_hints。
7. steps 要能在 Midscene 中用自然语言执行；assertions 要允许动态内容，例如"展示列表内容或空态提示"。
8. 输出只允许合法 JSON，结构仍为 title、module、analysis、scenarios、cases、manual_cases、review。
9. 必须补齐 analysis.coverage_matrix：每个 requirement_point 都要说明正常/异常/边界场景，以及进入 cases 还是 manual_cases；不能只补 cases 不补场景。
10. 不得删除已有有效业务链路；如果合并重复用例，要在 review 中说明合并原因，并保留覆盖点。
11. 自动化数量已经达到 generation_targets 上限时，不能因为“数量够了”而保留重复检查并遗漏显式需求点；应合并/替换低价值重复 case，确保每个显式 requirement_point 至少有一个可追溯 case 或完整 manual_case。
12. 对多设备、屏幕形态、宽屏/手机布局等显式需求，若检查点是当前页面可见文案、入口、同级关系或滚动可达性，应生成不绑定机型、只用真实可见文字定位的可复用 case；当前运行只在平台指定设备执行，未执行的其他形态另列 manual_cases，不得在 YAML 内选择第二台设备或写坐标。

当前标题：{title}
当前模块：{module}

覆盖率审查结果：
{audit_json}

待修正 JSON：
{payload_json}
"""


def enforce_min_case_count_audit(audit, targets):
    """Mark coverage audit as not-ok when generated cases are below target."""
    if not isinstance(audit, dict):
        audit = {}
    target_min = safe_int((targets or {}).get("min_automation_cases"), 0)
    case_count = safe_int(audit.get("case_count"), 0)
    if target_min and case_count < target_min:
        audit["ok"] = False
        audit["case_count_below_min"] = True
        audit["min_automation_cases"] = target_min
        audit["actual_case_count"] = case_count
        missing = normalize_text_list(audit.get("missing_case_points") or audit.get("missing_requirement_points"))
        gap = f"自动化用例数量不足：当前 {case_count} 条，至少需要 {target_min} 条；请补齐正常、异常、边界、空态和状态变化场景"
        if gap not in missing:
            missing.append(gap)
        audit["missing_case_points"] = missing
    return audit


def improve_case_coverage(
    title,
    module,
    payload,
    max_rounds=1,
    progress_callback=None,
    time_budget_seconds=None,
    model_config=None,
    targets=None,
):
    """改善用例覆盖度。"""
    current = normalize_cases_payload(payload)
    planned_targets = dict(targets) if isinstance(targets, dict) else None
    started_at = time.time()
    budget = safe_int(time_budget_seconds, AI_COVERAGE_TOTAL_BUDGET_SECONDS) or AI_COVERAGE_TOTAL_BUDGET_SECONDS

    def emit(message, progress=None):
        if callable(progress_callback):
            try:
                progress_callback(message, progress=progress)
            except Exception:
                pass

    def budget_left():
        return budget - int(time.time() - started_at)

    for round_index in range(max_rounds):
        emit(f"覆盖率审查：本地检查第 {round_index + 1}/{max_rounds} 轮", progress=72)
        current, local_audit = audit_case_coverage(current)
        current_targets = planned_targets or generation_volume_targets(current.get("analysis") or {})
        local_audit = enforce_min_case_count_audit(local_audit, current_targets)
        enough_cases = safe_int(local_audit.get("case_count"), 0) >= safe_int(current_targets.get("min_automation_cases"), 0)
        if local_audit.get("ok") and enough_cases and not AI_COVERAGE_MODEL_WHEN_LOCAL_OK:
            review = current.setdefault("review", {})
            local_audit["coverage_auditor_skill"] = "skipped_local_audit_ok"
            local_audit["generation_targets"] = current_targets
            review["coverage_audit"] = local_audit
            review["coverage_auditor_skipped"] = "本地覆盖审查已通过且用例数达到下限，跳过额外模型审查以降低超时风险"
            return current, local_audit
        if budget_left() <= 0:
            review = current.setdefault("review", {})
            local_audit["coverage_auditor_skill"] = "skipped_budget_exhausted"
            local_audit["generation_targets"] = current_targets
            review["coverage_audit"] = local_audit
            review["coverage_auditor_skipped"] = f"覆盖审查已超过 {budget}s 总预算，保留本地覆盖结果继续生成 YAML"
            return current, local_audit
        try:
            emit(f"覆盖率审查：调用 coverage_auditor，第 {round_index + 1}/{max_rounds} 轮，剩余预算约 {max(0, budget_left())} 秒", progress=73)
            audit = call_coverage_auditor_skill(
                title,
                module,
                current,
                local_audit,
                model_config=model_config,
                targets=current_targets,
            )
            audit = enforce_min_case_count_audit(audit, current_targets)
            review = current.setdefault("review", {})
            review["coverage_audit"] = audit
        except Exception as exc:
            audit = local_audit
            review = current.setdefault("review", {})
            review["coverage_auditor_skill"] = "fallback_local_audit"
            review["coverage_auditor_error"] = str(exc)
        if audit.get("ok"):
            return current, audit
        if budget_left() < 30:
            review = current.setdefault("review", {})
            audit["coverage_repair_skipped"] = True
            audit["coverage_repair_skip_reason"] = "剩余预算不足 30s，跳过覆盖修复大模型调用"
            review["coverage_audit"] = audit
            review["coverage_repair_skipped"] = audit["coverage_repair_skip_reason"]
            return current, audit
        emit(f"覆盖率审查：正在补齐遗漏场景，第 {round_index + 1}/{max_rounds} 轮，剩余预算约 {max(0, budget_left())} 秒", progress=74)
        prompt = build_case_coverage_repair_prompt(title, module, current, audit)
        repair_timeout = max(30, min(AI_COVERAGE_REPAIR_TIMEOUT_SECONDS, max(30, budget_left())))
        repair_model_trace = {}
        if isinstance(model_config, dict) and any(
            model_config.get(key)
            for key in ("providerId", "provider", "model", "modelName")
        ):
            content = ai_gateway_skill_content(
                "case_coverage_repair",
                prompt,
                payload={"title": title, "module": module, "audit": audit},
                timeout=repair_timeout,
                temperature=0.1,
                json_response=True,
                model_config=model_config,
                runtime_trace=repair_model_trace,
            )
        else:
            content = dashscope_chat_content(
                prompt,
                image_assets=None,
                temperature=0.1,
                timeout=repair_timeout,
                respect_global_timeout=False,
                retry_count=0,
            )
            repair_model_trace.update({
                "providerId": "dashscope_direct",
                "model": dashscope_text_model(),
                "fallbackUsed": False,
                "source": "dashscope_direct",
            })
        current = normalize_case_json_from_model(content)
        current.setdefault("review", {})["coverage_repair_model_trace"] = _model_config_trace(
            model_config,
            repair_model_trace,
        )
        current["title"] = current.get("title") or title
        current["module"] = current.get("module") or module
        validate_ai_skill_output("cases_payload", current)
    current, audit = audit_case_coverage(current)
    current_targets = planned_targets or generation_volume_targets(current.get("analysis") or {})
    audit = enforce_min_case_count_audit(audit, current_targets)
    current.setdefault("review", {})["coverage_audit"] = audit
    return current, audit


# ---------------------------------------------------------------------------
# DashScope case generation (legacy + skill pipeline)
# ---------------------------------------------------------------------------

def build_case_generation_prompt(title, module, text_assets):
    """构建用例生成 prompt（legacy 模式）。"""
    text_block = "\n\n".join(text_assets).strip()
    try:
        from task_server.prompts import get_prompt_center
        business_prompt = get_prompt_center().get("case", {
            "title": title,
            "target": title,
            "module": module,
            "requirementText": text_block,
        })
    except Exception:
        business_prompt = ""
    return f"""
{business_prompt}

你是资深移动 App UI 自动化测试工程师。
请根据需求文档、原型图或设计稿截图，生成标准测试用例 JSON。需求文档是业务范围和测试意图的主来源，页面知识库和设计稿用于校准真实入口、页面结构和 UI 可见断言。

要求：
1. 只输出合法 JSON，不要 Markdown，不要解释。
2. JSON 根节点必须包含 title、module、analysis、scenarios、cases。
3. JSON 根节点还可以包含 manual_cases、review，用于放置当前环境不可稳定自动执行的场景和自评审结果。
4. cases 是数组，每条用例包含 case_id、title、priority、smoke、preconditions、steps、assertions、tags；建议额外包含 goal、start_page、business_path、expected_result、repair_hints、risk、coverage、data_requirements、automation_reason，便于后续 AI 修复和人工评审理解业务链路。
5. cases 里只放"当前默认测试环境可直接执行"的 UI 自动化用例：入口可自然到达、无需 Mock/造数/系统设置、结果能通过页面标题、入口、列表/空态、按钮状态、弹窗等 UI 信号验证。
6. 不要因为数据结果可能为空就放弃自动化：列表类、记录类、收藏类、资源类页面必须兼容"有数据或空态"两种可见结果。依赖切换登录态、清空账号数据、特殊后台造数、接口 Mock、断网/弱网、排队或并发状态、服务器繁忙、系统权限预置、纯设计稿一致性对比、真实支付/删除等场景必须放入 manual_cases。
7. 如果需求没有明确说明测试账号状态，默认认为当前账号已登录。
8. 需求文档决定"测什么"；Figma/截图只辅助判断"从哪里进入、页面大概有哪些可见信号"。如果 Figma 和需求或真机页面可能不一致，不要把设计稿一致性写进自动化断言，应写入 repair_hints 或 manual_cases。
9. steps 必须是用户可执行的 UI 操作，尽量使用页面真实文案、按钮名、Tab 名、入口名。
10. assertions 必须表达"业务意图 + UI 可见信号"，避免抽象断言，也避免过严断言。除非需求明确要求完全一致，否则不要断言动态列表第几条、动态推荐内容、数量、时间、百分比、随机资源名，也不要写"与设计稿一致/模块排列顺序一致"这类 Runner 无法独立判断的断言。
10.1 每条自动化 case 的 steps 建议 3-6 条，assertions 建议 1-3 条；不要把多个业务分支塞进同一条 YAML。
10.2 智小白 3D AI建模当前入口以真机为准：底部中间 Tab/首页卡片进入 AI建模；不要在首页三维创作区查找旧的"文字输入"入口；标牌/趣味印章等横向入口必须包含横向滑动步骤；"大家都在做"、骨架屏、缩放控件、固定推荐内容等动态或历史稿信号不得作为自动化必过断言。
11. 当前平台采用可执行优先策略：小需求自动化目标 3 条，中需求 5 条，大需求最多 8 条；不要为了数量重复路径或扩展无关页面。其他覆盖点进入 manual_cases 或 draft，不要强行自动化。
12. 不要输出 YAML。

输出格式：
{{
  "title": "{title}",
  "module": "{module}",
  "analysis": {{
    "business_goals": [],
    "roles": [],
    "entry_points": [],
    "state_assumptions": [],
    "data_assumptions": [],
    "risks": [],
    "requirement_points": []
  }},
  "scenarios": [],
  "cases": [],
  "manual_cases": [],
  "review": {{
    "coverage_check": "",
    "automation_check": "",
    "assertion_check": "",
    "dedupe_check": "",
    "remaining_risks": []
  }}
}}

文本资产：
{text_block}
"""


def call_dashscope_cases_legacy(title, module, text_assets, image_assets, model_config=None):
    """Legacy 模式：直接调用 DashScope 生成用例。"""
    prompt = build_case_generation_prompt(title, module, text_assets)
    model_runtime_trace = {}
    explicit_model = isinstance(model_config, dict) and any(
        model_config.get(key)
        for key in ("providerId", "provider", "model", "modelName")
    )
    if explicit_model:
        content = ai_gateway_skill_content(
            "legacy_case_generation",
            prompt,
            payload={"title": title, "module": module},
            timeout=360,
            temperature=0.2,
            json_response=True,
            model_config=model_config,
            image_assets=image_assets,
            runtime_trace=model_runtime_trace,
        )
    else:
        api_key = dashscope_api_key()
        base_url = dashscope_base_url()
        body = json.dumps(build_dashscope_chat_body(
            prompt,
            image_assets=image_assets,
            temperature=0.2,
            json_response=True,
            image_limit=8
        ), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=360) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        model_runtime_trace.update({
            "providerId": "dashscope_direct",
            "model": dashscope_model_for_images(image_assets),
            "fallbackUsed": False,
            "source": "dashscope_direct",
        })
    payload = normalize_case_json_from_model(content)
    payload["title"] = payload.get("title") or title
    payload["module"] = payload.get("module") or module
    payload.setdefault("review", {})["model_trace"] = _model_config_trace(
        model_config,
        model_runtime_trace,
    )
    validate_ai_skill_output("cases_payload", payload)
    return payload


def call_dashscope_cases(title, module, text_assets, image_assets, model_config=None):
    """生成用例：优先 skill pipeline，有截图时走 legacy。"""
    if image_assets:
        return call_dashscope_cases_legacy(title, module, text_assets, image_assets, model_config=model_config)
    try:
        return build_cases_payload_from_skills(title, module, text_assets, model_config=model_config)
    except Exception as exc:
        if isinstance(model_config, dict) and any(
            model_config.get(key)
            for key in ("providerId", "provider", "model", "modelName")
        ):
            raise
        payload = call_dashscope_cases_legacy(title, module, text_assets, image_assets)
        review = payload.setdefault("review", {})
        review["skill_pipeline"] = "fallback_legacy_prompt"
        review["skill_pipeline_error"] = str(exc)
        return payload


def build_case_visual_refine_prompt(title, module, base_payload, visual_text_assets):
    """构建用例视觉精修 prompt（legacy 模式）。"""
    visual_block = "\n\n".join(visual_text_assets).strip() or "无额外页面知识或设计稿文本。"
    base_json = json.dumps(base_payload, ensure_ascii=False, indent=2)
    try:
        from task_server.prompts import get_prompt_center
        business_prompt = get_prompt_center().get("case", {
            "title": title,
            "target": title,
            "module": module,
            "requirementText": "\n\n".join([
                "第一阶段需求用例 JSON:",
                base_json,
                "Figma / 截图 / 页面知识文本:",
                visual_block,
            ]),
        })
    except Exception:
        business_prompt = ""
    return f"""
{business_prompt}

你是资深移动 App UI 自动化测试工程师，现在执行第二阶段：把"需求理解生成的测试用例 JSON"结合 Figma、截图和页面知识，校准为更可执行的 UI 自动化用例 JSON。

重要原则：
1. 第一阶段 JSON 来自需求文档，是业务覆盖范围的主依据。不要因为截图里没看到某个功能，就删除对应需求用例。
2. Figma、截图、页面知识只用于校准：真实页面名称、入口文案、按钮/Tab 名称、导航路径、可见断言、空态/列表/弹窗文案。
3. 只允许参考与当前需求点相关的 Figma 页面；如果 Figma 文件里混有其他页面，不要把无关页面的入口、按钮、文案带入当前需求用例。
4. 可以优化 steps、assertions、expected_result、repair_hints、start_page、business_path、data_requirements，但不要减少需求覆盖点。
5. 如果视觉资料和需求冲突，在 repair_hints 或 manual_cases 里说明冲突；不要静默丢弃需求。
6. 断言要贴近业务意图，不要过严。动态内容使用兼容表达，例如"展示列表内容或空态提示""页面展示标题或核心区域""按钮处于可点击状态"。
7. 每条自动化 case 仍必须可独立执行，步骤短而稳定，不写坐标、XPath、控件层级和固定长等待；需要 Mock/造数/断网/系统权限/排队并发状态/纯设计稿对比的内容转入 manual_cases。
8. 如果第一阶段某条用例只有泛化断言，例如"页面正常展示/跳转成功/结果符合预期"，必须结合视觉资料或业务目标改成 UI 可见业务信号。
9. 如果视觉资料能证明更多当前环境可稳定执行的分支，可以补充 cases，但不得生成和需求无关的控件清单。
10. 输出必须仍是合法 JSON，保留 title、module、analysis、scenarios、cases、manual_cases、review。
11. analysis.requirement_points 必须保留；review 中说明本次视觉校准做了哪些修正。
12. 不允许因为视觉资料缺页就删掉需求场景；只能把入口不确定、数据不稳定、无法自动化的内容转入 manual_cases，并保留 scenarios 覆盖。
12.1 需求文档是业务真相，Figma 是 UI 参考。不要把“与设计稿一致/视觉还原一致/模块排序一致/Figma 节点一致”直接作为 YAML 断言；应改成可见业务信号，或转入人工视觉验收。
13. 保留并补强 analysis.coverage_matrix；视觉校准后，每个 requirement_point 仍必须能追溯到 scenarios、cases 或 manual_cases。

当前标题：{title}
当前模块：{module}

第一阶段需求用例 JSON：
{base_json}

Figma / 截图 / 页面知识文本：
{visual_block}
"""


def call_dashscope_refine_cases_legacy(title, module, base_payload, visual_text_assets, image_assets, timeout_seconds=None):
    """Legacy 模式：直接调用 DashScope 精修用例。"""
    if not visual_text_assets and not image_assets:
        return base_payload
    base_payload = normalize_cases_payload(base_payload)
    prompt = build_case_visual_refine_prompt(title, module, base_payload, visual_text_assets)
    content = dashscope_chat_content(
        prompt,
        image_assets=image_assets,
        temperature=0.1,
        timeout=int(timeout_seconds or 360),
        respect_global_timeout=timeout_seconds is None,
        retry_count=None if timeout_seconds is None else 0,
    )
    payload = normalize_case_json_from_model(content)
    payload["title"] = payload.get("title") or title
    payload["module"] = payload.get("module") or module
    base_points = ((base_payload.get("analysis") or {}).get("requirement_points") or [])
    if base_points:
        analysis = payload.setdefault("analysis", {})
        if not analysis.get("requirement_points"):
            analysis["requirement_points"] = base_points
    validate_ai_skill_output("cases_payload", payload)
    return payload


def call_dashscope_refine_cases(
    title,
    module,
    base_payload,
    visual_text_assets,
    image_assets,
    timeout_seconds=None,
    legacy_fallback=True,
    bounded_retry=False,
    model_config=None,
):
    """精修用例：优先 visual_grounder skill，失败回退 legacy。"""
    if not visual_text_assets and not image_assets:
        return base_payload
    started = time.time()
    total_timeout = max(30, safe_int(timeout_seconds, 360)) if timeout_seconds is not None else None
    first_timeout = total_timeout
    if bounded_retry and total_timeout and total_timeout >= 60:
        first_timeout = max(30, int(total_timeout / 2))
    attempts = []
    try:
        if timeout_seconds is None:
            payload = call_visual_grounder_skill(
                title,
                module,
                base_payload,
                visual_text_assets,
                image_assets,
                model_config=model_config,
            )
        else:
            payload = call_visual_grounder_skill(
                title,
                module,
                base_payload,
                visual_text_assets,
                image_assets,
                timeout_seconds=first_timeout,
                model_config=model_config,
            )
        payload.setdefault("review", {})["visual_grounder_attempts"] = {
            "count": 1,
            "boundedRetryEnabled": bool(bounded_retry),
            "retryUsed": False,
        }
        return payload
    except Exception as exc:
        attempts.append(str(exc)[:300])
        if bounded_retry and total_timeout:
            elapsed = max(0, int(time.time() - started))
            remaining_timeout = total_timeout - elapsed
            if remaining_timeout >= 30:
                try:
                    payload = call_visual_grounder_skill(
                        title,
                        module,
                        base_payload,
                        visual_text_assets,
                        image_assets,
                        timeout_seconds=remaining_timeout,
                        model_config=model_config,
                    )
                    payload.setdefault("review", {})["visual_grounder_attempts"] = {
                        "count": 2,
                        "boundedRetryEnabled": True,
                        "retryUsed": True,
                        "firstError": attempts[0],
                    }
                    return payload
                except Exception as retry_exc:
                    attempts.append(str(retry_exc)[:300])
        if not legacy_fallback:
            if len(attempts) > 1:
                raise RuntimeError(
                    "视觉 AI 增量校准在同一批次预算内两次失败：" + "；".join(attempts)
                ) from exc
            raise
        if isinstance(model_config, dict) and (
            model_config.get("providerId")
            or model_config.get("provider")
            or model_config.get("model")
            or model_config.get("modelName")
        ):
            raise RuntimeError(
                "显式选择的 Agent 模型及其 Gateway 能力降级均失败，禁止静默切换到平台直连视觉模型："
                + "；".join(attempts)
            ) from exc
        if timeout_seconds is None:
            payload = call_dashscope_refine_cases_legacy(title, module, base_payload, visual_text_assets, image_assets)
        else:
            payload = call_dashscope_refine_cases_legacy(title, module, base_payload, visual_text_assets, image_assets, timeout_seconds=timeout_seconds)
        review = payload.setdefault("review", {})
        review["visual_grounder_skill"] = "fallback_legacy_refine_prompt"
        review["visual_grounder_error"] = str(exc)
        return payload


# ---------------------------------------------------------------------------
# Knowledge screenshot analysis
# ---------------------------------------------------------------------------

def analyze_knowledge_screenshot(data):
    """分析知识库截图，生成页面知识草稿。"""
    api_key = dashscope_api_key()

    screenshot = data.get("screenshot") or {}
    if not screenshot.get("contentBase64"):
        raise ValueError("请先上传页面截图")

    name = clean_asset_filename(screenshot.get("name") or "page.png")
    if not is_image_file(name):
        raise ValueError("页面截图只支持 png / jpg / jpeg")

    app_package = data.get("app_package") or data.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    hint = data.get("hint") or ""
    existing_page_name = data.get("page_name") or data.get("pageName") or ""
    prompt = f"""
你是移动 App UI 自动化测试知识库维护助手。
请根据截图识别这个页面，生成可维护的页面知识草稿。

要求：
1. 只输出合法 JSON，不要 Markdown，不要解释。
2. 不要编造截图里看不到的按钮、入口、Tab 或文案。
3. key_elements 用真实可见文案或稳定入口描述，适合给 Midscene 的 aiTap/aiAction 使用。
4. common_assertions 必须是页面上可以视觉验证的内容。
5. route 如果截图无法判断，可给出空字符串或"待补充"。
6. page_name 尽量用页面标题、Tab 名、核心业务名。

APP 包名：{app_package}
人工提示：{hint}
已有页面名称：{existing_page_name}

输出格式：
{{
  "page_name": "我的页",
  "route": "点击底部 Tab「我的」",
  "description": "用户个人中心页面，包含我的收藏、打印记录等入口。",
  "key_elements": ["底部 Tab「我的」", "入口「我的收藏」", "入口「打印记录」"],
  "common_assertions": ["页面展示「我的收藏」入口", "页面展示「打印记录」入口"],
  "tags": ["我的", "个人中心"]
}}
"""

    base_url = dashscope_base_url()
    body = json.dumps({
        "model": dashscope_vl_model(),
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{guess_mime(name)};base64,{screenshot['contentBase64']}"
                        }
                    }
                ]
            }
        ],
        "temperature": 0.1
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        resp_data = json.loads(resp.read().decode("utf-8"))

    draft = normalize_model_json(resp_data["choices"][0]["message"]["content"])
    return {
        "page_name": draft.get("page_name") or draft.get("pageName") or existing_page_name or "未命名页面",
        "route": draft.get("route") or "",
        "description": draft.get("description") or "",
        "key_elements": normalize_lines(draft.get("key_elements") or draft.get("keyElements")),
        "common_assertions": normalize_lines(draft.get("common_assertions") or draft.get("commonAssertions")),
        "tags": normalize_lines(draft.get("tags"))
    }


__all__ = [
    # AI skill core
    "ai_skill_path",
    "load_ai_skill_prompt",
    "load_ai_skill_schema",
    "validate_json_schema_minimal",
    "validate_ai_skill_output",
    "render_ai_skill_prompt",
    "run_ai_skill",
    # DashScope chat
    "build_dashscope_chat_body",
    "dashscope_chat_content",
    # Utility
    "normalize_lines",
    "is_image_file",
    "guess_mime",
    "normalize_case_json_from_model",
    "compact_text_assets",
    # Failure analysis
    "runtime_toast_error_from_text",
    "evidence_is_toast_assertion_issue",
    "review_ui_terms",
    "detect_wait_strategy_issue",
    "detect_horizontal_scroll_script_issue",
    "sanitize_failure_review_against_sources",
    "extract_failure_brief",
    "repair_strategy_guide",
    "execution_screenshot_context",
    "flow_items_with_index",
    "failure_target_terms",
    "locate_failure_window",
    "build_failure_context",
    "classify_failure_by_context",
    # Requirement analysis
    "normalize_source_quality",
    "normalize_readiness_level",
    "normalize_requirement_analysis_result",
    "call_skill_requirement_analyzer",
    # Scenario & automation
    "generation_volume_targets",
    "generation_targets_for_scope",
    "scenario_requirement_point",
    "case_matches_requirement",
    "build_skill_coverage_matrix",
    "call_skill_scenario_designer",
    "call_skill_automation_filter",
    "call_skill_smoke_selector",
    "select_smoke_cases_for_payload",
    "apply_smoke_selection_to_cases",
    "build_cases_payload_from_skills",
    "should_fast_path_baidu_entry_visibility",
    "call_skill_baseline_reranker",
    "call_skill_execution_scope_planner",
    "call_skill_executable_yaml_planner",
    "apply_executable_yaml_plan_to_payload",
    "executable_yaml_portfolio_audit",
    # Visual grounder
    "call_visual_grounder_skill",
    # Coverage auditor
    "call_coverage_auditor_skill",
    "build_case_coverage_repair_prompt",
    "improve_case_coverage",
    # Case generation
    "build_case_generation_prompt",
    "call_dashscope_cases_legacy",
    "call_dashscope_cases",
    "build_case_visual_refine_prompt",
    "call_dashscope_refine_cases_legacy",
    "call_dashscope_refine_cases",
    # Knowledge screenshot
    "analyze_knowledge_screenshot",
]
