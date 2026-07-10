#!/usr/bin/env python3
import base64
import json
import http.cookiejar
import os
import platform
import queue
import re
import shutil
import socket
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


def midscene_env(device_id=""):
    env = os.environ.copy()
    runtime = task_runtime_env()
    for key in (
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
        "DASHSCOPE_VL_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "MIDSCENE_MODEL_NAME",
        "MIDSCENE_USE_QWEN_VL",
        "MIDSCENE_SKIP_CONFIG_CHECK",
        "MIDSCENE_REPLANNING_CYCLE_LIMIT",
    ):
        if runtime.get(key) and not env.get(key):
            env[key] = runtime[key]
    if env.get("DASHSCOPE_API_KEY") and not env.get("OPENAI_API_KEY"):
        env["OPENAI_API_KEY"] = env["DASHSCOPE_API_KEY"]
    env.setdefault("OPENAI_BASE_URL", env.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    env.setdefault("MIDSCENE_MODEL_NAME", env.get("DASHSCOPE_VL_MODEL", "qwen3.6-plus"))
    env.setdefault("MIDSCENE_SKIP_CONFIG_CHECK", "1")
    env.setdefault("MIDSCENE_REPLANNING_CYCLE_LIMIT", "8")
    env.setdefault("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    ensure_android_sdk_env(env)
    if device_id:
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


def http_upload_report(path, filename):
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
        return http_upload_report_chunked(path, filename)


def http_upload_report_chunked(path, filename, chunk_size=256 * 1024):
    data = path.read_bytes()
    upload_id = f"{RUNNER_ID}-{int(time.time())}-{safe_filename(filename)}"
    total = max(1, (len(data) + chunk_size - 1) // chunk_size)
    for index in range(total):
        chunk = data[index * chunk_size:(index + 1) * chunk_size]
        http_json("POST", "/api/report/chunk", {
            "upload_id": upload_id,
            "filename": filename,
            "index": index,
            "total": total,
            "contentBase64": base64.b64encode(chunk).decode("ascii")
        }, timeout=180)
    result = http_json("POST", "/api/report/chunk-finish", {
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


def post_job_result(job_id, payload):
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
    return http_json_retry("POST", f"/api/runner/jobs/{job_id}/report-ready", {
        "report_url": report_url,
        "local_report_path": local_report_path,
        "report_upload_error": report_upload_error,
    }, timeout=60, attempts=3, label="报告回传")


def report_upload_worker():
    while True:
        job_id, report_path, report_name = _REPORT_UPLOAD_QUEUE.get()
        try:
            report_url = ""
            upload_error = ""
            try:
                report_url = http_upload_report(Path(report_path), report_name)
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


def detect_devices():
    global _LAST_NO_DEVICE_LOG_AT
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
    payload = {
        "runner_id": RUNNER_ID,
        "hostname": socket.gethostname(),
        "workspace": str(WORKSPACE),
        "os": f"{platform.system()} {platform.release()}",
        "capabilities": RUNNER_CAPABILITIES,
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


def ensure_cli_interface_config(yaml_text, platform="android"):
    text = (yaml_text or "").replace("\ufeff", "")
    text = re.sub(r"^(android|ios|web|computer|interface)\s*:\s*$", r"\1: {}", text, count=1, flags=re.M)
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


def execute_midscene(job_id, job_dir, yaml_path, task_names, device_id):
    started = time.time()
    stdout_lines = []
    stderr = ""
    total = len(task_names)
    completed = 0
    current_index = 0
    current_task = task_names[0] if task_names else ""
    last_progress_at = 0

    def emit_progress(force=False, message="执行中"):
        nonlocal last_progress_at
        now = time.time()
        if not force and now - last_progress_at < 2:
            return
        last_progress_at = now
        base = 5
        progress = base
        if total:
            progress = min(95, max(base, round((completed / total) * 90) + base))
        post_job_progress(job_id, {
            "progress": progress,
            "current_task_name": current_task,
            "current_task_index": current_index,
            "completed_task_count": completed,
            "total_task_count": total,
            "device_id": device_id,
            "message": message,
            "stdout_tail": "".join(stdout_lines)[-2000:]
        })

    try:
        midscene_bin = resolve_command(MIDSCENE_BIN, "Midscene")
        emit_progress(True, "Midscene 已启动")
        proc = subprocess.Popen(
            [midscene_bin, str(yaml_path)],
            cwd=str(job_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=midscene_env(device_id)
        )
        line_queue = queue.Queue()

        def read_stdout():
            try:
                if not proc.stdout:
                    return
                for item in iter(proc.stdout.readline, ""):
                    line_queue.put(item)
            except Exception as reader_error:
                line_queue.put(f"Runner stdout reader error: {reader_error}\n")

        threading.Thread(target=read_stdout, name=f"midscene-stdout-{job_id}", daemon=True).start()
        while True:
            line = ""
            try:
                line = line_queue.get(timeout=2)
            except queue.Empty:
                line = ""
            if line:
                stdout_lines.append(line)
                stripped = line.strip()
                for idx, name in enumerate(task_names):
                    if name and name in stripped:
                        current_index = idx
                        current_task = name
                        if ("✔" in stripped or "✓" in stripped) and idx + 1 > completed:
                            completed = idx + 1
                            current_task = task_names[min(completed, total - 1)] if completed < total else name
                        if "✘" in stripped or "error:" in stripped:
                            current_task = name
                        emit_progress(True, stripped[:160])
                        break
                else:
                    if stripped:
                        emit_progress(False, stripped[:160])
            if proc.poll() is not None:
                while True:
                    try:
                        rest_line = line_queue.get_nowait()
                    except queue.Empty:
                        break
                    if rest_line:
                        stdout_lines.append(rest_line)
                break
            if time.time() - started > TIMEOUT_SECONDS:
                proc.kill()
                stderr = f"\nTimeout after {TIMEOUT_SECONDS}s"
                emit_progress(True, stderr.strip())
                break
            emit_progress(False, f"执行中，已运行 {round(time.time() - started)}s")

        returncode = proc.returncode if proc.returncode is not None else -1
        stdout = "".join(stdout_lines)
        if returncode == 0:
            completed = total
            emit_progress(True, "执行完成")
        return {
            "status": "passed" if returncode == 0 else "failed",
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


def run_job(job):
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
        yaml_content = inject_external_page_escape(midscene_cli_yaml_text(original_yaml))
        yaml_path.write_text(yaml_content, encoding="utf-8")
        task_names = [job.get("target_task_name")] if job.get("target_task_name") else parse_yaml_task_names(yaml_content)
        app_package = parse_app_package(yaml_content)

        target_task = job.get("target_task_name") or ""
        print(f"Trying device {device_id} for {job_id}{f' task={target_task}' if target_task else ''}")
        try:
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
    if report_path:
        report_name = f"{safe_filename(job.get('file', 'task')).replace('.yaml', '').replace('.yml', '')}-{job_id}.html"
        report_url = SERVER.rstrip("/") + "/reports/" + urllib.parse.quote(report_name)
        print(f"Report reserved for background upload: {report_url}")
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
        "report_upload_pending": bool(report_path),
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
    print(f"Workspace: {WORKSPACE}")
    print(f"MIDSCENE_BIN: {MIDSCENE_BIN}")
    print(f"ADB_BIN: {ADB_BIN}")
    env_preview = midscene_env()
    print(f"模型配置来源: {'Task 服务端' if _RUNTIME_ENV_CACHE else '本机环境'}{f'（服务端读取失败：{_RUNTIME_ENV_ERROR}）' if _RUNTIME_ENV_ERROR else ''}")
    print(f"MIDSCENE_MODEL_NAME: {env_preview.get('MIDSCENE_MODEL_NAME', '') or '未配置'}")
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


def main():
    validate_runner_config()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    print_startup()

    while True:
        try:
            devices = detect_devices()
            if not devices:
                time.sleep(ADB_DEVICE_CHECK_INTERVAL)
                continue
            try:
                heartbeat(devices)
            except Exception as e:
                print(f"Heartbeat error: {e}")
                diagnose_server_error(e)
                time.sleep(POLL_INTERVAL)
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
            result = run_job(job)
            print(f"Finished {job['job_id']} status={result['status']}")
            pending_report_path = result.pop("_pending_report_path", "")
            pending_report_name = result.pop("_pending_report_name", "")
            post_job_result(job["job_id"], result)
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


if __name__ == "__main__":
    main()
