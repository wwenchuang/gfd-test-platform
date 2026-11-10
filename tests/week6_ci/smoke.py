"""Run the production HTTP handler against temporary state on loopback."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from zipfile import ZipFile


def get_json(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def api_smoke(port, out):
    status, health = get_json(port, "/api/health")
    revision = os.environ["TASK_RELEASE_REVISION"]
    checks = [{
        "path": "/api/health",
        "expected": [200, True, revision],
        "actual": [status, health.get("ok"), health.get("release_revision")],
    }]
    status, account = get_json(port, "/api/auth/me")
    checks.append({
        "path": "/api/auth/me",
        "expected": [401, False, "unauthorized"],
        "actual": [status, account.get("ok"), account.get("code")],
    })
    for check in checks:
        check["passed"] = json.dumps(check["actual"]) == json.dumps(check["expected"])
    evidence = json.dumps(checks, ensure_ascii=False, indent=2)
    (out / "api.json").write_text(evidence, encoding="utf-8")
    print(evidence)
    return all(check["passed"] for check in checks)


def ui_smoke(script, port, out, mode):
    from PIL import Image

    env = dict(os.environ, W01_UI_URL=f"http://127.0.0.1:{port}/task-manager.html", W01_UI_ARTIFACTS=str(out))
    result = subprocess.run(["node", script, mode], env=env, capture_output=True, text=True, timeout=60)
    print(result.stdout, result.stderr, flush=True)
    (out / "ui.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode not in (0, 1):
        return 2
    with Image.open(out / mode / "screenshot.png") as screenshot:
        screenshot.verify()
    with ZipFile(out / mode / "trace.zip") as trace:
        if trace.testzip() or not any(name.endswith(".trace") for name in trace.namelist()):
            raise ValueError("missing or invalid Playwright trace")
    return result.returncode


def main(script, profile, destination):
    from task_server.app import TaskHTTPHandler, ThreadingHTTPServer

    mode = "intentional-failure" if profile == "wrong-title" else "positive"
    with ThreadingHTTPServer(("127.0.0.1", 0), TaskHTTPHandler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for run in range(1, 4 if profile == "nightly" else 2):
                out = Path(destination) / f"run-{run}"
                out.mkdir()
                if not api_smoke(server.server_port, out):
                    return 1
                code = ui_smoke(script, server.server_port, out, mode)
                if code:
                    return code
        finally:
            server.shutdown()
            thread.join()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(*sys.argv[1:]))
    except Exception as error:
        print(f"Smoke infrastructure failed: {error}", file=sys.stderr)
        sys.exit(2)
