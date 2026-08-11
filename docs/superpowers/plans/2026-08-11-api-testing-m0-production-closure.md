# API Testing M0 Production Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make API testing production readiness explicit, block unsafe deployments, eliminate opaque dependency 500s, and complete the real “我的收藏” three-endpoint loop.

**Architecture:** Add a bounded readiness service and authenticated HTTP route, publish a Redis worker heartbeat, classify dependency errors, and add a deployment preflight that checks connection and migration state before service restart. Existing API testing services and execution behavior remain unchanged.

**Tech Stack:** Python 3.10, SQLAlchemy 2, Alembic, PostgreSQL 16, Redis 7, Celery, urllib, pytest, shell static checks.

## Global Constraints

- No database volume deletion or implicit role-password reset.
- `/api/health` remains process health; `/api/api-testing/v1/readiness` represents API testing readiness.
- Readiness responses contain component state and revisions but no URLs, passwords, tokens, stack traces, or Redis keys.
- The installer must stop before restart if database authentication or migration fails.
- Keep AI Gateway on `8090` and Task Server on `8091`.
- Do not alter API test domain tables in M0.

---

### Task 1: Add readiness component checks

**Files:**
- Create: `task_server/api_testing/services/readiness_service.py`
- Modify: `task_server/api_testing/services/__init__.py`
- Test: `tests/api_testing/test_readiness_service.py`

**Interfaces:**
- Consumes: `ApiTestingSettings`, `engine_for_url()`, Redis client, Alembic `ScriptDirectory`.
- Produces: `ReadinessService.check() -> dict` with `ready`, `database`, `redis`, `worker`, `ai_gateway`, and `api_testing` keys.

- [ ] **Step 1: Write failing component tests**

```python
def test_readiness_is_false_when_database_authentication_fails(settings):
    service = ReadinessService(
        settings,
        database_probe=lambda: (_ for _ in ()).throw(RuntimeError("auth failed")),
        redis_probe=lambda: True,
        worker_probe=lambda: True,
        gateway_probe=lambda: True,
        migration_probe=lambda: ("0003", "0003"),
    )
    result = service.check()
    assert result["ready"] is False
    assert result["database"] == {"connected": False, "error_code": "database_unavailable"}
    assert "auth failed" not in str(result)


def test_readiness_requires_current_migration(settings):
    service = ReadinessService(
        settings,
        database_probe=lambda: True,
        redis_probe=lambda: True,
        worker_probe=lambda: True,
        gateway_probe=lambda: True,
        migration_probe=lambda: ("0002", "0003"),
    )
    result = service.check()
    assert result["ready"] is False
    assert result["database"]["migration_current"] == "0002"
    assert result["database"]["migration_expected"] == "0003"
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `.venv/bin/python -m pytest tests/api_testing/test_readiness_service.py -q`

Expected: FAIL with `ModuleNotFoundError: task_server.api_testing.services.readiness_service`.

- [ ] **Step 3: Implement the service with injectable probes**

```python
class ReadinessService:
    def __init__(self, settings, *, database_probe=None, redis_probe=None,
                 worker_probe=None, gateway_probe=None, migration_probe=None):
        self.settings = settings
        self.database_probe = database_probe or self._database_probe
        self.redis_probe = redis_probe or self._redis_probe
        self.worker_probe = worker_probe or self._worker_probe
        self.gateway_probe = gateway_probe or self._gateway_probe
        self.migration_probe = migration_probe or self._migration_probe

    def check(self):
        components = {
            "database": self._safe_database(),
            "redis": self._safe_boolean("redis_unavailable", self.redis_probe),
            "worker": self._safe_boolean("worker_unavailable", self.worker_probe),
            "ai_gateway": self._safe_boolean("ai_gateway_unavailable", self.gateway_probe),
            "api_testing": {"enabled": self.settings.enabled},
        }
        components["ready"] = self.settings.enabled and all(
            self._component_ready(components[name])
            for name in ("database", "redis", "worker", "ai_gateway")
        )
        return components
```

Use `SELECT 1` for PostgreSQL, `PING` for Redis, the current `alembic_version` plus `ScriptDirectory.get_current_head()` for migration state, the Redis heartbeat key for Worker, and `GET /health` against the configured AI Gateway base with a two-second timeout.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_readiness_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/services/readiness_service.py task_server/api_testing/services/__init__.py tests/api_testing/test_readiness_service.py
git commit -m "Add API testing readiness diagnostics"
```

### Task 2: Publish a Redis Worker heartbeat

**Files:**
- Modify: `task_server/api_testing/tasks.py`
- Modify: `task_server/api_testing/config.py`
- Test: `tests/api_testing/test_tasks.py`
- Test: `tests/api_testing/test_config.py`

**Interfaces:**
- Produces: `ApiTestingSettings.worker_heartbeat_key`, `ApiTestingSettings.worker_heartbeat_ttl_seconds`.
- Produces: Celery `heartbeat_sent` handler refreshing the key with an expiry.

- [ ] **Step 1: Write failing heartbeat tests**

```python
def test_worker_heartbeat_has_short_expiry(monkeypatch, fake_redis):
    monkeypatch.setattr(tasks, "_heartbeat_redis", lambda: fake_redis)
    tasks.publish_worker_heartbeat(None)
    fake_redis.set.assert_called_once_with(
        "midscene:api-testing:worker-heartbeat",
        "1",
        ex=45,
    )
```

- [ ] **Step 2: Verify the test fails**

Run: `.venv/bin/python -m pytest tests/api_testing/test_tasks.py -q`

Expected: FAIL because `publish_worker_heartbeat` does not exist.

- [ ] **Step 3: Implement the signal handler**

```python
from celery.signals import heartbeat_sent, worker_ready

@heartbeat_sent.connect
@worker_ready.connect
def publish_worker_heartbeat(sender=None, **kwargs):
    settings = ApiTestingSettings.from_env()
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    client.set(
        settings.worker_heartbeat_key,
        "1",
        ex=settings.worker_heartbeat_ttl_seconds,
    )
```

Catch Redis exceptions inside the signal handler and log a warning; a heartbeat failure must not crash the Worker.

- [ ] **Step 4: Run config and task tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_config.py tests/api_testing/test_tasks.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/config.py task_server/api_testing/tasks.py tests/api_testing/test_config.py tests/api_testing/test_tasks.py
git commit -m "Publish API worker readiness heartbeat"
```

### Task 3: Expose authenticated readiness and dependency errors

**Files:**
- Modify: `task_server/api_testing/http.py`
- Test: `tests/api_testing/test_http_contract.py`

**Interfaces:**
- Produces: `GET /api/api-testing/v1/readiness`.
- Produces: stable codes `database_unavailable`, `redis_unavailable`, `migration_required`, and `internal_error` with `request_id`.

- [ ] **Step 1: Write failing HTTP contract tests**

```python
def test_readiness_route_returns_component_state(api_client, monkeypatch):
    monkeypatch.setattr(http, "_readiness", lambda settings: {"ready": True})
    response = api_client.get("/api/api-testing/v1/readiness")
    assert response.status_code == 200
    assert response.json["data"]["ready"] is True


def test_database_failure_is_safe_and_traceable(api_client, monkeypatch):
    monkeypatch.setattr(http, "_factory", lambda: (_ for _ in ()).throw(OperationalError("x", {}, Exception("secret"))))
    response = api_client.get("/api/api-testing/v1/projects")
    assert response.status_code == 503
    assert response.json["error"]["code"] == "database_unavailable"
    assert response.json["request_id"]
    assert "secret" not in response.text
```

- [ ] **Step 2: Verify tests fail with 404 and 500**

Run: `.venv/bin/python -m pytest tests/api_testing/test_http_contract.py -q`

Expected: new assertions FAIL because readiness is not routed and database errors map to `internal_error` 500.

- [ ] **Step 3: Route readiness and classify dependency exceptions**

Add readiness before data routes in `_get()`. Extend `_domain_error()` to map SQLAlchemy/psycopg operational failures to 503 `database_unavailable` and Redis connection failures to 503 `redis_unavailable`. Log the original exception with `request_id`, route, method, and actor in `_dispatch()` using `logging.exception`, while returning only the safe response.

- [ ] **Step 4: Run the HTTP contract suite**

Run: `.venv/bin/python -m pytest tests/api_testing/test_http_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add task_server/api_testing/http.py tests/api_testing/test_http_contract.py
git commit -m "Expose API testing readiness errors"
```

### Task 4: Add deployment preflight and migration gate

**Files:**
- Create: `deploy/api-testing-preflight.py`
- Create: `tests/api_testing/test_deploy_preflight.py`
- Modify: `deploy/api-testing-migrate.sh`
- Modify: `deploy/install-server.sh`
- Modify: `deploy/midscene.env.example`
- Test: `tests/backend_static_checks.py`

**Interfaces:**
- Produces: `python deploy/api-testing-preflight.py --mode connection|migration`.
- Installer invokes connection preflight, migration, migration preflight, then restart.

- [ ] **Step 1: Write failing preflight tests**

```python
def test_preflight_reports_database_authentication_without_password(tmp_path):
    result = run_preflight(
        {"API_TESTING_ENABLED": "1", "API_TESTING_DATABASE_URL": "postgresql+psycopg://midscene:wrong@127.0.0.1/db"}
    )
    assert result.returncode == 12
    assert "数据库认证失败" in result.stderr
    assert "wrong" not in result.stderr


def test_installer_migrates_before_restarting_services():
    source = Path("deploy/install-server.sh").read_text()
    assert source.index("api-testing-migrate.sh") < source.index("systemctl restart midscene-task.service")
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/api_testing/test_deploy_preflight.py -q`

Expected: FAIL because the preflight script and ordering assertion do not exist.

- [ ] **Step 3: Implement safe probes and installer order**

The preflight must parse settings through `ApiTestingSettings`, run `SELECT 1`, and compare the database `alembic_version.version_num` with Alembic head. Exit codes are stable: `10` configuration, `12` database connection/authentication, `13` Redis, `14` migration drift. It must never print connection URLs.

`install-server.sh` must install dependencies and files first, then run:

```bash
"${VENV_DIR}/bin/python" "${APP_DIR}/deploy/api-testing-preflight.py" --mode connection
APP_DIR="${APP_DIR}" bash "${APP_DIR}/deploy/api-testing-migrate.sh"
"${VENV_DIR}/bin/python" "${APP_DIR}/deploy/api-testing-preflight.py" --mode migration
systemctl restart midscene-task.service
systemctl restart midscene-api-worker.service
```

- [ ] **Step 4: Run deployment checks**

Run:

```bash
.venv/bin/python -m pytest tests/api_testing/test_deploy_preflight.py -q
bash -n deploy/install-server.sh deploy/api-testing-migrate.sh
python3 tests/backend_static_checks.py
```

Expected: PASS and both shell scripts parse successfully.

- [ ] **Step 5: Commit**

```bash
git add deploy/api-testing-preflight.py deploy/api-testing-migrate.sh deploy/install-server.sh deploy/midscene.env.example tests/api_testing/test_deploy_preflight.py tests/backend_static_checks.py
git commit -m "Block deployment on API database drift"
```

### Task 5: Show readiness failures in the Vue shell

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/api/client.ts`
- Modify: `api-testing-ui/src/stores/setup.ts`
- Modify: `api-testing-ui/src/App.vue`
- Create: `api-testing-ui/src/components/ReadinessBanner.vue`
- Test: `api-testing-ui/src/stores/setup.spec.ts`
- Create: `api-testing-ui/src/components/ReadinessBanner.spec.ts`

**Interfaces:**
- Consumes: `GET /readiness`.
- Produces: persistent Chinese readiness banner with retry and request ID; disables writes while retaining read-only history navigation.

- [ ] **Step 1: Write failing store and component tests**

```ts
it('keeps reports readable while disabling writes when database is unavailable', async () => {
  api.getReadiness.mockResolvedValue({ ready: false, database: { connected: false, error_code: 'database_unavailable' } })
  await store.loadReadiness()
  expect(store.canWrite).toBe(false)
  expect(store.readinessMessage).toContain('数据库')
})
```

- [ ] **Step 2: Verify tests fail**

Run: `npm --prefix api-testing-ui test -- --run src/stores/setup.spec.ts src/components/ReadinessBanner.spec.ts`

Expected: FAIL because readiness state and component are missing.

- [ ] **Step 3: Implement the banner and write guard**

Render one unframed status band beneath the context bar. Map component codes to concise Chinese text; place technical details and request ID behind a disclosure. Do not render raw database URLs or service IDs.

- [ ] **Step 4: Run frontend tests and build**

Run:

```bash
npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
```

Expected: PASS and Vite build exits `0`.

- [ ] **Step 5: Commit**

```bash
git add api-testing-ui/src/api/contracts.ts api-testing-ui/src/api/client.ts api-testing-ui/src/stores/setup.ts api-testing-ui/src/App.vue api-testing-ui/src/components/ReadinessBanner.vue api-testing-ui/src/stores/setup.spec.ts api-testing-ui/src/components/ReadinessBanner.spec.ts
git commit -m "Show API subsystem readiness"
```

### Task 6: Add a secure production smoke command

**Files:**
- Create: `deploy/api-testing-smoke.py`
- Create: `tests/api_testing/test_production_smoke.py`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: base URL plus `TASK_ADMIN_USER` / password through environment or an existing session token.
- Produces: non-secret status summary for health, login, readiness, projects, workspace, and context options.

- [ ] **Step 1: Write failing redaction and exit-code tests**

```python
def test_smoke_never_prints_session_token(fake_server, capsys):
    result = smoke.run(fake_server.url, username="admin", password="secret")
    assert result == 0
    assert "session-token-value" not in capsys.readouterr().out


def test_smoke_fails_when_readiness_is_false(fake_server):
    fake_server.readiness = {"ready": False, "database": {"connected": False}}
    assert smoke.run(fake_server.url, username="admin", password="secret") == 2
```

- [ ] **Step 2: Verify tests fail because the smoke module is absent**

Run: `.venv/bin/python -m pytest tests/api_testing/test_production_smoke.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement JSON-only smoke checks**

Use `urllib.request`, ten-second timeouts, redacted output, and stable non-zero exit codes. The command must not accept tokens as command-line arguments; read them from environment or login response held only in memory.

- [ ] **Step 4: Run smoke unit tests**

Run: `.venv/bin/python -m pytest tests/api_testing/test_production_smoke.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/api-testing-smoke.py deploy/README.md tests/api_testing/test_production_smoke.py
git commit -m "Add API production smoke verification"
```

### Task 7: Run the M0 full and production gates

**Files:**
- Modify: `CODEX_STATE.md`

- [ ] **Step 1: Run the complete local gate**

Run the commands in the roadmap milestone gate.

Expected: all commands exit `0`.

- [ ] **Step 2: Deploy and verify readiness**

Run on the server without Markdown link syntax and without deleting volumes:

```bash
cd /opt/midscene-task-platform-src
git pull --ff-only
bash deploy/install-server.sh
systemctl restart midscene-task midscene-api-worker
/opt/midscene-task-platform/.venv/bin/python /opt/midscene-task-platform/deploy/api-testing-smoke.py --base-url http://127.0.0.1:8091
```

Expected: readiness reports database, Redis, Worker, migration, and AI Gateway ready; projects, workspace, and context options return `200`.

- [ ] **Step 3: Execute the real “我的收藏” workflow**

In the browser select the three saved endpoints, generate drafts, debug each version, adopt only passed versions, execute the current task baselines, follow SSE to terminal state, refresh the page, and reopen the report. Record execution ID, child counts, statuses, failure categories, and request IDs.

Expected: no platform 500, no stuck polling, no result rewriting, and no secret in UI/log/report. Business failures remain visible as business results.

- [ ] **Step 4: Update state and commit M0 evidence**

Add the verified commit, commands, counts, production execution ID, and unresolved business failures to `CODEX_STATE.md` without tokens.

```bash
git add CODEX_STATE.md
git commit -m "Record API testing M0 verification"
```
