"""Real IAM HTTP fixture. All mutable state is isolated; outbound sockets are denied."""

import json
import os
from pathlib import Path
import signal
import sys
import threading


ROOT = Path(__file__).resolve().parents[2]
STATE = Path(os.environ["IDENTITY_E2E_STATE"]).resolve()
sys.path.insert(0, str(ROOT))

DIRECTORIES = {
    "TASK_DIR": "tasks", "REPORT_DIR": "reports", "LEARNING_DIR": "learning",
    "ASSET_DIR": "assets", "CASE_DIR": "cases", "GENERATE_JOB_DIR": "generation",
    "KNOWLEDGE_DIR": "knowledge",
}
for key, name in DIRECTORIES.items():
    directory = STATE / name
    directory.mkdir(parents=True, exist_ok=True)
    os.environ[key] = str(directory)
os.environ.update({
    "TASK_AUTH_DB": str(STATE / "auth" / "identity.sqlite3"),
    "MIDSCENE_ENV_FILE": str(STATE / "absent.env"),
    "API_TESTING_ENABLED": "0",
    "TASK_APP_ENV": "test",
    "TASK_ADMIN_USER": "admin",
    "SONIC_BASE_URL": "http://127.0.0.1:1",
    "AI_GATEWAY_URL": "http://127.0.0.1:1",
    "CASE_PLATFORM_BASE_URL": "http://127.0.0.1:1",
})

apps = []
metadata = {}
for letter in ("a", "b"):
    module = f"IAM-{letter.upper()}"
    filename = f"scope-{letter}.yaml"
    app = f"app.iam.{letter}"
    directory = STATE / "tasks" / module
    directory.mkdir(exist_ok=True)
    target = directory / filename
    if not target.exists():
        target.write_text(
            f"android:\n  launch: {app}\ntasks:\n  - name: IAM fixture {letter.upper()}\n"
            f"    flow:\n      - aiAssert: IAM_ONLY_{letter.upper()}\n", encoding="utf-8",
        )
    apps.append({"package": app, "name": f"IAM\u5e94\u7528{letter.upper()}", "modules": [module], "enabled": True})
    metadata[f"{module}/{filename}"] = {"module": module, "file": filename, "app_package": app, "status": "draft"}
for name, data in (("task-apps.json", {"apps": apps}), ("task-meta.json", metadata)):
    target = STATE / "learning" / name
    if not target.exists():
        target.write_text(json.dumps(data), encoding="utf-8")

outbound_attempts = 0


def deny_outbound(event, args):
    global outbound_attempts
    if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
        outbound_attempts += 1
        raise OSError("IAM fixture forbids outbound connections")


sys.addaudithook(deny_outbound)

# Import the production handler only after setting every state path. No auth patches,
# fake profiles, route replacements, background workers or Runner startup are used.
from task_server.app import TaskHTTPHandler, ThreadingHTTPServer


server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("IDENTITY_E2E_PORT", "0"))), TaskHTTPHandler)


def stop(_signum, _frame):
    threading.Thread(target=server.shutdown, daemon=True).start()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
print(json.dumps({"port": server.server_port}), flush=True)
try:
    server.serve_forever(poll_interval=0.1)
finally:
    server.server_close()
    print(json.dumps({"outbound_attempts": outbound_attempts}), flush=True)
