#!/usr/bin/env python3
"""Non-destructive live API smoke checks for the Task platform.

Usage:
  TASK_SMOKE_BASE_URL=http://101.34.197.12:8088 \
  TASK_SMOKE_USER=admin \
  TASK_SMOKE_PASSWORD='***' \
  python3 tests/live_api_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request


BASE_URL = os.getenv("TASK_SMOKE_BASE_URL", "http://127.0.0.1:8091").rstrip("/")
USERNAME = os.getenv("TASK_SMOKE_USER", "admin")
PASSWORD = os.getenv("TASK_SMOKE_PASSWORD", "")
MODULE = os.getenv("TASK_SMOKE_MODULE", "3D打印基线")
FILE = os.getenv("TASK_SMOKE_FILE", "打印记录查看.yaml")
CASE_ID = os.getenv("TASK_SMOKE_CASE_ID", "COM_KFB_MODEL_117ec9dccef4")


def request(method: str, path: str, token: str = "", payload=None, timeout: int = 30) -> dict:
    headers = {}
    if token and not path.startswith("/ai-gateway/"):
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(5000).decode("utf-8", errors="replace")
            status = resp.status
    except Exception as exc:
        status = getattr(exc, "code", 0) or 0
        try:
            body = exc.read().decode("utf-8", errors="replace")[:5000]
        except Exception:
            body = str(exc)
    ms = int((time.time() - started) * 1000)
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = None
    logical_ok = True
    error = ""
    if isinstance(parsed, dict):
        logical_ok = parsed.get("ok", parsed.get("success", True)) is not False
        error = str(parsed.get("error") or parsed.get("message") or "")
    elif not (200 <= int(status or 0) < 300):
        logical_ok = False
        error = body[:180]
    return {
        "method": method,
        "path": path,
        "status": status,
        "httpOk": 200 <= int(status or 0) < 300,
        "logicalOk": logical_ok,
        "ms": ms,
        "error": error,
        "body": body[:800],
    }


def main() -> int:
    if not PASSWORD:
        print("TASK_SMOKE_PASSWORD is required", file=sys.stderr)
        return 2
    login = request("POST", "/api/auth/login", payload={"username": USERNAME, "password": PASSWORD})
    try:
        token = json.loads(login["body"]).get("token", "")
    except Exception:
        token = ""
    if not (login["httpOk"] and token):
        print(json.dumps({"login": login}, ensure_ascii=False, indent=2))
        return 1

    mod = urllib.parse.quote(MODULE)
    file = urllib.parse.quote(FILE)
    case_id = urllib.parse.quote(CASE_ID)
    checks = [
        ("GET", "/api/health", None, True),
        ("GET", "/api/auth/me", None, True),
        ("GET", "/api/modules", None, True),
        ("GET", "/api/task-apps", None, True),
        ("GET", "/api/task-meta", None, True),
        ("GET", "/api/apps", None, True),
        ("GET", "/api/models", None, True),
        ("GET", "/api/runners", None, True),
        ("GET", "/api/jobs", None, True),
        ("GET", "/api/agent-runs", None, True),
        ("GET", "/api/sonic/cases", None, True),
        ("POST", "/api/sonic/diagnose", {}, True),
        ("GET", f"/api/sonic/bridge-diagnose?case_id={case_id}", None, True),
        ("GET", "/api/sonic/callback-diagnose", None, True),
        ("GET", "/api/preflight/dashboard?live=1", None, True),
        ("GET", "/api/cases/mindmaps?limit=5", None, True),
        ("GET", f"/api/yaml-stats?module={mod}", None, True),
        ("GET", f"/api/sonic/status?module={mod}&file={file}", None, True),
        ("GET", f"/api/file?module={mod}&file={file}", None, True),
        ("GET", f"/api/file/history?module={mod}&file={file}", None, True),
        ("GET", f"/api/baseline/page-refs?module={mod}&file={file}", None, True),
        ("POST", "/api/agent-runs/preview", {
            "target": "回归一下查看打印记录基线用例",
            "appName": "智小白3D APP",
            "platform": "android",
            "mode": "AUTO_SAFE",
            "executionMode": "RUNNER_JOB",
            "sourceType": "manual",
            "sourceRefs": {},
        }, True),
        ("POST", "/api/sonic/publish-check", {"module": MODULE, "file": FILE, "taskName": ""}, False),
        ("GET", "/ai-gateway/health", None, True),
        ("GET", "/ai-gateway/ai/providers", None, True),
        ("GET", "/ai-gateway/ai/model-router", None, True),
    ]

    results = []
    failed = []
    for method, path, payload, require_logical_ok in checks:
        row = request(method, path, token=token, payload=payload)
        row["required"] = require_logical_ok
        results.append(row)
        ok = row["httpOk"] and (row["logicalOk"] or not require_logical_ok)
        if not ok:
            failed.append(row)
        mark = "OK" if ok else "BAD"
        print(f"{mark:3} {method:4} {path[:72]:72} HTTP={row['status']} {row['ms']}ms {row['error'][:120]}")

    out_path = os.getenv("TASK_SMOKE_OUTPUT", "/tmp/midscene-live-smoke.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"baseUrl": BASE_URL, "results": results, "failed": failed}, f, ensure_ascii=False, indent=2)
    print(f"saved {out_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
