#!/usr/bin/env python3
import base64
import hashlib
from contextlib import contextmanager
import json
import http.cookiejar
import os
import platform
import plistlib
import tempfile
import queue
import re
import shutil
import socket
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SERVER = os.getenv("TASK_SERVER", "http://101.34.197.12:8088")
RUNNER_ID = os.getenv("RUNNER_ID", "mac-runner-01")
TOKEN = os.getenv("MIDSCENE_RUNNER_TOKEN", "").strip()
WORKSPACE = Path(os.getenv("MIDSCENE_RUNNER_WORKSPACE", str(Path.home() / "midscene-runner"))).expanduser()
CALLBACK_OUTBOX_DIR = WORKSPACE / "callback_outbox"
RUNNER_VERSION = os.getenv("MIDSCENE_RUNNER_VERSION", "2026.08.31-qwen3.7-result-retry-v1-tls")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "3"))
MIDSCENE_BIN = os.getenv("MIDSCENE_BIN", "midscene")
ADB_BIN = os.getenv("ADB_BIN", "adb")
TIMEOUT_SECONDS = int(os.getenv("MIDSCENE_TIMEOUT", "300"))
ADB_DEVICE_CHECK_INTERVAL = int(os.getenv("ADB_DEVICE_CHECK_INTERVAL", "10"))
_ADB_BIN_CACHE = None
_LAST_NO_DEVICE_LOG_AT = 0
_LAST_SERVER_ERROR_LOG_AT = 0
_REPORT_UPLOAD_QUEUE = queue.Queue()
_REPORT_UPLOAD_WORKER_STARTED = False
_REPORT_UPLOAD_WORKER_LOCK = threading.Lock()
_RUNTIME_ENV_CACHE = None
_RUNTIME_ENV_ERROR = ""
_RUNTIME_ENV_FETCHED_AT = 0
RUNTIME_ENV_CACHE_SECONDS = int(os.getenv("MIDSCENE_RUNTIME_ENV_CACHE_SECONDS", "60"))
WEAK_RUNNER_TOKENS = {"", "midscene2026", "change-me", "changeme", "test", "token"}
DEFAULT_APP_PACKAGES = "com.kfb.model,com.xbxxhz.box"
RUNNER_CAPABILITIES = {
    "yaml_dry_run": True,
    "apk_install": True,
}
DEVICE_MARKET_NAME_BY_MODEL = {
    "ELS-AN00": "HUAWEI P40 Pro",
    "PHM110": "OPPO Reno9",
}

RUNNER_PLATFORM = os.getenv("MIDSCENE_RUNNER_PLATFORM", "android").lower().strip()
IOS_BUNDLE_ID = os.getenv("IOS_APP_BUNDLE_ID", "").strip()
IOS_SOURCE_APP = os.getenv("IOS_SOURCE_APP_PACKAGE", "").strip()
IOS_EXECUTION_ENABLED = os.getenv("IOS_EXECUTION_ENABLED", "0") == "1"
if RUNNER_PLATFORM == "ios":
    RUNNER_CAPABILITIES.update(yaml_dry_run=False, apk_install=False, device_preview=True, job_execution=IOS_EXECUTION_ENABLED, explicit_target_only=True, ios_case_reuse=IOS_EXECUTION_ENABLED)
    RUNNER_VERSION = "2026.10.13-ios-execution-v1-step-progress-v1-wda-recovery-v1"
ANDROID_UI_ACTIONS = ["launch", "terminate", "sleep", "runAdbShell", "ai", "aiAct", "aiAction",
                      "aiTap", "aiHover", "aiInput", "aiKeyboardPress", "aiScroll", "aiAssert",
                      "aiWaitFor", "aiQuery", "aiAsk", "aiBoolean", "aiNumber", "aiString",
                      "recordToReport"]
RUNNER_CAPABILITIES.update(ui_execution_contract="ui-v1", ui_platforms=[RUNNER_PLATFORM],
                           ui_actions=["launch", "aiTap", "aiWaitFor", "aiAssert"] if RUNNER_PLATFORM == "ios" else ANDROID_UI_ACTIONS,
                           android_baseline=RUNNER_PLATFORM == "android")



RUNNER_CAPABILITIES["ui_attempt_events"] = 1
RUNNER_CAPABILITIES["ui_private_reports"] = 1
RUNNER_CAPABILITIES["ui_process_stop_proof"] = 1


def ios_job_matches(job, runner_id, device_id):
    return bool(device_id and job.get("device_id") == device_id and job.get("target_runner_id") == runner_id)


def ios_job_mapping(job, launches):
    if job.get("ui_execution_contract") == "ui-v1":
        source_app, bundle_id = job.get("app_id"), job.get("target_package")
        if job.get("platform") != "ios" or not source_app or not bundle_id or not launches or any(item != bundle_id for item in launches):
            raise ValueError("iOS 编译副本与任务应用映射不一致")
        return source_app, bundle_id
    mappings = ios_app_mappings()
    source_app = next((key for key, value in mappings.items() if launches and launches[0] in (key, value)), "")
    if not source_app:
        raise ValueError("用例缺少已配置映射的启动应用步骤")
    return source_app, mappings[source_app]


def prepare_ios_yaml(text, *, source_app, bundle_id, wda_port):
    """Compile an execution copy; never rewrite the shared source asset.

    This first compatibility gate supports semantic navigation only. Anything
    outside the verified subset requires an explicit platform adaptation.
    """
    import yaml
    if not source_app or not bundle_id or not 1 <= int(wda_port) <= 65535:
        raise ValueError("iOS 应用映射或 WDA 端口未配置")
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or set(data) - {"android", "ios", "tasks", "agent"}:
        raise ValueError("当前跨端验证仅支持平台配置、agent 与 tasks")
    if "agent" in data and not isinstance(data["agent"], dict):
        raise ValueError("agent 配置无效")
    if ("android" in data) == ("ios" in data):
        raise ValueError("必须且只能指定 android 或 ios")
    source_platform = "android" if "android" in data else "ios"
    interface = data[source_platform] or {}
    if not isinstance(interface, dict) or set(interface) - ({"deviceId"} if source_platform == "android" else set()):
        raise ValueError("平台专用连接/启动配置需先适配，不能静默丢弃")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("缺少可执行 tasks")
    for task in tasks:
        if not isinstance(task, dict) or set(task) - {"name", "flow"} or not isinstance(task.get("flow"), list) or not task["flow"]:
            raise ValueError("用例结构需要人工适配")
        for index, step in enumerate(task["flow"], 1):
            if not isinstance(step, dict):
                raise ValueError(f"第 {index} 步结构无效")
            actions = set(step) & {"launch", "aiTap", "aiWaitFor", "aiAssert"}
            if len(actions) != 1:
                raise ValueError(f"第 {index} 步不是已验证的跨端导航动作")
            action = next(iter(actions))
            if set(step) - {action, *(('timeout',) if action == 'aiWaitFor' else ())}:
                raise ValueError(f"第 {index} 步含待适配参数")
            if not isinstance(step[action], str) or not step[action].strip():
                raise ValueError(f"第 {index} 步必须使用语义目标，不能复用坐标")
            if action == "launch":
                expected = source_app if source_platform == "android" else bundle_id
                if step[action] != expected:
                    raise ValueError(f"第 {index} 步的应用尚未配置跨端映射")
                step[action] = bundle_id
    data.pop(source_platform)
    data["ios"] = {"wdaHost": "127.0.0.1", "wdaPort": int(wda_port)}
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def validate_runner_config():
    if not SERVER.strip():
        raise RuntimeError("TASK_SERVER 未配置，请设置为 Task 平台地址，例如 http://101.34.197.12:8088")
    if TOKEN in WEAK_RUNNER_TOKENS:
        raise RuntimeError("MIDSCENE_RUNNER_TOKEN 未配置或仍使用弱默认值，请与服务端 /opt/midscene.env 保持一致")


def normalize_device_model(value):
    return str(value or "").strip().upper().replace("_", "-")


def device_market_name(adb_bin, device_id, brand="", model=""):
    props = (
        "ro.product.marketname",
        "ro.product.vendor.marketname",
        "ro.config.marketing_name",
        "ro.vendor.product.marketname",
        "ro.product.odm.marketname",
        "ro.product.oplus.marketname",
        "ro.product.system.marketname",
    )
    for prop in props:
        try:
            value = adb_shell_text(adb_bin, device_id, "getprop", prop, timeout=5)
        except Exception:
            value = ""
        if value:
            return value
    mapped = DEVICE_MARKET_NAME_BY_MODEL.get(normalize_device_model(model))
    if mapped:
        return mapped
    return " ".join([part for part in [brand, model] if part]).strip()


def android_sdk_root_from_adb(adb_path=""):
    candidates = []
    for raw in (adb_path, _ADB_BIN_CACHE, ADB_BIN):
        raw = str(raw or "").strip()
        if raw:
            candidates.append(raw)
    for raw in candidates:
        try:
            path = Path(raw).expanduser()
            if not path.exists():
                continue
            resolved = path.resolve()
            if resolved.parent.name == "platform-tools":
                return str(resolved.parent.parent)
        except Exception:
            continue
    return ""


def ensure_android_sdk_env(env):
    sdk_root = env.get("ANDROID_SDK_ROOT") or env.get("ANDROID_HOME") or android_sdk_root_from_adb()
    if not sdk_root:
        return env
    env.setdefault("ANDROID_SDK_ROOT", sdk_root)
    env.setdefault("ANDROID_HOME", sdk_root)
    platform_tools = str(Path(sdk_root).expanduser() / "platform-tools")
    path_value = env.get("PATH") or ""
    if platform_tools and platform_tools not in path_value.split(os.pathsep):
        env["PATH"] = platform_tools + os.pathsep + path_value
    return env


def infer_midscene_model_family(model_name, configured_family=""):
    name = str(model_name or "").strip().lower()
    if "qwen3.7" in name:
        return "qwen3"
    if "qwen3.6" in name:
        return "qwen3.6"
    if "qwen3.5" in name:
        return "qwen3.5"
    if "qwen3-vl" in name:
        return "qwen3-vl"
    if "qwen2.5-vl" in name or name.startswith("qwen-vl"):
        return "qwen2.5-vl"
    return str(configured_family or "").strip()


def midscene_env(device_id=""):
    env = os.environ.copy()
    runtime = task_runtime_env()
    server_model_keys = {
        "MIDSCENE_MODEL_API_KEY",
        "MIDSCENE_MODEL_BASE_URL",
        "MIDSCENE_MODEL_NAME",
        "MIDSCENE_MODEL_FAMILY",
        "MIDSCENE_MODEL_TIMEOUT",
        "MIDSCENE_MODEL_RETRY_COUNT",
        "MIDSCENE_MODEL_RETRY_INTERVAL",
    }
    for key in (
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
        "DASHSCOPE_VL_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "MIDSCENE_MODEL_API_KEY",
        "MIDSCENE_MODEL_BASE_URL",
        "MIDSCENE_MODEL_NAME",
        "MIDSCENE_MODEL_FAMILY",
        "MIDSCENE_MODEL_TIMEOUT",
        "MIDSCENE_MODEL_RETRY_COUNT",
        "MIDSCENE_MODEL_RETRY_INTERVAL",
        "MIDSCENE_USE_QWEN_VL",
        "MIDSCENE_USE_QWEN3_VL",
        "MIDSCENE_USE_DOUBAO_VISION",
        "MIDSCENE_USE_GEMINI",
        "MIDSCENE_USE_VLM_UI_TARS",
        "MIDSCENE_SKIP_CONFIG_CHECK",
        "MIDSCENE_REPLANNING_CYCLE_LIMIT",
    ):
        if runtime.get(key) and (key in server_model_keys or not env.get(key)):
            env[key] = runtime[key]
    if env.get("DASHSCOPE_API_KEY") and not env.get("OPENAI_API_KEY"):
        env["OPENAI_API_KEY"] = env["DASHSCOPE_API_KEY"]
    env.setdefault("OPENAI_BASE_URL", env.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    env.setdefault("MIDSCENE_MODEL_API_KEY", env.get("DASHSCOPE_API_KEY") or env.get("OPENAI_API_KEY", ""))
    env.setdefault("MIDSCENE_MODEL_BASE_URL", env.get("DASHSCOPE_BASE_URL") or env.get("OPENAI_BASE_URL", ""))
    env.setdefault("MIDSCENE_MODEL_NAME", env.get("DASHSCOPE_VL_MODEL", "qwen3.7-plus"))
    model_family = infer_midscene_model_family(env.get("MIDSCENE_MODEL_NAME"), env.get("MIDSCENE_MODEL_FAMILY"))
    if model_family:
        env["MIDSCENE_MODEL_FAMILY"] = model_family
        for legacy_key in (
            "MIDSCENE_USE_QWEN_VL",
            "MIDSCENE_USE_QWEN3_VL",
            "MIDSCENE_USE_DOUBAO_VISION",
            "MIDSCENE_USE_GEMINI",
            "MIDSCENE_USE_VLM_UI_TARS",
        ):
            env.pop(legacy_key, None)
    env.setdefault("MIDSCENE_SKIP_CONFIG_CHECK", "1")
    env.setdefault("MIDSCENE_REPLANNING_CYCLE_LIMIT", "8")
    # Use Node's default certificate verification; preserve an explicit operator override.
    # Private CAs should be supplied with NODE_EXTRA_CA_CERTS instead of disabling TLS.
    if RUNNER_PLATFORM == "ios" or env.get("MIDSCENE_RUNNER_PLATFORM") == "ios":
        env.pop("ANDROID_SERIAL", None)
        env.pop("DEVICE_ID", None)
        # Loopback WDA must not be sent to an operator's system HTTP proxy.
        env["NO_PROXY"] = ",".join(filter(None, [env.get("NO_PROXY", ""), "127.0.0.1", "localhost"]))
    else:
        ensure_android_sdk_env(env)
    if device_id and RUNNER_PLATFORM != "ios" and env.get("MIDSCENE_RUNNER_PLATFORM") != "ios":
        env["ANDROID_SERIAL"] = str(device_id)
        env["DEVICE_ID"] = str(device_id)
    return env


def http_json(method, path, payload=None, timeout=60):
    url = SERVER.rstrip("/") + path
    data = None
    headers = {"x-token": TOKEN}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def task_runtime_env(force=False):
    global _RUNTIME_ENV_CACHE, _RUNTIME_ENV_ERROR, _RUNTIME_ENV_FETCHED_AT
    now = time.time()
    if _RUNTIME_ENV_CACHE is not None and not force and now - _RUNTIME_ENV_FETCHED_AT < RUNTIME_ENV_CACHE_SECONDS:
        return _RUNTIME_ENV_CACHE
    try:
        data = http_json("GET", "/api/sonic/runtime-env", timeout=15)
        values = data.get("env") if isinstance(data, dict) else {}
        _RUNTIME_ENV_CACHE = values if isinstance(values, dict) else {}
        _RUNTIME_ENV_ERROR = "" if _RUNTIME_ENV_CACHE else "接口未返回模型配置"
    except Exception as exc:
        _RUNTIME_ENV_CACHE = {}
        _RUNTIME_ENV_ERROR = str(exc)
    _RUNTIME_ENV_FETCHED_AT = now
    return _RUNTIME_ENV_CACHE


def http_error_text(error, limit=1200):
    try:
        body = error.read().decode("utf-8", errors="ignore")
    except Exception:
        body = str(error)
    body = body.strip()
    return body[:limit] if body else str(error)


def ui_report_upload_context(job_id):
    if not str(job_id).startswith("uib_"):
        return {}
    context = _UI_DELIVERIES.get(job_id)
    if not context or not context.get("ui_claim_token"):
        raise ValueError("Private UI report requires delivery credentials")
    return {"job_id": job_id, **{key: context[key] for key in ("runner_id", "device_id", "ui_claim_token")}}


def http_upload_report(path, filename, job_id=""):
    context = ui_report_upload_context(job_id)
    if not path or not path.exists():
        return ""
    url = SERVER.rstrip("/") + "/report"
    safe_name = urllib.parse.quote(filename)
    data = path.read_bytes()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "x-token": TOKEN,
            **({"x-ui-job-id": job_id, "x-ui-claim-token": context["ui_claim_token"],
                "x-ui-runner-id": context["runner_id"], "x-ui-device-id": context["device_id"]} if context else {}),
            "x-filename": safe_name,
            "Content-Type": "text/html; charset=utf-8"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        if e.code not in (413, 502, 504):
            raise
        print(f"Report direct upload HTTP {e.code}, switching to chunk upload")
        return http_upload_report_chunked(path, filename, job_id=job_id)


def http_upload_report_chunked(path, filename, chunk_size=256 * 1024, job_id=""):
    context = ui_report_upload_context(job_id)
    data = path.read_bytes()
    upload_id = f"{job_id}-{time.time_ns()}" if context else f"{RUNNER_ID}-{int(time.time())}-{safe_filename(filename)}"
    total = max(1, (len(data) + chunk_size - 1) // chunk_size)
    for index in range(total):
        chunk = data[index * chunk_size:(index + 1) * chunk_size]
        http_json("POST", "/api/report/chunk", {
            **context,
            "upload_id": upload_id,
            "filename": filename,
            "index": index,
            "total": total,
            "contentBase64": base64.b64encode(chunk).decode("ascii")
        }, timeout=180)
    result = http_json("POST", "/api/report/chunk-finish", {
        **context,
        "upload_id": upload_id,
        "total": total
    }, timeout=180)
    return result.get("url", "")


def compact_result_payload(payload, reason):
    light = dict(payload)
    light.pop("report_html", None)
    light.pop("screenshots", None)
    light["stdout"] = (light.get("stdout") or "")[-4000:]
    light["stderr"] = (light.get("stderr") or "")[-4000:]
    summary = light.get("summary")
    try:
        summary_size = len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) if summary is not None else 0
    except Exception:
        summary_size = 0
    if summary_size > 300 * 1024:
        light["summary"] = {
            "compact": True,
            "reason": "summary 过大，Runner 已保留本地 summary 文件",
            "original_size": summary_size
        }
    light["upload_warning"] = reason
    return light


def http_json_retry(method, path, payload=None, timeout=60, attempts=3, label="HTTP"):
    last_error = None
    for index in range(max(1, attempts)):
        try:
            return http_json(method, path, payload, timeout=timeout)
        except Exception as e:
            last_error = e
            if index < attempts - 1:
                time.sleep(min(2 + index * 2, 6))
    raise RuntimeError(f"{label} 失败：{last_error}") from last_error


# UI attempt credentials stay local to the claimed execution and its retry payload.
_UI_DELIVERIES = {}


def initialize_ui_delivery(job):
    if not job.get("ui_batch_id"):
        return
    if (not job.get("device_id") or job.get("target_runner_id") != RUNNER_ID
            or not job.get("ui_claim_token") or job.get("ui_attempt_events") != 1):
        raise ValueError("UI batch requires an exact Runner/device and claim credential")
    _UI_DELIVERIES.setdefault(job["job_id"], {
        "runner_id": RUNNER_ID, "device_id": job["device_id"],
        "ui_claim_token": job["ui_claim_token"], "sequence": 0, "stopped": True})


def ui_callback_payload(job_id, payload, result=False):
    context = _UI_DELIVERIES.get(job_id)
    if context and "ui_event_sequence" not in payload:
        context["sequence"] += 1
        payload.update({key: context[key] for key in ("runner_id", "device_id", "ui_claim_token")})
        payload["ui_event_sequence"] = context["sequence"]
        if result:
            payload["execution_stopped"] = context["stopped"]
    return payload


def post_job_result(job_id, payload):
    ui_callback_payload(job_id, payload, result=True)
    try:
        return http_json_retry("POST", f"/api/runner/jobs/{job_id}/result", payload, timeout=120, attempts=3, label="结果回传")
    except urllib.error.HTTPError as e:
        body = http_error_text(e)
        if e.code in (413, 502, 504):
            reason = f"原始结果回传 HTTP {e.code}，已改用轻量结果回传；原始错误：{body[:300]}"
            print("Result post failed, retrying with compact payload")
            light = compact_result_payload(payload, reason)
            try:
                return http_json_retry("POST", f"/api/runner/jobs/{job_id}/result", light, timeout=120, attempts=3, label="轻量结果回传")
            except urllib.error.HTTPError as retry_error:
                retry_body = http_error_text(retry_error)
                raise RuntimeError(f"结果回传失败：HTTP {retry_error.code} {retry_body}") from retry_error
        raise RuntimeError(f"结果回传失败：HTTP {e.code} {body}") from e


def post_job_report_ready(job_id, report_url="", local_report_path="", report_upload_error=""):
    return http_json_retry("POST", f"/api/runner/jobs/{job_id}/report-ready", ui_callback_payload(job_id, {
        "report_url": report_url,
        "local_report_path": local_report_path,
        "report_upload_error": report_upload_error,
    }), timeout=60, attempts=3, label="报告回传")


def upload_report_before_result(job_id, report_path, report_name):
    if not report_path or not report_name:
        return "", ""
    try:
        report_url = http_upload_report(Path(report_path), report_name, job_id=job_id)
        print(f"Uploaded report before result callback: {report_url}")
        try:
            post_job_report_ready(job_id, report_url, str(report_path), "")
        except Exception as e:
            print(f"Immediate report association warning: {e}")
        return report_url, ""
    except Exception as e:
        upload_error = str(e)
        print(f"Immediate report upload failed, will keep background fallback: {upload_error}")
        return "", upload_error


def _callback_outbox_path(job_id):
    safe = safe_filename(job_id).replace(".yaml", "").replace(".yml", "") or "job"
    return CALLBACK_OUTBOX_DIR / f"{safe}.json"


def queue_failed_result_callback(job_id, payload, pending_report_path="", pending_report_name=""):
    CALLBACK_OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "job_id": job_id,
        "payload": payload,
        "pending_report_path": pending_report_path,
        "pending_report_name": pending_report_name,
        "queued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    target = _callback_outbox_path(job_id)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    print(f"Queued result callback for replay: {job_id}")


def replay_pending_result_callbacks():
    if not CALLBACK_OUTBOX_DIR.exists():
        return 0
    replayed = 0
    for path in sorted(CALLBACK_OUTBOX_DIR.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            job_id = record.get("job_id") or path.stem
            payload = record.get("payload") or {}
            pending_report_path = record.get("pending_report_path") or ""
            pending_report_name = record.get("pending_report_name") or ""
            if not isinstance(payload, dict) or not job_id:
                path.unlink(missing_ok=True)
                continue
            if payload.get("ui_claim_token"):
                _UI_DELIVERIES.setdefault(job_id, {
                    **{k: payload[k] for k in ("runner_id", "device_id", "ui_claim_token")},
                    "sequence": payload["ui_event_sequence"], "stopped": payload.get("execution_stopped", False)})
            post_job_result(job_id, payload)
            if payload.get("report_upload_pending"):
                enqueue_report_upload(job_id, pending_report_path, pending_report_name)
            path.unlink(missing_ok=True)
            replayed += 1
            print(f"Replayed queued result callback: {job_id}")
        except Exception as e:
            print(f"Queued result callback replay paused: {path.name}: {e}")
            break
    return replayed


def report_upload_worker():
    while True:
        job_id, report_path, report_name = _REPORT_UPLOAD_QUEUE.get()
        try:
            report_url = ""
            upload_error = ""
            try:
                report_url = http_upload_report(Path(report_path), report_name, job_id=job_id)
                print(f"Uploaded report in background: {report_url}")
            except Exception as e:
                upload_error = str(e)
                print(f"Background report upload failed: {upload_error}")
            try:
                post_job_report_ready(job_id, report_url, report_path, upload_error)
            except Exception as e:
                print(f"Background report association failed: {e}")
        finally:
            _REPORT_UPLOAD_QUEUE.task_done()


def enqueue_report_upload(job_id, report_path, report_name):
    global _REPORT_UPLOAD_WORKER_STARTED
    if not report_path:
        return
    with _REPORT_UPLOAD_WORKER_LOCK:
        if not _REPORT_UPLOAD_WORKER_STARTED:
            thread = threading.Thread(target=report_upload_worker, name="midscene-report-uploader", daemon=True)
            thread.start()
            _REPORT_UPLOAD_WORKER_STARTED = True
    _REPORT_UPLOAD_QUEUE.put((job_id, str(report_path), report_name))
    print("Report queued for background upload")


def post_job_progress(job_id, payload):
    ui_callback_payload(job_id, payload)
    try:
        return http_json_retry("POST", f"/api/runner/jobs/{job_id}/progress", payload, timeout=15, attempts=2, label="进度回传")
    except Exception as e:
        print(f"Progress post failed: {e}")
        return None


def diagnose_server_error(error):
    global _LAST_SERVER_ERROR_LOG_AT
    now = time.time()
    if now - _LAST_SERVER_ERROR_LOG_AT < 15:
        return
    _LAST_SERVER_ERROR_LOG_AT = now
    print(f"Server connection error: {error}")
    for path in ("/api/modules", "/api/runners"):
        try:
            data = http_json("GET", path, timeout=10)
            print(f"  {path}: ok")
            if path == "/api/runners":
                print(f"  runners keys: {list((data.get('runners') or {}).keys())}")
        except urllib.error.HTTPError as e:
            print(f"  {path}: HTTP {e.code} {http_error_text(e, 200)}")
        except Exception as e:
            print(f"  {path}: failed: {e}")
    print("  服务端建议检查：tail -n 120 /opt/midscene-upload.log")


def safe_filename(name):
    bad = '<>:"/\\|?*'
    name = str(name or "task.yaml")
    for ch in bad:
        name = name.replace(ch, "_")
    return name or "task.yaml"


def resolve_command(command, label):
    if command:
        expanded = Path(command).expanduser()
        if expanded.exists():
            return str(expanded)
    resolved = shutil.which(command) if command else None
    if resolved:
        return resolved
    raise RuntimeError(f"找不到 {label} 命令：{command}。请检查 PATH，或设置环境变量。")


def unique_existing_paths(paths):
    result = []
    seen = set()
    for item in paths:
        if not item:
            continue
        path = str(Path(item).expanduser())
        if path in seen:
            continue
        seen.add(path)
        if Path(path).exists():
            result.append(path)
    return result


def adb_candidates():
    candidates = []
    if ADB_BIN and ADB_BIN != "adb":
        candidates.append(ADB_BIN)
    which_adb = shutil.which("adb")
    if which_adb:
        candidates.append(which_adb)

    android_home = os.getenv("ANDROID_HOME") or os.getenv("ANDROID_SDK_ROOT")
    if android_home:
        candidates.append(str(Path(android_home).expanduser() / "platform-tools" / "adb"))

    candidates.extend([
        "~/Library/Android/sdk/platform-tools/adb",
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        "/usr/local/platform-tools/adb",
        "/Applications/Android Studio.app/Contents/bin/adb",
    ])
    return unique_existing_paths(candidates)


def run_cmd(args, timeout=20):
    return subprocess.run(
        args,
        text=True,
        capture_output=True,
        timeout=timeout
    )


def parse_adb_devices(output):
    devices = []
    unauthorized = []
    offline = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[1] == "device":
            devices.append(parts[0])
        elif parts[1] == "unauthorized":
            unauthorized.append(parts[0])
        elif parts[1] == "offline":
            offline.append(parts[0])
    return devices, unauthorized, offline


def runner_app_packages():
    raw = os.getenv("RUNNER_APP_PACKAGES") or os.getenv("APP_PACKAGE") or DEFAULT_APP_PACKAGES
    result = []
    seen = set()
    for item in re.split(r"[,;\s]+", raw or ""):
        package = item.strip()
        if not package or package in seen:
            continue
        seen.add(package)
        result.append(package)
    return result[:8]


def adb_shell_text(adb_bin, device_id, *args, timeout=10):
    result = run_cmd([adb_bin, "-s", device_id, "shell", *args], timeout=timeout)
    return (result.stdout or "").strip()


def detect_package_info(adb_bin, device_id, package_name):
    info = {"package": package_name, "installed": False, "version_name": "", "version_code": ""}
    try:
        path = adb_shell_text(adb_bin, device_id, "pm", "path", package_name, timeout=10)
        if not path:
            return info
        info["installed"] = True
        dump = adb_shell_text(adb_bin, device_id, "dumpsys", "package", package_name, timeout=12)
        name_match = re.search(r"versionName=([^\s]+)", dump)
        code_match = re.search(r"versionCode=(\d+)", dump)
        if name_match:
            info["version_name"] = name_match.group(1)
        if code_match:
            info["version_code"] = code_match.group(1)
    except Exception:
        pass
    return info


def resolve_adb_with_devices(require_devices=True):
    global _ADB_BIN_CACHE
    configured = os.getenv("DEVICE_ID") or os.getenv("ANDROID_DEVICE_ID")
    if _ADB_BIN_CACHE and Path(_ADB_BIN_CACHE).exists():
        if not require_devices:
            return _ADB_BIN_CACHE, []
        result = run_cmd([_ADB_BIN_CACHE, "devices"], timeout=20)
        devices, unauthorized, offline = parse_adb_devices(result.stdout)
        if configured or devices:
            return _ADB_BIN_CACHE, devices

    candidates = adb_candidates()
    if not candidates and ADB_BIN:
        candidates = [resolve_command(ADB_BIN, "ADB")]

    last_details = []
    for candidate in candidates:
        try:
            result = run_cmd([candidate, "devices"], timeout=20)
            devices, unauthorized, offline = parse_adb_devices(result.stdout)
            last_details.append({
                "adb": candidate,
                "devices": devices,
                "unauthorized": unauthorized,
                "offline": offline,
                "stderr": (result.stderr or "").strip()
            })
            if configured or devices or not require_devices:
                _ADB_BIN_CACHE = candidate
                return candidate, devices
        except Exception as e:
            last_details.append({"adb": candidate, "error": str(e)})

    details = "; ".join([
        f"{item.get('adb')}: devices={item.get('devices', [])}, unauthorized={item.get('unauthorized', [])}, offline={item.get('offline', [])}, error={item.get('error') or item.get('stderr') or ''}"
        for item in last_details
    ])
    raise RuntimeError("未检测到可用 Android 设备。已扫描 adb：" + (details or "无"))


def detect_device_ids():
    configured = os.getenv("DEVICE_ID") or os.getenv("ANDROID_DEVICE_ID")
    if configured:
        return [configured]

    _, devices = resolve_adb_with_devices(require_devices=True)
    if not devices:
        raise RuntimeError("未检测到可用 Android 设备，请先确认 adb devices 能看到 device 状态")
    return devices


def ios_info(device_id, key, domain=None):
    args = ["ideviceinfo", "-u", device_id]
    if domain:
        args += ["-q", domain]
    return subprocess.check_output(args + ["-k", key], text=True, timeout=5, stderr=subprocess.DEVNULL).strip()


def ios_battery_level(device_id):
    try:
        level = int(ios_info(device_id, "BatteryCurrentCapacity", "com.apple.mobile.battery"))
        return level if 0 <= level <= 100 else None
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def ios_connected_device_ids():
    return subprocess.check_output(["idevice_id", "-l"], text=True, timeout=5).splitlines()


def ios_marketing_name(device_id):
    try:
        with tempfile.TemporaryDirectory(prefix="midscene-ios-info-") as directory:
            output = Path(directory) / "device.json"
            subprocess.run(["xcrun", "devicectl", "device", "info", "details", "--device", device_id,
                            "--json-output", str(output)], check=True, timeout=8,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return str(json.loads(output.read_text()).get("result", {}).get("hardwareProperties", {}).get("marketingName") or "")
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


_IOS_METADATA_CACHE = {}


def ios_app_mappings():
    mappings = {"com.kfb.model": "com.kfb.3d", "com.xbxxhz.box": "com.xbxxhz.box"}
    if IOS_SOURCE_APP and IOS_BUNDLE_ID:
        mappings[IOS_SOURCE_APP] = IOS_BUNDLE_ID
    return mappings


def ios_device_metadata(device_id):
    cached = _IOS_METADATA_CACHE.get(device_id)
    if cached and time.monotonic() - cached[0] < 60:
        return dict(cached[1])
    # Read-only, selected UDID only; retain a small allowlist, not the full plist.
    row = {"device_id": device_id, "status": "online", "platform": "ios",
           "brand": "Apple", "model": "iPhone", "label": "iPhone (iOS)",
           "preflight_status": "unknown"}
    try:
        raw = subprocess.check_output(["ideviceinfo", "-u", device_id, "-x"], timeout=5, stderr=subprocess.DEVNULL)
        info = plistlib.loads(raw)
        row.update(model=info.get("ProductType") or "iPhone", ios_version=info.get("ProductVersion") or "")
        row["label"] = ios_marketing_name(device_id) or info.get("DeviceName") or row["model"]
        row["display_name"] = row["label"]
    except (ValueError, OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
        pass
    row["battery_level"] = ios_battery_level(device_id)
    row["installed_apps"] = []
    for source_package, bundle in ios_app_mappings().items():
        entry = {"package": bundle, "source_package": source_package,
                 "name": {"com.kfb.model": "智小白3D", "com.xbxxhz.box": "小白学习"}.get(source_package, bundle)}
        try:
            raw = subprocess.check_output(["ideviceinstaller", "-u", device_id, "list", "-b", bundle, "--xml"], timeout=8, stderr=subprocess.DEVNULL)
            apps = plistlib.loads(raw)
            app = next((a for a in apps if a.get("CFBundleIdentifier") == bundle), None)
            entry.update(installed=app is not None,
                         version_name=(app or {}).get("CFBundleShortVersionString", ""),
                         version_code=(app or {}).get("CFBundleVersion", ""))
        except (ValueError, OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
            entry.update(installed=None, error="版本读取失败")
        row["installed_apps"].append(entry)
    _IOS_METADATA_CACHE[device_id] = (time.monotonic(), dict(row))
    return row


@contextmanager
def ios_usb_forward(device_id):
    """One owned loopback port per exact USB device, cleaned even on failure."""
    if not device_id or device_id not in ios_connected_device_ids():
        raise ValueError("指定 iPhone 不在当前 USB 连接列表中")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    proxy = subprocess.Popen(["iproxy", "-u", device_id, f"{port}:8100", "-s", "127.0.0.1"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 3
        while True:
            if proxy.poll() is not None:
                raise RuntimeError("iPhone USB 转发启动失败")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("iPhone USB 转发未就绪")
                time.sleep(0.1)
        # Recheck ownership after connect so a port race cannot reuse another WDA.
        time.sleep(0.05)
        if proxy.poll() is not None:
            raise RuntimeError("iPhone USB 转发端口被占用")
        yield port
    finally:
        if proxy.poll() is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=2)


def ios_wda_request(port, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=12) as response:
        raw = response.read(17 * 1024 * 1024 + 1)
    if len(raw) > 17 * 1024 * 1024:
        raise RuntimeError("WDA 响应超过大小限制")
    value = json.loads(raw).get("value")
    if isinstance(value, dict) and value.get("error"):
        raise RuntimeError("WDA 未能读取当前画面信息")
    return value


def ios_wda_read(device_id, path):
    if path not in ("/screenshot", "/wda/activeAppInfo", "/status"):
        raise ValueError("不支持的 WDA 读取路径")
    with ios_usb_forward(device_id) as port:
        return ios_wda_request(port, path)


_IOS_READINESS_CACHE = {}

# Recovery is opt-in and only operates on explicitly configured USB UDIDs.
IOS_WDA_AUTORECOVER = os.getenv("IOS_WDA_AUTORECOVER", "0") == "1"
IOS_WDA_PROJECT = os.getenv("IOS_WDA_PROJECT", "").strip()
IOS_WDA_DEVICE_IDS = set(filter(None, (v.strip() for v in os.getenv("IOS_WDA_DEVICE_IDS", "").split(","))))
_IOS_JOB_ACTIVE = threading.Event()
_IOS_RECOVERY_GUARD = threading.RLock()


def ios_wda_other_process(device_id):
    """Do not interrupt WDA sessions started by Sonic/Xcode/another Runner."""
    # On macOS a multi-column `comm` is truncated to 16 characters. Use the
    # executable at the beginning of args, not the truncated display name.
    rows = subprocess.check_output(["ps", "-axww", "-o", "pid=,args="], text=True, timeout=3)
    for row in rows.splitlines():
        parts = row.strip().split(None, 1)
        if len(parts) == 2 and parts[1].split() and Path(parts[1].split()[0]).name == "xcodebuild":
            # Other XCTest sessions also own the phone; accept comma-separated
            # destinations and xctestrun launches, but never partial UDID matches.
            if re.search(r"(?:^|[\s,\"'])id=" + re.escape(device_id) + r"(?=$|[\s,\"'])", parts[1]):
                return True
    return False


def ios_wda_launch_blocker(device_id):
    """Fail closed when USB/lock state cannot be established; never unlock a phone."""
    if device_id not in ios_connected_device_ids():
        return "手机 USB 已断开，连接后自动重试"
    try:
        with tempfile.TemporaryDirectory(prefix="midscene-wda-lock-") as directory:
            path = Path(directory) / "state.json"
            subprocess.run(["xcrun", "devicectl", "device", "info", "lockState", "--device", device_id,
                            "--timeout", "5", "--json-output", str(path)],
                           check=True, timeout=7, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            state = json.loads(path.read_text()).get("result", {})
        if state.get("passcodeRequired") is True:
            return "手机已锁定，请解锁并保持亮屏，随后自动重试"
        if state.get("passcodeRequired") is not False or state.get("unlockedSinceBoot") is not True:
            return "无法确认手机解锁状态，请解锁并检查 USB 信任"
        return ""
    except Exception:
        return "无法确认手机解锁状态，请解锁并检查 USB 信任"


class IOSWDARecovery:
    """Bounded, idle-only recovery. Own only processes and locks created here."""
    def __init__(self):
        self.states = {}
        self.closed = False
        self.workers = set()

    def state(self, device_id):
        return self.states.setdefault(device_id, dict(failures=0, sampled=None, attempts=0,
            next_retry=0, stable_since=None, started=0, process=None, log=None, lock=None,
            was_ready=False, launching=False, reason=""))

    def stop(self, state):
        process = state.get("process")
        if process:
            # Popen start_new_session creates this group's ownership boundary.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    process.poll()  # reap the leader, but also check descendants
                    os.killpg(process.pid, 0)
                    time.sleep(0.1)
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        for field in ("log", "lock"):
            if state.get(field):
                state[field].close()
                state[field] = None
        state["process"] = None

    def failed(self, state, now, reason):
        state["stable_since"] = None
        state["was_ready"] = False
        state["failures"] = 0
        delay = 300 if state["attempts"] >= 3 else (15 if state["attempts"] <= 1 else 45)
        state["next_retry"] = now + delay
        state["reason"] = reason + ("；连续失败，冷却 5 分钟后重试" if delay == 300 else f"；{delay} 秒后重试")
        print("WDA recovery: " + state["reason"], flush=True)

    def launch(self, device_id, now):
        if self.closed:
            return
        state = self.state(device_id)
        try:
            # Expensive prerequisites run on a background thread, not heartbeat.
            if ios_wda_other_process(device_id):
                state.update(next_retry=now + 30, reason="现有 WDA 启动进程尚未退出，等待恢复，不重复启动")
                return
            blocker = ios_wda_launch_blocker(device_id)
            if blocker:
                state.update(next_retry=now + 30, reason=blocker)
                return
            project = Path(IOS_WDA_PROJECT).expanduser()
            if not IOS_WDA_PROJECT or not project.is_absolute() or not project.is_dir():
                state.update(next_retry=now + 300, reason="WDA 恢复工程未配置或不存在，请检查 IOS_WDA_PROJECT")
                return
            with _IOS_RECOVERY_GUARD:
                if self.closed or _IOS_JOB_ACTIVE.is_set():
                    return
                directory = WORKSPACE / "wda-recovery"
                directory.mkdir(parents=True, exist_ok=True)
                safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", device_id)
                import fcntl
                lock = (directory / f"{safe_id}.lock").open("a")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock.close()
                    state.update(next_retry=now + 30, reason="其他恢复进程正在管理 WDA，等待其完成")
                    return
                state["lock"] = lock
                # Recheck after acquiring the cross-process lock.
                if ios_wda_other_process(device_id):
                    self.stop(state)
                    state.update(next_retry=now + 30, reason="现有 WDA 启动进程正在恢复，不重复启动")
                    return
                log_path = directory / f"{safe_id}.log"
                if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                    log_path.replace(log_path.with_suffix(".previous.log"))
                state["log"] = log_path.open("ab")
                state["attempts"] += 1
                state["started"] = time.monotonic()
                state["was_ready"] = False
                state["process"] = subprocess.Popen(["xcodebuild", "-project", str(project),
                    "-scheme", "WebDriverAgentRunner", "-destination", f"id={device_id}",
                    "-destination-timeout", "15", "test-without-building"],
                    stdout=state["log"], stderr=subprocess.STDOUT, start_new_session=True)
                state["reason"] = f"WDA 正在恢复（第 {state['attempts']} 次，启动最长 90 秒）"
                print(f"WDA recovery {device_id}: start pid={state['process'].pid} attempt={state['attempts']}", flush=True)
        except Exception as exc:
            self.stop(state)
            self.failed(state, time.monotonic(), "WDA 启动异常：" + type(exc).__name__)

    def schedule(self, device_id, now):
        with _IOS_RECOVERY_GUARD:
            if self.closed:
                return
            state = self.state(device_id)
            state["launching"] = True
            def worker():
                try:
                    self.launch(device_id, now)
                finally:
                    with _IOS_RECOVERY_GUARD:
                        state["launching"] = False
                        self.workers.discard(threading.current_thread())
            thread = threading.Thread(target=worker, name="ios-wda-recovery", daemon=True)
            self.workers.add(thread)
            thread.start()

    def shutdown(self):
        with _IOS_RECOVERY_GUARD:
            self.closed = True
            workers = list(self.workers)
        deadline = time.monotonic() + 10
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        # A late prerequisite worker cannot spawn after closed is set.
        for state in list(self.states.values()):
            self.stop(state)

    def prune(self, connected):
        if _IOS_JOB_ACTIVE.is_set():
            return
        for device_id in set(self.states) - set(connected):
            state = self.states[device_id]
            if state["launching"]:
                continue
            if state["process"]:
                self.stop(state)
            del self.states[device_id]
            _IOS_READINESS_CACHE.pop(device_id, None)

    def tick(self, device_id, readiness, now=None):
        if self.closed or not IOS_WDA_AUTORECOVER or device_id not in IOS_WDA_DEVICE_IDS or _IOS_JOB_ACTIVE.is_set():
            return readiness
        now = time.monotonic() if now is None else now
        state = self.state(device_id)
        if state["process"] and state["process"].poll() is not None:
            _IOS_READINESS_CACHE.pop(device_id, None)
            readiness = {"ready": False, "code": "wda_unreachable", "reason": "WDA 启动进程退出"}
        if readiness["ready"]:
            state["failures"] = 0
            state["was_ready"] = True
            state["reason"] = ""
            if state["stable_since"] is None:
                state["stable_since"] = now
                print(f"WDA recovery {device_id}: ready", flush=True)
            if now - state["stable_since"] >= 60:
                state["attempts"] = 0
                state["next_retry"] = 0
            return readiness
        state["stable_since"] = None
        if readiness.get("code") not in ("wda_unreachable", "wda_not_ready"):
            return readiness
        process = state.get("process")
        if process and process.poll() is not None:
            reason = "WDA 启动进程退出"
            try:
                state["log"].flush()
                with open(state["log"].name, "rb") as log:
                    log.seek(max(0, log.seek(0, 2) - 6000))
                    tail = log.read().decode("utf-8", errors="replace")
                if "enabling automation mode" in tail:
                    reason = "启用自动化模式超时，请解锁手机并保持亮屏"
                elif "Testing failed" in tail:
                    reason = "WDA 测试启动失败，请检查本机恢复日志"
            except Exception:
                pass
            self.stop(state)
            self.failed(state, now, reason)
        elif process and not state["was_ready"]:
            if now - state["started"] < 90:
                return {**readiness, "reason": f"WDA 正在恢复（第 {state['attempts']} 次，已等待 {int(now-state['started'])} 秒，最长 90 秒）"}
            self.stop(state)
            self.failed(state, now, "WDA 恢复超时，请检查手机解锁和开发者信任")
        if state["launching"]:
            return {**readiness, "reason": "正在检查 WDA 恢复条件"}
        if now < state["next_retry"]:
            return {**readiness, "reason": state["reason"]}
        if state["sampled"] is None or now - state["sampled"] >= 30:
            state["sampled"] = now
            state["failures"] += 1
            if state["failures"] >= 2:
                state["failures"] = 0
                if process and state["process"]:
                    self.stop(state)
                    self.failed(state, now, "WDA 再次无响应")
                    return {**readiness, "reason": state["reason"]}
                if state["attempts"] >= 3:
                    state["attempts"] = 0  # cooldown elapsed; a new bounded retry cycle
                self.schedule(device_id, now)
        return {**readiness, "reason": state["reason"] or "WDA 连接中断，正在复查，连续失败后自动恢复"}


_IOS_WDA_RECOVERY = IOSWDARecovery()


def ios_execution_readiness(device_id, force=False):
    cached = _IOS_READINESS_CACHE.get(device_id)
    if cached and not force and time.monotonic() - cached[0] < 30:
        return dict(cached[1])
    try:
        resolve_command(MIDSCENE_BIN, "Midscene")
    except Exception:
        return {"ready": False, "code": "midscene_missing", "reason": "Midscene 执行器不存在，请检查 MIDSCENE_BIN"}
    try:
        value = ios_wda_read(device_id, "/status")
        result = {"ready": isinstance(value, dict) and value.get("ready") is True}
        result["code"] = "ready" if result["ready"] else "wda_not_ready"
        result["reason"] = "" if result["ready"] else "WDA 尚未就绪，请解锁手机并启动 WDA"
    except Exception:
        result = {"ready": False, "code": "wda_unreachable", "reason": "WDA 连接不可用，请检查 USB、手机解锁及 WDA 服务"}
    _IOS_READINESS_CACHE[device_id] = (time.monotonic(), result)
    return dict(result)


def parse_battery_state(text):
    def number(name):
        match = re.search(rf"(?m)^\s*{re.escape(name)}:\s*(-?\d+)", text or "")
        return int(match.group(1)) if match else None
    level = number("level")
    temperature = number("temperature")
    return {
        "battery_level": level,
        "battery_temperature_c": round(temperature / 10.0, 1) if temperature is not None else None,
    }


def parse_screen_on(text):
    value = str(text or "").upper()
    if re.search(r"(?:STATE|SCREEN_STATE)\s*=\s*OFF", value):
        return False
    if re.search(r"(?:STATE|SCREEN_STATE)\s*=\s*ON", value):
        return True
    return None


def capture_screen_png(adb_bin, device_id, run=None, max_bytes=12 * 1024 * 1024):
    # Match the Windows Runner's bounded, explicitly targeted binary capture.
    result = (run or subprocess.run)([adb_bin, "-s", device_id, "exec-out", "screencap", "-p"],
                                    capture_output=True, timeout=15)
    content = result.stdout or b""
    if getattr(result, "returncode", 0) != 0:
        raise RuntimeError("ADB 截图失败")
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("ADB 未返回有效 PNG 截图")
    if len(content) > max_bytes:
        raise RuntimeError("手机截图超过 12MB 限制")
    return content


def capture_device_snapshot(adb_bin, device_id):
    battery = parse_battery_state(adb_shell_text(adb_bin, device_id, "dumpsys", "battery", timeout=5))
    display = adb_shell_text(adb_bin, device_id, "dumpsys", "display", timeout=5)
    activity = adb_shell_text(adb_bin, device_id, "dumpsys", "window", "windows", timeout=6)
    foreground = ""
    match = re.search(r"mCurrentFocus=.*?\s([A-Za-z0-9._]+)/(?:[A-Za-z0-9._$]+)", activity)
    if match:
        foreground = match.group(1)
    return {
        **battery,
        "screen_on": parse_screen_on(display),
        "foreground_package": foreground,
        "content_base64": base64.b64encode(capture_screen_png(adb_bin, device_id)).decode("ascii"),
    }


def capture_ios_device_snapshot(device_id):
    if not device_id or device_id not in ios_connected_device_ids():
        raise ValueError("截图设备不在当前 USB 连接列表中")
    encoded = ios_wda_read(device_id, "/screenshot")
    if not isinstance(encoded, str) or len(encoded) > 16 * 1024 * 1024:
        raise RuntimeError("WDA 未返回有效截图")
    content = base64.b64decode(encoded, validate=True)
    if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) < 24:
        raise RuntimeError("WDA 截图不是有效 PNG")
    width, height = int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    result = {"content_base64": encoded, "resolution": f"{width}x{height}",
              "battery_level": ios_battery_level(device_id)}
    try:
        app = ios_wda_read(device_id, "/wda/activeAppInfo")
        result["foreground_package"] = str(app.get("bundleId") or "")
    except Exception:
        # A successful picture remains useful if active-app metadata is unavailable.
        pass
    return result


def upload_snapshot_requests(response, devices):
    if RUNNER_PLATFORM not in ("ios", "android"):
        return
    requests = response.get("snapshot_requests", []) if isinstance(response, dict) else []
    if not isinstance(requests, list):
        return
    available = {item.get("device_id") for item in devices if item.get("status", "online") == "online"}
    seen = set()
    adb_bin = None
    for request in requests:
        if not isinstance(request, dict) or not request.get("request_id"):
            continue
        device_id = str(request.get("device_id") or "")
        if device_id in seen:
            continue
        seen.add(device_id)
        payload = {"runner_id": RUNNER_ID, "device_id": device_id, "request_id": request["request_id"]}
        try:
            if device_id not in available:
                raise RuntimeError("所选设备已离线或不属于此 Runner")
            if RUNNER_PLATFORM == "ios":
                payload.update(capture_ios_device_snapshot(device_id))
            else:
                if adb_bin is None:
                    adb_bin, _ = resolve_adb_with_devices(require_devices=False)
                payload.update(capture_device_snapshot(adb_bin, device_id))
        except Exception:
            payload["error"] = ("iOS 当前画面采集失败，请检查 USB 连接、手机解锁及 WDA 服务" if RUNNER_PLATFORM == "ios"
                                else "Android 当前画面采集失败，请检查 USB 连接、ADB 授权及手机状态")
        http_json("POST", "/api/runner/device-snapshot", payload, timeout=15)


def detect_devices():
    global _LAST_NO_DEVICE_LOG_AT
    if RUNNER_PLATFORM == "ios":
        try:
            connected = ios_connected_device_ids()
            _IOS_WDA_RECOVERY.prune(connected)
            for previous in set(_IOS_METADATA_CACHE) - set(connected):
                _IOS_METADATA_CACHE.pop(previous, None)
            rows = [ios_device_metadata(device_id) for device_id in connected]
            if IOS_EXECUTION_ENABLED:
                for row in rows:
                    state = ios_execution_readiness(row["device_id"])
                    state = _IOS_WDA_RECOVERY.tick(row["device_id"], state)
                    row.update(preflight_status="ready" if state["ready"] else "blocked", preflight=state)
            return rows
        except (OSError, subprocess.SubprocessError):
            return []
    try:
        device_ids = detect_device_ids()
    except Exception as e:
        now = time.time()
        if now - _LAST_NO_DEVICE_LOG_AT >= ADB_DEVICE_CHECK_INTERVAL:
            print(f"Device detect error: {e}")
            print("提示：如果另一个终端能识别手机，可以执行 `which adb`，然后用 `export ADB_BIN=那个路径` 指定给 runner。")
            _LAST_NO_DEVICE_LOG_AT = now
        return []

    adb_bin, _ = resolve_adb_with_devices(require_devices=False)
    devices = []
    for device_id in device_ids:
        brand = ""
        model = ""
        android_version = ""
        sdk = ""
        resolution = ""
        density = ""
        installed_apps = []
        try:
            brand = adb_shell_text(adb_bin, device_id, "getprop", "ro.product.brand", timeout=10)
            model = adb_shell_text(adb_bin, device_id, "getprop", "ro.product.model", timeout=10)
            android_version = adb_shell_text(adb_bin, device_id, "getprop", "ro.build.version.release", timeout=10)
            sdk = adb_shell_text(adb_bin, device_id, "getprop", "ro.build.version.sdk", timeout=10)
            resolution = re.sub(r"^Physical size:\s*", "", adb_shell_text(adb_bin, device_id, "wm", "size", timeout=10)).strip()
            density = re.sub(r"^Physical density:\s*", "", adb_shell_text(adb_bin, device_id, "wm", "density", timeout=10)).strip()
            installed_apps = [detect_package_info(adb_bin, device_id, pkg) for pkg in runner_app_packages()]
        except Exception:
            pass
        raw_label = " ".join([part for part in [brand, model] if part]).strip() or device_id
        display_name = device_market_name(adb_bin, device_id, brand, model) or raw_label
        preflight_ok = bool(adb_bin and device_id)
        devices.append({
            "device_id": device_id,
            "platform": "android",
            "status": "online",
            "brand": brand,
            "model": model,
            "label": display_name,
            "raw_label": raw_label,
            "display_name": display_name,
            "market_name": display_name,
            "adb_path": adb_bin,
            "android_version": android_version,
            "sdk": sdk,
            "resolution": resolution,
            "density": density,
            "installed_apps": installed_apps,
            "preflight_status": "ready" if preflight_ok else "unknown",
        })
    return devices


def is_apk_install_job(job):
    return str(job.get("job_type") or job.get("jobType") or job.get("type") or "").strip().lower() == "apk_install"


def build_download_url(url):
    url = str(url or "").strip()
    if url.startswith("/"):
        return SERVER.rstrip("/") + url
    return url


def pgyer_download_opener(url, job_id=""):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    page_req = urllib.request.Request(url, headers={"User-Agent": "Midscene-Runner/1.0"})
    with opener.open(page_req, timeout=60) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    match = re.search(r'href=["\']([^"\']*/app/build/[^"\']+)["\']', body, flags=re.I)
    if not match:
        raise RuntimeError("蒲公英页面未解析到下载入口，请上传 APK 或填写 APK 直链。")
    download_url = urllib.parse.urljoin(url, match.group(1))
    if job_id:
        post_job_progress(job_id, {
            "progress": 12,
            "current_task_name": "解析蒲公英下载页",
            "current_task_index": 0,
            "completed_task_count": 0,
            "total_task_count": 3,
            "message": "已找到蒲公英下载入口"
        })
    return opener, download_url


def download_file(url, dest_path, job_id="", source=""):
    target_url = build_download_url(url)
    if not target_url:
        raise RuntimeError("安装包下载地址为空")
    headers = {
        "User-Agent": "Midscene-Runner/1.0",
        "Accept": "application/vnd.android.package-archive,*/*",
    }
    if target_url.startswith(SERVER.rstrip("/") + "/"):
        headers["x-token"] = TOKEN
    opener = urllib.request.build_opener()
    if source == "pgyer":
        opener, target_url = pgyer_download_opener(target_url, job_id=job_id)
    req = urllib.request.Request(target_url, headers=headers)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with opener.open(req, timeout=300) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(512 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if job_id and total:
                    progress = 10 + min(35, int(downloaded * 35 / max(total, 1)))
                    post_job_progress(job_id, {
                        "progress": progress,
                        "current_task_name": "下载安装包",
                        "current_task_index": 0,
                        "completed_task_count": 0,
                        "total_task_count": 3,
                        "message": f"已下载 {downloaded // 1024 // 1024} MB"
                    })
    return dest_path


def validate_apk_file(path, source=""):
    with open(path, "rb") as f:
        head = f.read(8)
        f.seek(0)
        sample = f.read(120)
    if not head.startswith(b"PK"):
        hint = "蒲公英短链返回的不是 APK 文件，请上传 APK 或填写 APK 直链。" if source == "pgyer" else "下载内容不是 APK 文件，请检查下载地址。"
        preview = sample.decode("utf-8", errors="ignore").strip().replace("\n", " ")[:80]
        raise RuntimeError(f"{hint}{f' 返回内容：{preview}' if preview else ''}")


def install_apk_job(job):
    job_id = job["job_id"]
    started = time.time()
    job_dir = WORKSPACE / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    install_mode = str(job.get("install_mode") or job.get("installMode") or "test_validation")
    source = str(job.get("package_source") or job.get("source_type") or job.get("sourceType") or "upload")
    apk_url = job.get("apk_url") or job.get("apkUrl") or ""
    apk_name = safe_filename(job.get("apk_name") or job.get("apkName") or job.get("file") or "app.apk").replace(".yaml", ".apk").replace(".yml", ".apk")
    app_package = str(job.get("app_package") or job.get("appPackage") or "").strip()

    stdout = []
    stderr = []
    device_id = job.get("device_id") or ""
    try:
        if install_mode == "baseline_regression" and source != "production_url":
            raise RuntimeError("基线回归只能安装线上包来源，不能安装测试上传包或蒲公英包。")
        if not device_id:
            device_ids = detect_device_ids()
            device_id = device_ids[0] if device_ids else ""
        if not device_id:
            raise RuntimeError("未检测到可安装的 Android 设备")
        post_job_progress(job_id, {
            "progress": 8,
            "current_task_name": "下载安装包",
            "current_task_index": 0,
            "completed_task_count": 0,
            "total_task_count": 3,
            "device_id": device_id,
            "message": "Runner 已接收安装任务"
        })
        apk_path = job_dir / apk_name
        download_file(apk_url, apk_path, job_id=job_id, source=source)
        validate_apk_file(apk_path, source=source)
        post_job_progress(job_id, {
            "progress": 55,
            "current_task_name": "ADB 安装",
            "current_task_index": 1,
            "completed_task_count": 1,
            "total_task_count": 3,
            "device_id": device_id,
            "message": f"安装包下载完成：{apk_path.name}"
        })
        adb_bin, _ = resolve_adb_with_devices(require_devices=False)
        result = run_cmd([adb_bin, "-s", device_id, "install", "-r", "-d", str(apk_path)], timeout=600)
        stdout.append(result.stdout or "")
        stderr.append(result.stderr or "")
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "adb install 执行失败").strip())
        verify_message = ""
        if app_package:
            verify = run_cmd([adb_bin, "-s", device_id, "shell", "pm", "path", app_package], timeout=30)
            verify_message = (verify.stdout or verify.stderr or "").strip()
            stdout.append(verify.stdout or "")
            stderr.append(verify.stderr or "")
            if verify.returncode != 0 or not verify_message:
                raise RuntimeError(f"APK 已安装，但未检测到包名 {app_package}；请确认包名是否正确。")
        post_job_progress(job_id, {
            "progress": 100,
            "current_task_name": "安装完成",
            "current_task_index": 2,
            "completed_task_count": 3,
            "total_task_count": 3,
            "device_id": device_id,
            "message": verify_message or "APK 安装成功"
        })
        payload = {
            "status": "success",
            "duration": round(time.time() - started, 2),
            "device_id": device_id,
            "stdout": "\n".join(stdout)[-12000:],
            "stderr": "\n".join(stderr)[-12000:],
            "summary": {
                "job_type": "apk_install",
                "install_mode": install_mode,
                "package_source": source,
                "apk_name": apk_path.name,
                "apk_path": str(apk_path),
                "app_package": app_package,
            },
            "progress": 100,
        }
        write_text(job_dir / "final_result.json", json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    except Exception as e:
        message = str(e)
        post_job_progress(job_id, {
            "progress": 0,
            "current_task_name": "安装失败",
            "current_task_index": 0,
            "completed_task_count": 0,
            "total_task_count": 3,
            "device_id": device_id,
            "message": message
        })
        payload = {
            "status": "failed",
            "duration": round(time.time() - started, 2),
            "device_id": device_id,
            "stdout": "\n".join(stdout)[-12000:],
            "stderr": ("\n".join(stderr) + "\n" + message).strip()[-12000:],
            "summary": {
                "job_type": "apk_install",
                "install_mode": install_mode,
                "package_source": source,
                "apk_name": apk_name,
                "app_package": app_package,
            },
            "error": message,
            "progress": 0,
        }
        write_text(job_dir / "final_result.json", json.dumps(payload, ensure_ascii=False, indent=2))
        return payload


def heartbeat(devices):
    runtime = {} if RUNNER_PLATFORM == "ios" else midscene_env()
    payload = {
        "runner_id": RUNNER_ID,
        "hostname": socket.gethostname(),
        "workspace": str(WORKSPACE),
        "os": f"{platform.system()} {platform.release()}",
        "runner_version": RUNNER_VERSION,
        "version": RUNNER_VERSION,
        "capabilities": {
            **RUNNER_CAPABILITIES,
            "midscene_model_name": runtime.get("MIDSCENE_MODEL_NAME", ""),
            "midscene_model_family": runtime.get("MIDSCENE_MODEL_FAMILY", ""),
        },
        "devices": devices
    }
    return http_json("POST", "/api/runner/heartbeat", payload)


def ensure_android_device_id(yaml_text, device_id):
    lines = (yaml_text or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "android: {}":
            lines[i] = "android:"
            lines.insert(i + 1, f"  deviceId: {device_id}")
            return "\n".join(lines) + "\n"
        if line.strip() == "android:":
            j = i + 1
            while j < len(lines):
                current = lines[j]
                if current.strip() and not current.startswith("  "):
                    break
                if current.strip().startswith("deviceId:"):
                    lines[j] = f"  deviceId: {device_id}"
                    return "\n".join(lines) + "\n"
                j += 1
            lines.insert(i + 1, f"  deviceId: {device_id}")
            return "\n".join(lines) + "\n"

    return f"android:\n  deviceId: {device_id}\n\n{yaml_text or ''}"


def yaml_has_root_tasks(yaml_text):
    return bool(re.search(r"^tasks\s*:", yaml_text or "", re.M))


def yaml_has_interface_config(yaml_text):
    return bool(re.search(r"^(android|ios|web|computer|interface)\s*:", yaml_text or "", re.M))


def normalize_empty_cli_interface_config(yaml_text):
    text = yaml_text or ""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r"^(android|ios|web|computer|interface)\s*:\s*$", line)
        if not match:
            continue
        next_content = next((item for item in lines[i + 1:] if item.strip()), "")
        if not next_content.startswith((" ", "\t")):
            lines[i] = f"{match.group(1)}: {{}}"
        suffix = "\n" if text.endswith(("\n", "\r")) else ""
        return "\n".join(lines) + suffix
    return text


def ensure_cli_interface_config(yaml_text, platform="android"):
    text = (yaml_text or "").replace("\ufeff", "")
    text = normalize_empty_cli_interface_config(text)
    if yaml_has_interface_config(text):
        return text
    platform = platform if platform in ("android", "ios", "web", "computer", "interface") else "android"
    return f"{platform}: {{}}\n{text.lstrip()}"


def midscene_cli_yaml_text(yaml_text):
    """Convert server platform-root YAML to the root tasks layout Midscene CLI loads."""
    text = (yaml_text or "").replace("\ufeff", "")
    if yaml_has_root_tasks(text):
        return ensure_cli_interface_config(text)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        platform_match = re.match(r"^(android|ios)\s*:\s*$", line.strip())
        if not platform_match:
            continue
        end = len(lines)
        for probe in range(index + 1, len(lines)):
            if lines[probe].strip() and not lines[probe].startswith((" ", "\t")):
                end = probe
                break
        for task_index in range(index + 1, end):
            task_line = lines[task_index]
            task_match = re.match(r"^(\s*)tasks\s*:\s*$", task_line)
            if not task_match:
                continue
            indent = task_match.group(1)
            converted = []
            for child in lines[task_index:end]:
                if indent and child.startswith(indent):
                    converted.append(child[len(indent):])
                else:
                    converted.append(child)
            suffix = lines[end:]
            result = "\n".join([f"{platform_match.group(1)}: {{}}"] + converted + suffix).rstrip()
            return result + "\n" if result else text
    return text


def parse_yaml_task_names(yaml_text):
    names = []
    for line in (yaml_text or "").splitlines():
        m = re.match(r"^\s*-\s+name:\s*(.+?)\s*$", line)
        if not m:
            continue
        name = m.group(1).strip()
        if (name.startswith('"') and name.endswith('"')) or (name.startswith("'") and name.endswith("'")):
            name = name[1:-1]
        if name:
            names.append(name)
    return names


def parse_app_package(yaml_text):
    for line in (yaml_text or "").splitlines():
        match = re.match(r"^\s*-\s+(?:launch|terminate)\s*:\s*[\"']?([^\"'\s#]+)", line)
        if match and "." in match.group(1):
            return match.group(1).strip()
    return os.getenv("APP_PACKAGE", "").strip()


def is_yaml_dry_run_job(job):
    job_type = str(job.get("job_type") or job.get("type") or "").strip().lower()
    run_mode = str(job.get("run_mode") or "").strip().lower()
    return job_type == "yaml_dry_run" or run_mode == "yaml_dry_run" or bool(job.get("dry_run"))


def dry_run_yaml_issues(yaml_text):
    text = yaml_text or ""
    issues = []
    task_names = parse_yaml_task_names(text)
    if not text.strip():
        issues.append("YAML 内容为空")
    if not yaml_has_interface_config(text):
        issues.append('缺少 Midscene CLI 接口配置 android/web/ios/computer/interface')
    if not yaml_has_root_tasks(text):
        issues.append('缺少 Midscene CLI 可加载的顶层 tasks')
    if not task_names:
        issues.append("未解析到 tasks.name")
    if not re.search(r"^\s*flow\s*:\s*$", text, re.M):
        issues.append("未解析到 flow")
    if not re.search(r"^\s*-\s+(aiTap|aiWaitFor|aiAssert|launch|terminate|sleep|runAdbShell)\s*:", text, re.M):
        issues.append("flow 中未解析到可执行 Midscene 动作")
    return issues


def run_yaml_dry_run_job(job):
    job_id = job["job_id"]
    started = time.time()
    job_dir = WORKSPACE / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    yaml_name = safe_filename(job.get("file") or "dry-run.yaml")
    yaml_text = midscene_cli_yaml_text(job.get("yaml_content", "") or "")
    yaml_path = job_dir / yaml_name
    write_text(yaml_path, yaml_text)
    task_names = parse_yaml_task_names(yaml_text)
    app_package = parse_app_package(yaml_text)
    issues = dry_run_yaml_issues(yaml_text)
    post_job_progress(job_id, {
        "progress": 100 if not issues else 20,
        "current_task_name": task_names[0] if task_names else "",
        "current_task_index": 0,
        "completed_task_count": len(task_names) if not issues else 0,
        "total_task_count": len(task_names),
        "message": "YAML dry-run 通过" if not issues else "YAML dry-run 未通过",
    })
    summary = {
        "dry_run": True,
        "mode": "runner_yaml_dry_run",
        "task_count": len(task_names),
        "task_names": task_names[:50],
        "app_package": app_package,
        "issues": issues,
        "yaml_path": str(yaml_path),
    }
    stdout = f"YAML dry-run checked {len(task_names)} task(s); app={app_package or '-'}"
    stderr = "\n".join(issues)
    return {
        "status": "passed" if not issues else "failed",
        "duration": round(time.time() - started, 2),
        "device_id": job.get("device_id") or "",
        "attempts": [],
        "stdout": stdout,
        "stderr": stderr,
        "summary": summary,
        "screenshots": [],
        "report_url": "",
        "local_report_path": "",
        "report_upload_error": "",
        "report_missing_reason": "YAML dry-run 不生成 HTML 报告",
        "report_upload_pending": False,
    }


def inject_external_page_escape(yaml_text):
    result = []
    lines = (yaml_text or "").splitlines()
    for line in lines:
        if re.match(r"^\s*-\s+terminate\s*:", line):
            indent = re.match(r"^(\s*)", line).group(1)
            recent = "\n".join(result[-6:])
            if "runAdbShell:" not in recent:
                result.extend([
                    f'{indent}- runAdbShell: "input keyevent 3"',
                    f"{indent}- sleep: 500",
                ])
        result.append(line)
    return "\n".join(result) + "\n"


def current_foreground_package(adb_bin, device_id):
    patterns = [
        r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/(?:[A-Za-z0-9_.$]+)",
        r"mFocusedApp=.*?\s([A-Za-z0-9_.]+)/(?:[A-Za-z0-9_.$]+)",
        r"topResumedActivity=.*?\s([A-Za-z0-9_.]+)/(?:[A-Za-z0-9_.$]+)",
    ]
    for args in (
        ["shell", "dumpsys", "window"],
        ["shell", "dumpsys", "activity", "activities"],
    ):
        try:
            out = run_cmd([adb_bin, "-s", device_id] + args, timeout=10).stdout or ""
            for pattern in patterns:
                match = re.search(pattern, out)
                if match:
                    pkg = match.group(1).strip()
                    if "." in pkg:
                        return pkg
        except Exception:
            continue
    return ""


def should_force_stop_foreground(pkg, app_package):
    if not pkg or pkg == app_package:
        return False
    protected = ("com.android.systemui", "com.huawei.android.launcher", "com.android.launcher")
    return not any(pkg.startswith(item) for item in protected)


def reset_foreground_app(adb_bin, device_id, app_package):
    if not app_package:
        return
    width, height = 1080, 2400
    try:
        size = run_cmd([adb_bin, "-s", device_id, "shell", "wm", "size"], timeout=10).stdout or ""
        match = re.search(r"(\d+)x(\d+)", size)
        if match:
            width, height = int(match.group(1)), int(match.group(2))
    except Exception as e:
        print(f"ADB reset warning: wm size -> {e}")
    x = max(1, width // 2)
    y1 = max(1, int(height * 0.82))
    y2 = max(1, int(height * 0.18))
    foreground_pkg = current_foreground_package(adb_bin, device_id)
    commands = [
        ["shell", "input", "keyevent", "3"],
        ["shell", "input", "keyevent", "187"],
        ["shell", "input", "swipe", str(x), str(y1), str(x), str(y2), "300"],
        ["shell", "input", "swipe", str(x), str(y1), str(x), str(y2), "300"],
        ["shell", "input", "swipe", str(x), str(y1), str(x), str(y2), "300"],
        ["shell", "input", "keyevent", "3"],
        ["shell", "am", "kill-all"],
    ]
    if should_force_stop_foreground(foreground_pkg, app_package):
        commands.insert(0, ["shell", "am", "force-stop", foreground_pkg])
    commands.extend([
        ["shell", "am", "force-stop", app_package],
        ["shell", "monkey", "-p", app_package, "-c", "android.intent.category.LAUNCHER", "1"],
    ])
    for args in commands:
        try:
            run_cmd([adb_bin, "-s", device_id] + args, timeout=20)
            time.sleep(0.5)
        except Exception as e:
            print(f"ADB reset warning: {' '.join(args)} -> {e}")
    time.sleep(2)


def latest_file(root, pattern):
    files = list(root.rglob(pattern))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def read_text(path, limit=None):
    if not path or not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if limit and len(text) > limit:
        return text[-limit:]
    return text


def read_json(path):
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def collect_screenshots(root, limit=4, max_bytes=1500 * 1024):
    if not root or not root.exists():
        return []
    candidates = []
    for pattern in ("*.png", "*.jpg", "*.jpeg"):
        candidates.extend(root.rglob(pattern))
    candidates = [
        path for path in candidates
        if path.is_file() and path.stat().st_size > 0 and path.stat().st_size <= max_bytes
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    assets = []
    for path in candidates[:limit]:
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        try:
            assets.append({
                "name": path.name,
                "mime": mime,
                "contentBase64": base64.b64encode(path.read_bytes()).decode("ascii"),
                "local_path": str(path)
            })
        except Exception:
            pass
    return assets


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8", errors="ignore")


def _wait_for_owned_group_exit(proc, timeout):
    deadline = time.monotonic() + timeout
    while True:
        parent_exited = proc.poll() is not None  # Reap the child independently of the group probe.
        group_absent = False
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            group_absent = True
        except PermissionError:
            # macOS can briefly return EPERM while a killed group is being reaped.
            # It is not proof either way; boundedly recheck both conditions.
            pass
        if group_absent and parent_exited:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def stop_midscene_process(proc):
    owned_group = RUNNER_PLATFORM == "ios" or getattr(proc, "_ui_process_group", False) is True
    if owned_group:
        if getattr(proc, "_ui_group_stop_confirmed", False) is True:
            return True
        # A descendant can outlive an already-exited parent. Probe/escalate the
        # private group independently, and acknowledge only its disappearance.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass  # No expanded kill scope; the bounded proof below still has to succeed.
            if _wait_for_owned_group_exit(proc, 3):
                proc._ui_group_stop_confirmed = True
                return True
        return False
    if proc.poll() is not None:
        return True
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)
    except ProcessLookupError:
        proc.wait(timeout=3)
    return proc.poll() is not None


# Kept inline so updating the standalone Mac Runner remains a one-file install.
# Read-only adapter for the public ScriptPlayer status exposed by Midscene 1.13.
RUNNER_PROGRESS_OBSERVER = r'''
(() => {
  try {
    const paths = [process.argv[1]];
    const version = require(require.resolve('@midscene/cli/package.json', {paths})).version;
    if (!/^1\.13\./.test(version)) return;
    const { ScriptPlayer } = require(require.resolve('@midscene/core/yaml', {paths}));
    const original = ScriptPlayer?.prototype?.run;
    if (typeof original !== 'function') return;
    const actionNames = {launch:'启动应用',terminate:'关闭应用',aiTap:'点击',aiWaitFor:'等待',aiAssert:'检查',ai:'操作',aiAction:'操作',aiInput:'输入内容',aiScroll:'滚动',sleep:'等待',runAdbShell:'设备操作',aiQuery:'提取信息'};
    const label = step => {
      const key = Object.keys(step || {}).find(k => k in actionNames) || Object.keys(step || {})[0] || '操作';
      const value = step?.[key];
      const detail = ['aiInput','runAdbShell'].includes(key) ? '' : typeof value === 'string' ? value.replace(/\s+/g,' ').slice(0,120) : '';
      return `${actionNames[key] || key}${detail ? ` · ${detail}` : ''}`;
    };
    ScriptPlayer.prototype.run = function(...args) {
      let previous = '';
      const emit = () => {
        try {
          if (!Array.isArray(this.taskStatusList)) return;
          let budget = 1000;
          let truncated = this.taskStatusList.length > 100;
          const tasks = this.taskStatusList.slice(0,100).map(t => {
            const flow = Array.isArray(t.flow) ? t.flow : [];
            const limit = Math.min(200,budget);
            const steps = flow.slice(0,limit).map(s => ({label:label(s)}));
            budget -= steps.length;
            truncated ||= steps.length < flow.length;
            return {name:String(t.name || '未命名用例').slice(0,160),status:({init:'pending',running:'running',done:'passed',error:'failed'})[t.status] || 'pending',current_step:Number.isInteger(t.currentStep) ? t.currentStep : null,total_steps:flow.length,steps};
          });
          const value = JSON.stringify({version:1,tasks,truncated});
          if (value !== previous) {
            previous = value;
            process.stdout.write(`MIDSCENE_PLATFORM_PROGRESS ${value}\n`);
          }
        } catch (_) { /* Progress must never affect device execution. */ }
      };
      emit();
      const timer = setInterval(emit,500);
      const finish = () => { clearInterval(timer); emit(); };
      try { return Promise.resolve(original.apply(this,args)).finally(finish); }
      catch(error) { finish(); throw error; }
    };
  } catch (_) { /* Unsupported installation retains legacy progress. */ }
})();
'''


class RunnerStepProgress:
    def __init__(self):
        self.snapshot = None

    def consume(self, line):
        prefix = "MIDSCENE_PLATFORM_PROGRESS "
        if not line.startswith(prefix) or len(line) > 500_000:
            return False
        try:
            snapshot = json.loads(line[len(prefix):])
            if snapshot.get("version") != 1 or not isinstance(snapshot.get("tasks"), list):
                return False
        except (ValueError, AttributeError):
            return False
        self.snapshot = snapshot
        return True


def progress_observer_env(job_dir, device_id):
    env = midscene_env(device_id)
    try:
        path = (job_dir / "runner-progress-observer.cjs").resolve()
        path.write_text(RUNNER_PROGRESS_OBSERVER, encoding="utf-8")
        # NODE_OPTIONS is scoped to this CLI subprocess, not the machine.
        option = '--require ' + json.dumps(str(path), ensure_ascii=False)
        env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " " + option).strip()
    except OSError as error:
        print(f"Step progress unavailable: {error}")
    return env


def execute_midscene(job_id, job_dir, yaml_path, task_names, device_id):
    started = time.time()
    stdout_lines = []
    stderr = ""
    total = len(task_names)
    completed = 0
    current_index = 0
    current_task = task_names[0] if task_names else ""
    last_progress_at = 0
    step_progress = RunnerStepProgress()
    cancelled = False
    proc = None

    def emit_progress(force=False, message="执行中"):
        nonlocal last_progress_at, cancelled
        now = time.time()
        if not force and now - last_progress_at < 2:
            return
        last_progress_at = now
        base = 5
        progress = base
        if total:
            progress = min(95, max(base, round((completed / total) * 90) + base))
        acknowledgement = post_job_progress(job_id, {
            "progress": progress,
            "current_task_name": current_task,
            "current_task_index": current_index,
            "completed_task_count": completed,
            "total_task_count": total,
            "device_id": device_id,
            "message": message,
            "stdout_tail": "".join(stdout_lines)[-2000:],
            **({"execution_progress": step_progress.snapshot} if step_progress.snapshot else {})
        })
        if (RUNNER_PLATFORM == "ios" or job_id in _UI_DELIVERIES) and acknowledgement and acknowledgement.get("ignored") == "terminal_job":
            cancelled = True

    try:
        midscene_bin = resolve_command(MIDSCENE_BIN, "Midscene")
        emit_progress(True, "Midscene 已启动")
        if cancelled:
            return {"status":"cancelled", "stdout":"", "stderr":"平台已取消执行", "returncode":-1, "duration":0}
        midscene_command = execution_command(midscene_bin, yaml_path, device_id)
        proc = subprocess.Popen(
            midscene_command,
            cwd=str(job_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=RUNNER_PLATFORM == "ios" or job_id in _UI_DELIVERIES,
            env=progress_observer_env(job_dir, device_id)
        )
        if job_id in _UI_DELIVERIES:
            proc._ui_process_group = True
            _UI_DELIVERIES[job_id]["stopped"] = False
        line_queue = queue.Queue()

        def read_stdout():
            try:
                if not proc.stdout:
                    return
                for item in iter(proc.stdout.readline, ""):
                    line_queue.put(item)
            except Exception as reader_error:
                line_queue.put(f"Runner stdout reader error: {reader_error}\n")

        reader = threading.Thread(target=read_stdout, name=f"midscene-stdout-{job_id}", daemon=True)
        reader.start()
        while True:
            if cancelled:
                stop_midscene_process(proc)
                stderr = "平台任务已结束，已停止本机执行进程"
                break
            line = ""
            try:
                line = line_queue.get(timeout=2)
            except queue.Empty:
                line = ""
            if line:
                if step_progress.consume(line):
                    tasks = step_progress.snapshot["tasks"]
                    completed = sum(t.get("status") == "passed" for t in tasks)
                    active = next(((i,t) for i,t in enumerate(tasks) if t.get("status") == "running"), None)
                    if active is None:
                        active = next(((i,t) for i,t in enumerate(tasks) if t.get("status") == "failed"), None)
                    if active:
                        current_index, task = active
                        current_task = task.get("name", "")
                    emit_progress(False, "执行步骤已更新")
                else:
                    stdout_lines.append(line)
                    if line.strip():
                        emit_progress(False, line.strip()[:160])
            if proc.poll() is not None:
                reader.join(timeout=2)
                while True:
                    try:
                        rest_line = line_queue.get_nowait()
                    except queue.Empty:
                        break
                    if rest_line and not step_progress.consume(rest_line):
                        stdout_lines.append(rest_line)
                break
            if time.time() - started > TIMEOUT_SECONDS:
                stop_midscene_process(proc)
                stderr = f"\nTimeout after {TIMEOUT_SECONDS}s"
                emit_progress(True, stderr.strip())
                break
            emit_progress(False, f"执行中，已运行 {round(time.time() - started)}s")

        returncode = proc.returncode if proc.returncode is not None else -1
        stdout = "".join(stdout_lines)
        if returncode == 0:
            completed = total
        emit_progress(True, "执行完成" if returncode == 0 else "执行失败")
        return {
            "status": "cancelled" if cancelled else ("passed" if returncode == 0 else "failed"),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
            "duration": round(time.time() - started, 2)
        }
    except Exception as e:
        return {
            "status": "failed",
            "stdout": "".join(stdout_lines),
            "stderr": str(e),
            "returncode": -1,
            "duration": round(time.time() - started, 2)
        }
    finally:
        try:
            if job_id in _UI_DELIVERIES:
                _UI_DELIVERIES[job_id]["stopped"] = proc is None or stop_midscene_process(proc) is True
            elif proc is not None and proc.poll() is None:
                stop_midscene_process(proc)
        except Exception:
            if job_id in _UI_DELIVERIES:
                _UI_DELIVERIES[job_id]["stopped"] = False
            else:
                raise


def execution_command(midscene_bin, yaml_path, device_id):
    command = [midscene_bin, str(yaml_path)]
    if device_id and RUNNER_PLATFORM != "ios":
        command.extend(["--android.deviceId", device_id])
    return command


def reset_ios_foreground_app(port, bundle_id, allowed_bundle_id=None):
    """Close only the mapped app, matching Android's existing cold-start guard."""
    if bundle_id != allowed_bundle_id and bundle_id not in ios_app_mappings().values():
        raise ValueError("应用没有配置 iOS 映射")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                     headers={"Content-Type": "application/json"}, method=method)
        with opener.open(req, timeout=15) as response:
            result = json.loads(response.read())
        if isinstance(result.get("value"), dict) and result["value"].get("error"):
            raise RuntimeError("WDA 应用启动前清理失败")
        return result
    session = request("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
    session_id = session.get("sessionId") or (session.get("value") or {}).get("sessionId")
    if not session_id:
        raise RuntimeError("WDA 未建立应用清理会话")
    session_path = "/session/" + urllib.parse.quote(str(session_id), safe="")
    try:
        request("POST", session_path + "/wda/apps/terminate", {"bundleId": bundle_id})
    finally:
        request("DELETE", session_path)


def run_job(job):
    initialize_ui_delivery(job)
    if RUNNER_PLATFORM != "ios":
        return run_standard_job(job)
    device_id = str(job.get("device_id") or "")
    try:
        if not IOS_EXECUTION_ENABLED or not ios_job_matches(job, RUNNER_ID, device_id):
            raise ValueError("iOS 任务必须明确选择当前 Runner 和手机，并启用执行")
        if is_apk_install_job(job) or is_yaml_dry_run_job(job):
            raise ValueError("iOS 不支持 APK 安装或 Android dry-run")
        state = ios_execution_readiness(device_id, force=True)
        if not state["ready"]:
            raise RuntimeError(state["reason"])
        import yaml
        source = midscene_cli_yaml_text(job.get("yaml_content", ""))
        data = yaml.safe_load(source)
        launches = [step["launch"] for task in (data or {}).get("tasks", []) for step in task.get("flow", []) if "launch" in step]
        source_app, bundle_id = ios_job_mapping(job, launches)
        job_dir = WORKSPACE / job["job_id"]
        write_text(job_dir / "source.yaml", source)
        with ios_usb_forward(device_id) as port:
            # Validate again on the exact forward used by this task.
            if ios_wda_request(port, "/status").get("ready") is not True:
                raise RuntimeError("所选 iPhone WDA 尚未就绪")
            compiled = prepare_ios_yaml(source, source_app=source_app, bundle_id=bundle_id, wda_port=port)
            write_text(job_dir / "reuse-manifest.json", json.dumps({
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "device_id": device_id,
                "platform": "ios", "bundle_id": bundle_id, "source_file": job.get("file")}, ensure_ascii=False))
            reset_ios_foreground_app(port, bundle_id, allowed_bundle_id=bundle_id if job.get("ui_execution_contract") == "ui-v1" else None)
            return run_standard_job({**job, "yaml_content": compiled})
    except Exception as exc:
        return {"status": "failed", "device_id": device_id, "duration": 0, "stdout": "",
                "stderr": f"iOS 执行前检查失败：{exc}", "attempts": []}


def run_standard_job(job):
    if is_apk_install_job(job):
        return install_apk_job(job)
    if is_yaml_dry_run_job(job):
        return run_yaml_dry_run_job(job)

    job_id = job["job_id"]
    job_dir = WORKSPACE / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    yaml_name = safe_filename(job.get("file", "task.yaml"))
    original_yaml = job.get("yaml_content", "")
    target_device = job.get("device_id") or ""
    device_ids = [target_device] if target_device else detect_device_ids()

    attempts = []
    final = None
    final_yaml_path = None

    for index, device_id in enumerate(device_ids, start=1):
        attempt_dir = job_dir / f"attempt-{index}-{safe_filename(device_id)}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = attempt_dir / yaml_name
        yaml_content = original_yaml if RUNNER_PLATFORM == "ios" or job.get("ui_batch_id") else inject_external_page_escape(midscene_cli_yaml_text(original_yaml))
        yaml_path.write_text(yaml_content, encoding="utf-8")
        task_names = [job.get("target_task_name")] if job.get("target_task_name") else parse_yaml_task_names(yaml_content)
        app_package = parse_app_package(yaml_content)

        target_task = job.get("target_task_name") or ""
        print(f"Trying device {device_id} for {job_id}{f' task={target_task}' if target_task else ''}")
        try:
            if RUNNER_PLATFORM != "ios":
                adb_bin, _ = resolve_adb_with_devices(require_devices=False)
                reset_foreground_app(adb_bin, device_id, app_package)
                if app_package:
                    print(f"Reset foreground app: {app_package}")
        except Exception as e:
            print(f"Pre-run app reset warning: {e}")
        post_job_progress(job_id, {
            "progress": 3,
            "current_task_name": task_names[0] if task_names else "",
            "current_task_index": 0,
            "completed_task_count": 0,
            "total_task_count": len(task_names),
            "device_id": device_id,
            "message": "准备执行"
        })
        result = execute_midscene(job_id, attempt_dir, yaml_path, task_names, device_id)
        write_text(attempt_dir / "stdout.log", result["stdout"])
        write_text(attempt_dir / "stderr.log", result["stderr"])
        write_text(attempt_dir / "result.json", json.dumps({
            "job_id": job_id,
            "module": job.get("module"),
            "file": job.get("file"),
            "target_task_name": target_task,
            "device_id": device_id,
            "status": result["status"],
            "returncode": result["returncode"],
            "duration": result["duration"]
        }, ensure_ascii=False, indent=2))

        attempt_report = latest_file(attempt_dir, "*.html")
        attempt_summary = latest_file(attempt_dir, "summary-*.json") or latest_file(attempt_dir, "summary*.json")
        attempt_screenshots = collect_screenshots(attempt_dir, limit=3)
        if attempt_report:
            write_text(attempt_dir / "report_path.txt", str(attempt_report))
        if attempt_summary:
            write_text(attempt_dir / "summary_path.txt", str(attempt_summary))

        attempts.append({
            "device_id": device_id,
            "status": result["status"],
            "returncode": result["returncode"],
            "duration": result["duration"],
            "attempt_dir": str(attempt_dir),
            "report_path": str(attempt_report) if attempt_report else "",
            "summary_path": str(attempt_summary) if attempt_summary else "",
            "screenshots": [
                {k: v for k, v in item.items() if k != "contentBase64"}
                for item in attempt_screenshots
            ],
            "stdout_tail": result["stdout"][-2000:],
            "stderr_tail": result["stderr"][-2000:]
        })

        final = result
        final["device_id"] = device_id
        final_yaml_path = yaml_path
        if result["status"] == "passed":
            break

    if final is None:
        final = {
            "status": "failed",
            "stdout": "",
            "stderr": "没有可尝试的设备",
            "duration": 0,
            "device_id": ""
        }

    report_root = final_yaml_path.parent if final_yaml_path else job_dir
    report_path = latest_file(report_root, "*.html")
    summary_path = latest_file(report_root, "summary-*.json") or latest_file(report_root, "summary*.json")
    summary = read_json(summary_path)
    screenshots = collect_screenshots(report_root, limit=2, max_bytes=750 * 1024)
    report_url = ""
    report_upload_error = ""
    report_missing_reason = ""
    report_name = ""
    report_uploaded = False
    if report_path:
        report_name = f"{safe_filename(job.get('file', 'task')).replace('.yaml', '').replace('.yml', '')}-{job_id}.html"
        report_url = "" if job.get("ui_batch_id") else SERVER.rstrip("/") + "/reports/" + urllib.parse.quote(report_name)
        print(f"Report reserved for background upload: {report_url}")
        uploaded_report_url, immediate_upload_error = upload_report_before_result(job_id, report_path, report_name)
        if uploaded_report_url:
            report_url = uploaded_report_url
            report_uploaded = True
        elif immediate_upload_error:
            report_upload_error = immediate_upload_error
    else:
        report_missing_reason = "Midscene 未生成 HTML 报告，通常是启动前失败、命令异常退出，或报告目录未写入"
        print(f"Report not found: {report_missing_reason}")

    payload = {
        "status": final["status"],
        "duration": final["duration"],
        "device_id": final.get("device_id", ""),
        "attempts": attempts,
        "stdout": final["stdout"][-12000:],
        "stderr": final["stderr"][-12000:],
        "summary": summary,
        "screenshots": screenshots,
        "report_url": report_url,
        "local_report_path": str(report_path) if report_path else "",
        "report_upload_error": report_upload_error,
        "report_missing_reason": report_missing_reason,
        "report_upload_pending": bool(report_path and not report_uploaded),
        "_pending_report_path": str(report_path) if report_path else "",
        "_pending_report_name": report_name,
    }
    write_text(job_dir / "attempts.json", json.dumps(attempts, ensure_ascii=False, indent=2))
    write_text(job_dir / "final_result.json", json.dumps({
        "job_id": job_id,
        "status": payload["status"],
        "device_id": payload.get("device_id", ""),
        "duration": payload.get("duration", 0),
        "attempts": attempts,
        "report_path": str(report_path) if report_path else "",
        "report_url": report_url,
        "summary_path": str(summary_path) if summary_path else ""
    }, ensure_ascii=False, indent=2))

    print(f"Job artifacts: {job_dir}")
    if report_path:
        print(f"Report: {report_path}")
    if summary_path:
        print(f"Summary: {summary_path}")
    return payload


def print_startup():
    print("MidScene macOS Runner started")
    print(f"Server: {SERVER}")
    print(f"Runner: {RUNNER_ID}")
    print(f"Runner version: {RUNNER_VERSION}")
    print(f"Workspace: {WORKSPACE}")
    print(f"MIDSCENE_BIN: {MIDSCENE_BIN}")
    print(f"ADB_BIN: {ADB_BIN}")
    env_preview = midscene_env()
    print(f"模型配置来源: {'Task 服务端' if _RUNTIME_ENV_CACHE else '本机环境'}{f'（服务端读取失败：{_RUNTIME_ENV_ERROR}）' if _RUNTIME_ENV_ERROR else ''}")
    print(f"MIDSCENE_MODEL_NAME: {env_preview.get('MIDSCENE_MODEL_NAME', '') or '未配置'}")
    print(f"MIDSCENE_MODEL_FAMILY: {env_preview.get('MIDSCENE_MODEL_FAMILY', '') or '未配置'}")
    print(f"MIDSCENE_REPLANNING_CYCLE_LIMIT: {env_preview.get('MIDSCENE_REPLANNING_CYCLE_LIMIT', '') or '未配置'}")
    print(f"OPENAI_BASE_URL: {env_preview.get('OPENAI_BASE_URL', '') or '未配置'}")
    print(f"OPENAI_API_KEY: {'已配置' if env_preview.get('OPENAI_API_KEY') else '未配置'}")
    try:
        print(f"Resolved midscene: {resolve_command(MIDSCENE_BIN, 'Midscene')}")
        candidates = adb_candidates()
        print(f"ADB candidates: {', '.join(candidates) if candidates else '未找到'}")
        adb_bin, devices = resolve_adb_with_devices(require_devices=False)
        print(f"Resolved adb: {adb_bin}")
        if devices:
            print(f"Detected devices: {', '.join(devices)}")
    except Exception as e:
        print(f"Command resolve error: {e}")


def run_ios_device_monitor():
    """Device preview only until the iOS execution adapter passes acceptance."""
    validate_runner_config()
    print("iOS device preview started; task execution is disabled")
    while True:
        try:
            devices = detect_devices()
            response = heartbeat(devices)
            upload_snapshot_requests(response, devices)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print("iOS device preview update failed: " + type(exc).__name__)
        time.sleep(max(POLL_INTERVAL, 5))


def _run_main():
    if RUNNER_PLATFORM == "ios" and not IOS_EXECUTION_ENABLED:
        return run_ios_device_monitor()
    validate_runner_config()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if RUNNER_PLATFORM != "ios":
        print_startup()

    while True:
        try:
            devices = detect_devices()
            if not devices and RUNNER_PLATFORM != "ios":
                time.sleep(ADB_DEVICE_CHECK_INTERVAL)
                continue
            try:
                acknowledgement = heartbeat(devices)
                upload_snapshot_requests(acknowledgement, devices)
                if RUNNER_PLATFORM == "ios" and acknowledgement.get("dispatch_policy") != "explicit-target-v1":
                    raise RuntimeError("平台需部署明确设备调度支持后才能执行 iOS")
                replay_pending_result_callbacks()
            except Exception as e:
                print(f"Heartbeat error: {e}")
                diagnose_server_error(e)
                time.sleep(POLL_INTERVAL)
                continue

            if not devices:
                time.sleep(ADB_DEVICE_CHECK_INTERVAL)
                continue
            qs = urllib.parse.urlencode({
                "runner_id": RUNNER_ID,
                "devices": ",".join([dev["device_id"] for dev in devices])
            })
            resp = http_json("GET", f"/api/runner/jobs/next?{qs}")
            job = resp.get("job")
            if not job:
                time.sleep(POLL_INTERVAL)
                continue

            print(f"Running {job['job_id']} {job.get('module')}/{job.get('file')}")
            stopped = threading.Event()
            def keep_alive():
                while not stopped.wait(10):
                    try:
                        heartbeat(devices)
                    except Exception:
                        pass
            if RUNNER_PLATFORM == "ios" or job.get("ui_batch_id"):
                threading.Thread(target=keep_alive, daemon=True).start()
            try:
                with _IOS_RECOVERY_GUARD:
                    _IOS_JOB_ACTIVE.set()
                result = run_job(job)
            finally:
                _IOS_JOB_ACTIVE.clear()
                stopped.set()
            print(f"Finished {job['job_id']} status={result['status']}")
            pending_report_path = result.pop("_pending_report_path", "")
            pending_report_name = result.pop("_pending_report_name", "")
            try:
                post_job_result(job["job_id"], result)
            except Exception:
                queue_failed_result_callback(job["job_id"], result, pending_report_path, pending_report_name)
                raise
            if result.get("report_upload_pending"):
                enqueue_report_upload(job["job_id"], pending_report_path, pending_report_name)
        except urllib.error.HTTPError as e:
            print(f"HTTP error: {e.code} {http_error_text(e)}")
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\nRunner stopped")
            break
        except Exception as e:
            print(f"Runner error: {e}")
            diagnose_server_error(e)
            time.sleep(POLL_INTERVAL)


def main():
    previous_term = None
    if RUNNER_PLATFORM == "ios" and threading.current_thread() is threading.main_thread():
        def stop_on_term(signum, frame):
            raise KeyboardInterrupt
        previous_term = signal.signal(signal.SIGTERM, stop_on_term)
    try:
        _run_main()
    except KeyboardInterrupt:
        print("\nRunner stopped")
    finally:
        if RUNNER_PLATFORM == "ios":
            _IOS_WDA_RECOVERY.shutdown()
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    main()
