#!/usr/bin/env python3
"""Comprehensive non-destructive smoke checks for the Task platform.

The script logs in, probes the main backend APIs, verifies AI Gateway and Sonic
integration, and keeps mutating operations in precheck/dry-run mode by default.

Usage:
  TASK_SMOKE_BASE_URL=http://101.34.197.12:8088 \
  TASK_SMOKE_USER=admin \
  TASK_SMOKE_PASSWORD='***' \
  python3 tests/full_platform_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Any


BASE_URL = os.getenv("TASK_SMOKE_BASE_URL", "http://127.0.0.1:8091").rstrip("/")
USERNAME = os.getenv("TASK_SMOKE_USER", "admin")
PASSWORD = os.getenv("TASK_SMOKE_PASSWORD", "")
MODULE = os.getenv("TASK_SMOKE_MODULE", "3D打印基线")
FILE = os.getenv("TASK_SMOKE_FILE", "打印记录查看.yaml")
TASK_NAME = os.getenv("TASK_SMOKE_TASK_NAME", "打印记录查看")
CASE_ID = os.getenv("TASK_SMOKE_CASE_ID", "COM_KFB_MODEL_117ec9dccef4")
OUTPUT = os.getenv("TASK_FULL_SMOKE_OUTPUT", "/tmp/midscene-full-platform-smoke.json")


class Client:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.token = ""
        self.cookie = ""

    def request(self, method: str, path: str, payload: Any = None, *, timeout: int = 30, auth: bool = True) -> dict:
        headers: dict[str, str] = {}
        if auth and self.token and not path.startswith("/ai-gateway/"):
            headers["Authorization"] = f"Bearer {self.token}"
        if self.cookie:
            headers["Cookie"] = self.cookie
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        started = time.time()
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(20000).decode("utf-8", errors="replace")
                status = int(resp.status)
                cookie = resp.headers.get("Set-Cookie")
                if cookie:
                    self.cookie = cookie.split(";", 1)[0]
        except Exception as exc:
            status = int(getattr(exc, "code", 0) or 0)
            try:
                body = exc.read().decode("utf-8", errors="replace")[:20000]
            except Exception:
                body = str(exc)
        ms = int((time.time() - started) * 1000)
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = None
        logical_ok = True
        if isinstance(parsed, dict):
            logical_ok = parsed.get("ok", parsed.get("success", True)) is not False
        elif not (200 <= status < 300):
            logical_ok = False
        return {
            "method": method,
            "path": path,
            "status": status,
            "httpOk": 200 <= status < 300,
            "logicalOk": logical_ok,
            "ms": ms,
            "body": body[:1200],
            "json": parsed,
        }


def q(value: str) -> str:
    return urllib.parse.quote(value or "")


def pick_case(cases: list[dict]) -> dict:
    for item in cases:
        text = " ".join(str(item.get(k, "")) for k in ("module", "file", "task_name", "case_id"))
        if MODULE in text and FILE in text and (not TASK_NAME or TASK_NAME in text):
            return item
    for item in cases:
        text = " ".join(str(item.get(k, "")) for k in ("module", "file", "task_name", "case_id"))
        if "打印记录" in text:
            return item
    return cases[0] if cases else {}


def summarize_json(row: dict) -> str:
    obj = row.get("json")
    if isinstance(obj, dict):
        if obj.get("error"):
            return str(obj.get("error"))[:140]
        if obj.get("message"):
            return str(obj.get("message"))[:140]
        if "items" in obj and isinstance(obj["items"], list):
            return f"items={len(obj['items'])}"
        if "cases" in obj and isinstance(obj["cases"], list):
            return f"cases={len(obj['cases'])}"
        if "runs" in obj and isinstance(obj["runs"], list):
            return f"runs={len(obj['runs'])}"
        if "jobs" in obj and isinstance(obj["jobs"], list):
            return f"jobs={len(obj['jobs'])}"
    return ""


def main() -> int:
    if not PASSWORD:
        print("TASK_SMOKE_PASSWORD is required", file=sys.stderr)
        return 2
    client = Client(BASE_URL)
    results: list[dict] = []

    def add(name: str, method: str, path: str, payload: Any = None, *, require_ok: bool = True, auth: bool = True) -> dict:
        row = client.request(method, path, payload, auth=auth)
        row["name"] = name
        row["required"] = require_ok
        row["ok"] = row["httpOk"] and (row["logicalOk"] or not require_ok)
        results.append(row)
        mark = "OK" if row["ok"] else ("WARN" if not require_ok else "BAD")
        print(f"{mark:4} {name:30} {method:4} {path[:76]:76} HTTP={row['status']} {row['ms']}ms {summarize_json(row)}")
        return row

    login = add("auth.login", "POST", "/api/auth/login", {"username": USERNAME, "password": PASSWORD}, auth=False)
    if not login["ok"]:
        print(json.dumps({"failed": login}, ensure_ascii=False, indent=2))
        return 1
    login_json = login.get("json") or {}
    client.token = login_json.get("token") or login_json.get("sessionToken") or ""

    mod = q(MODULE)
    file = q(FILE)
    case_id = q(CASE_ID)
    base_gets = [
        ("health", "/api/health"),
        ("auth.me", "/api/auth/me"),
        ("models", "/api/models"),
        ("apps", "/api/apps"),
        ("modules", "/api/modules"),
        ("task.meta", "/api/task-meta"),
        ("task.apps", "/api/task-apps"),
        ("platform.status", "/api/platform/status"),
        ("tasks.legacy", "/api/tasks"),
        ("cases.legacy", "/api/cases"),
        ("jobs", "/api/jobs"),
        ("runners", "/api/runners"),
        ("agent.runs", "/api/agent-runs"),
        ("agent.tools", "/api/agent-tools"),
        ("reports", "/api/reports"),
        ("reports.cleanup.preview", "/api/reports/cleanup?dry_run=1&days=14&min_keep=20"),
        ("preflight.dashboard", "/api/preflight/dashboard?live=1"),
        ("yaml.stats", f"/api/yaml-stats?module={mod}"),
        ("file.get", f"/api/file?module={mod}&file={file}"),
        ("file.history", f"/api/file/history?module={mod}&file={file}"),
        ("baseline.refs", f"/api/baseline/page-refs?module={mod}&file={file}"),
        ("repair.drafts", "/api/repair-drafts"),
        ("generation.status.invalid", "/api/ui/generate-status?job_id=__smoke_invalid__",),
        ("mindmaps", "/api/cases/mindmaps?limit=5"),
        ("knowledge.apps", "/api/knowledge/apps"),
        ("knowledge.stats", "/api/knowledge/stats"),
        ("knowledge.failures", f"/api/knowledge/failures?log={q('failed to locate element AI call error')}&topK=2"),
        ("knowledge.cases", f"/api/knowledge/cases?file={q(f'{MODULE}/{FILE}')}&limit=3"),
    ]
    for name, path in base_gets:
        add(name, "GET", path, require_ok=name != "generation.status.invalid")

    sonic_gets = [
        ("sonic.config", "/api/sonic/config"),
        ("sonic.runtime.env", "/api/sonic/runtime-env"),
        ("sonic.cases", "/api/sonic/cases"),
        ("sonic.status", f"/api/sonic/status?module={mod}&file={file}"),
        ("sonic.suite.results", "/api/sonic/suite-results"),
        ("sonic.bridge.diagnose", f"/api/sonic/bridge-diagnose?case_id={case_id}"),
        ("sonic.callback.diagnose", "/api/sonic/callback-diagnose"),
    ]
    sonic_cases_row = None
    for name, path in sonic_gets:
        row = add(name, "GET", path)
        if name == "sonic.cases":
            sonic_cases_row = row

    cases = []
    if sonic_cases_row and isinstance(sonic_cases_row.get("json"), dict):
        cases = sonic_cases_row["json"].get("cases") or []
    chosen = pick_case(cases)
    chosen_case_id = chosen.get("case_id") or CASE_ID
    chosen_module = chosen.get("module") or MODULE
    chosen_file = chosen.get("file") or FILE
    chosen_task = chosen.get("task_name") or TASK_NAME
    add("sonic.case", "GET", f"/api/sonic/case?case_id={q(chosen_case_id)}")
    add("sonic.case.yaml", "GET", f"/api/sonic/case-yaml?case_id={q(chosen_case_id)}")
    add("sonic.publish.check", "POST", "/api/sonic/publish-check", {
        "module": chosen_module,
        "file": chosen_file,
        "taskName": chosen_task,
    })
    add("sonic.scan.legacy.scope", "POST", "/api/sonic/scan-legacy", {
        "module": chosen_module,
        "file": chosen_file,
    })
    add("sonic.run.case.deprecated", "GET", "/api/sonic/run-case", require_ok=False)
    add("sonic.bridge.groovy.unauth", "GET", f"/api/sonic/bridge-groovy?case_id={q(chosen_case_id)}", require_ok=False, auth=False)

    add("agent.preview", "POST", "/api/agent-runs/preview", {
        "target": "回归一下查看打印记录基线用例",
        "appName": "智小白3D APP",
        "platform": "android",
        "mode": "AUTO_SAFE",
        "executionMode": "RUNNER_JOB",
        "sourceType": "manual",
        "sourceRefs": {},
    })
    add("ai.gateway.health", "GET", "/ai-gateway/health", auth=False)
    add("ai.gateway.providers", "GET", "/ai-gateway/ai/providers", auth=False)
    add("ai.gateway.router", "GET", "/ai-gateway/ai/model-router", auth=False)

    failed = [r for r in results if not r["ok"] and r["required"]]
    output = {
        "baseUrl": BASE_URL,
        "module": MODULE,
        "file": FILE,
        "chosenCase": {
            "module": chosen_module,
            "file": chosen_file,
            "taskName": chosen_task,
            "caseId": chosen_case_id,
        },
        "total": len(results),
        "failed": failed,
        "results": [
            {k: v for k, v in r.items() if k not in ("json",)}
            for r in results
        ],
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"saved {OUTPUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
