# API Testing Platform Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete manual API testing loop that saves Apifox/OpenAPI revisions and environments, generates editable AI case drafts, debugs selected cases asynchronously, streams stable logs, adopts passing cases as baselines, and shows truthful reports.

**Architecture:** Add a bounded `task_server.api_testing` module backed by PostgreSQL and Redis/Celery, mounted through one route prefix in the existing server. Add a separate Vue 3 + TypeScript application served at `/api-test/`, reusing the existing session token while owning all API-testing state. Keep UI Agent, Runner, Sonic, legacy static pages, and preserved API SQLite files untouched.

**Tech Stack:** Python 3.9+, SQLAlchemy 2.0, Alembic, psycopg 3, Redis, Celery 5, cryptography, PostgreSQL 16, Redis 7, Vue 3, TypeScript, Vite, Pinia, Vue Router, Vitest, Playwright, Lucide Vue Next.

## Global Constraints

- Apifox refresh is manual only; entering the workspace must read the saved PostgreSQL revision without contacting Apifox.
- Source definitions, test assets, and execution records are separate data layers.
- AI output is a versioned draft and cannot overwrite a confirmed baseline.
- Debugging is allowed before baseline adoption.
- `FAILED` means an assertion failure; environment, script, network, timeout, and infrastructure failures use `BROKEN`.
- Secrets are encrypted at rest and never returned in plaintext, logged, reported, or sent to AI by default.
- The Vue application is isolated from the legacy frontend and shares only the same-origin session token and visual tokens.
- `task_server/router.py` mounts one module route and contains no API-testing business logic.
- Existing UI Agent, Runner, Sonic, YAML, and user dirty files must not be modified except for the single navigation link and deployment integration explicitly named below.
- Every behavior change follows red-green-refactor and ends with focused verification and a commit.

---

## File Map

### Deployment and Dependencies

- `requirements-api-testing.txt`: pinned server dependencies for the new module.
- `requirements-api-testing-dev.txt`: pytest and backend test-only dependencies.
- `deploy/api-testing-compose.yml`: PostgreSQL and Redis containers bound to localhost.
- `deploy/midscene-api-worker.service`: Celery worker service.
- `deploy/api-testing-migrate.sh`: idempotent Alembic migration command.
- `deploy/install-server.sh`: installs the venv, copies Vue assets, installs worker service, and runs migrations.
- `deploy/midscene.env.example`: documents database, Redis, encryption, and worker settings.

### Backend Module

- `task_server/api_testing/config.py`: validated environment configuration.
- `task_server/api_testing/db.py`: SQLAlchemy engine, session scope, and readiness probe.
- `task_server/api_testing/crypto.py`: authenticated secret encryption and fingerprints.
- `task_server/api_testing/models/*.py`: project/source, environment, case, and execution tables.
- `task_server/api_testing/repositories/*.py`: persistence interfaces with no HTTP knowledge.
- `task_server/api_testing/contracts/*.py`: public request and response normalization.
- `task_server/api_testing/services/source_service.py`: OpenAPI normalization, diff, and revision activation.
- `task_server/api_testing/services/environment_service.py`: editable revisions, secret updates, and runtime resolution.
- `task_server/api_testing/services/case_service.py`: drafts, validation, versions, and baseline adoption.
- `task_server/api_testing/services/ai_service.py`: AI Gateway job submission and structured draft validation.
- `task_server/api_testing/services/execution_service.py`: task creation, state transitions, cancellation, and report queries.
- `task_server/api_testing/executor.py`: HTTP request resolution, sending, extraction, and assertion evaluation.
- `task_server/api_testing/tasks.py`: Celery execution and AI generation tasks.
- `task_server/api_testing/events.py`: Redis event stream append/read/resume.
- `task_server/api_testing/http.py`: authenticated REST/SSE adapter.
- `task_server/api_testing/routes.py`: one prefix registration function.
- `task_server/api_testing/migrations/*`: Alembic configuration and revisions.
- `task_server/router.py`: invokes the single registration function after route decorators are defined.

### Vue Application

- `api-testing-ui/package.json`, `vite.config.ts`, `tsconfig*.json`: isolated build and test configuration.
- `api-testing-ui/src/api/*`: typed API client and response contracts.
- `api-testing-ui/src/stores/*`: project context, assets, drafts, and execution state.
- `api-testing-ui/src/components/*`: context bar, endpoint tree, case editor, AI assistant, debug drawer, log console, and report panel.
- `api-testing-ui/src/views/WorkbenchView.vue`: three-column primary workflow.
- `api-testing-ui/src/views/AssetsView.vue`, `RunsView.vue`, `ReportsView.vue`, `SettingsView.vue`: secondary views.
- `api-testing-ui/src/styles/*`: domain-specific responsive visual system.
- `api-test/`: committed production build consumed by deployment.
- `task-manager.html`: one same-tab `API 测试` navigation entry.

### Tests

- `tests/api_testing/test_config.py`
- `tests/api_testing/test_crypto.py`
- `tests/api_testing/test_migrations.py`
- `tests/api_testing/test_source_service.py`
- `tests/api_testing/test_environment_service.py`
- `tests/api_testing/test_case_service.py`
- `tests/api_testing/test_executor.py`
- `tests/api_testing/test_execution_service.py`
- `tests/api_testing/test_http_contract.py`
- `api-testing-ui/src/**/*.spec.ts`
- `tests/api_testing_e2e.spec.mjs`
- `tests/backend_static_checks.py`
- `tests/frontend_static_checks.py`

---

### Task 1: Reproducible PostgreSQL, Redis, and Python Runtime

**Files:**
- Create: `requirements-api-testing.txt`
- Create: `requirements-api-testing-dev.txt`
- Create: `deploy/api-testing-compose.yml`
- Create: `deploy/api-testing-migrate.sh`
- Create: `deploy/midscene-api-worker.service`
- Modify: `deploy/midscene.env.example`
- Modify: `deploy/install-server.sh`
- Test: `tests/api_testing/test_config.py`
- Test: `tests/backend_static_checks.py`

**Interfaces:**
- Produces: `ApiTestingSettings.from_env() -> ApiTestingSettings`
- Produces environment variables `API_TESTING_DATABASE_URL`, `API_TESTING_REDIS_URL`, `API_TESTING_SECRET_KEY`, `API_TESTING_QUEUE`, and `API_TESTING_ENABLED`.
- Produces system services `midscene-task.service` and `midscene-api-worker.service` using the same application venv.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_settings_require_secret_when_enabled(monkeypatch):
    monkeypatch.setenv("API_TESTING_ENABLED", "1")
    monkeypatch.delenv("API_TESTING_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="API_TESTING_SECRET_KEY"):
        ApiTestingSettings.from_env()


def test_settings_are_disabled_without_infrastructure(monkeypatch):
    monkeypatch.setenv("API_TESTING_ENABLED", "0")
    settings = ApiTestingSettings.from_env()
    assert settings.enabled is False
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python3 -m pytest tests/api_testing/test_config.py -v`

Expected: import failure because `task_server.api_testing.config` does not exist.

- [ ] **Step 3: Add pinned dependencies and settings implementation**

Use compatible bounded ranges:

```text
SQLAlchemy>=2.0,<2.1
alembic>=1.14,<2
psycopg[binary]>=3.2,<3.3
redis>=5.2,<6
celery[redis]>=5.4,<6
cryptography>=44,<46
```

`requirements-api-testing-dev.txt` contains:

```text
-r requirements-api-testing.txt
pytest>=8,<9
pytest-cov>=5,<7
```

Implement an immutable `ApiTestingSettings` dataclass. Disabled mode must not import or connect to PostgreSQL/Redis during normal UI Agent startup.

- [ ] **Step 4: Add local-only containers and service definitions**

`deploy/api-testing-compose.yml` must use PostgreSQL 16 and Redis 7, persistent named volumes, health checks, restart policies, and `127.0.0.1` port bindings. It must not publish database or Redis ports publicly.

`deploy/midscene-api-worker.service` runs:

```ini
ExecStart=/opt/midscene-task-platform/.venv/bin/celery \
  -A task_server.api_testing.tasks:celery_app worker \
  --loglevel=INFO --queues=api-testing --concurrency=2
```

- [ ] **Step 5: Make deployment idempotent**

`deploy/install-server.sh` must:

1. create `.venv` with `--system-site-packages`
2. install `requirements-api-testing.txt`
3. install but not start the worker when `API_TESTING_ENABLED=0`
4. run `deploy/api-testing-migrate.sh` before restarting enabled services
5. fail before service restart when migration fails
6. copy `api-test/` to each discovered web root

- [ ] **Step 6: Verify GREEN and deployment syntax**

Run:

```bash
python3 -m pytest tests/api_testing/test_config.py -v
bash -n deploy/install-server.sh deploy/api-testing-migrate.sh
python3 tests/backend_static_checks.py
git diff --check
```

Expected: all commands pass; existing services are not started locally.

- [ ] **Step 7: Commit infrastructure**

```bash
git add requirements-api-testing.txt requirements-api-testing-dev.txt deploy/api-testing-compose.yml \
  deploy/api-testing-migrate.sh deploy/midscene-api-worker.service \
  deploy/midscene.env.example deploy/install-server.sh \
  task_server/api_testing/config.py tests/api_testing/test_config.py \
  tests/backend_static_checks.py
git commit -m "Add API testing runtime infrastructure"
```

### Task 2: Database Schema, Migrations, and Secret Boundary

**Files:**
- Create: `task_server/api_testing/db.py`
- Create: `task_server/api_testing/crypto.py`
- Create: `task_server/api_testing/models/base.py`
- Create: `task_server/api_testing/models/project.py`
- Create: `task_server/api_testing/models/source.py`
- Create: `task_server/api_testing/models/environment.py`
- Create: `task_server/api_testing/models/case.py`
- Create: `task_server/api_testing/models/execution.py`
- Create: `task_server/api_testing/migrations/alembic.ini`
- Create: `task_server/api_testing/migrations/env.py`
- Create: `task_server/api_testing/migrations/versions/0001_phase1_schema.py`
- Test: `tests/api_testing/test_crypto.py`
- Test: `tests/api_testing/test_migrations.py`

**Interfaces:**
- Produces: `session_scope() -> Iterator[Session]`
- Produces: `encrypt_secret(plaintext: str) -> str`, `decrypt_secret(ciphertext: str) -> str`, `secret_fingerprint(plaintext: str) -> str`.
- Produces ORM entities for projects, source revisions/endpoints, environment revisions/variables, case versions/baselines, executions/case attempts/events.

- [ ] **Step 1: Write failing crypto and migration tests**

```python
def test_secret_round_trip_does_not_embed_plaintext(secret_key):
    encrypted = encrypt_secret("business-token")
    assert "business-token" not in encrypted
    assert decrypt_secret(encrypted) == "business-token"


def test_upgrade_creates_phase1_tables(postgres_url):
    upgrade_database(postgres_url)
    tables = inspect(create_engine(postgres_url)).get_table_names()
    assert {"api_projects", "api_source_revisions", "api_environments",
            "api_cases", "api_executions"}.issubset(tables)
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest tests/api_testing/test_crypto.py tests/api_testing/test_migrations.py -v`

Expected: missing modules and migrations.

- [ ] **Step 3: Implement SQLAlchemy models and initial migration**

Use UUID strings, UTC timestamps, `owner_id`, audit columns, and explicit foreign keys. Store normalized OpenAPI, request templates, assertions, and sanitized execution payloads in JSONB. Add indexes for project/revision, endpoint method/path, case status, execution state/created time, and event sequence.

- [ ] **Step 4: Implement authenticated encryption**

Derive a Fernet key from `API_TESTING_SECRET_KEY` using SHA-256 and URL-safe base64. Encryption functions accept only non-empty strings. Fingerprints expose the first 12 hex characters of HMAC-SHA256, never a token substring.

- [ ] **Step 5: Verify clean upgrade twice**

Run:

```bash
python3 -m pytest tests/api_testing/test_crypto.py tests/api_testing/test_migrations.py -v
API_TESTING_DATABASE_URL="$TEST_DATABASE_URL" deploy/api-testing-migrate.sh
API_TESTING_DATABASE_URL="$TEST_DATABASE_URL" deploy/api-testing-migrate.sh
```

Expected: tests pass and the second migration is a no-op.

- [ ] **Step 6: Commit schema**

```bash
git add task_server/api_testing/db.py task_server/api_testing/crypto.py \
  task_server/api_testing/models task_server/api_testing/migrations \
  tests/api_testing/test_crypto.py tests/api_testing/test_migrations.py
git commit -m "Add API testing database schema"
```

### Task 3: Manual Source Revisions and Deterministic Diff

**Files:**
- Create: `task_server/api_testing/contracts/source.py`
- Create: `task_server/api_testing/repositories/source_repository.py`
- Create: `task_server/api_testing/adapters/openapi.py`
- Create: `task_server/api_testing/adapters/apifox.py`
- Create: `task_server/api_testing/services/source_service.py`
- Test: `tests/api_testing/fixtures/my_favorites_openapi.json`
- Test: `tests/api_testing/test_source_service.py`

**Interfaces:**
- Produces: `SourceService.preview_refresh(project_id, source_id, document, actor_id) -> SourceRefreshPreview`.
- Produces: `SourceService.activate_preview(preview_id, actor_id) -> SourceRevisionView`.
- Produces stable endpoint key: `sha256(source_id + operation_id + method + normalized_path)`.

- [ ] **Step 1: Write failing source behavior tests**

```python
def test_preview_does_not_replace_active_revision(source_service, project):
    preview = source_service.preview_refresh(project.id, None, FAVORITES_OPENAPI, "admin")
    assert preview.added_count == 3
    assert source_service.get_active_revision(project.id) is None


def test_activation_preserves_old_revision_and_detects_changed_schema(source_service):
    first = activate_fixture(source_service, FAVORITES_OPENAPI)
    preview = source_service.preview_refresh(first.project_id, first.source_id, CHANGED_OPENAPI, "admin")
    assert preview.changed_count == 1
    second = source_service.activate_preview(preview.id, "admin")
    assert second.id != first.id
    assert source_service.get_revision(first.id).status == "superseded"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest tests/api_testing/test_source_service.py -v`

Expected: missing source service.

- [ ] **Step 3: Implement OpenAPI normalization**

Support OpenAPI 3.0/3.1 JSON documents, parameters, request bodies, responses, examples, tags, servers, operation IDs, and security metadata. Reject incomplete documents before writing a revision. Preserve unknown vendor extensions in a namespaced JSON field.

- [ ] **Step 4: Implement Apifox adapter**

The adapter accepts a saved access token and project/branch/environment identifiers, invokes the configured Apifox export/discovery command, and returns OpenAPI plus environment metadata. It never stores CLI output containing the access token and never runs on workspace page load.

- [ ] **Step 5: Implement preview and activation transactions**

Preview writes an expiring candidate record and deterministic diff. Activation writes one immutable source revision and endpoints in a transaction, then marks the previous active revision superseded.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
python3 -m pytest tests/api_testing/test_source_service.py -v
python3 -m py_compile $(find task_server/api_testing -name '*.py' -print)
git diff --check
```

- [ ] **Step 7: Commit source revisions**

```bash
git add task_server/api_testing/contracts/source.py \
  task_server/api_testing/repositories/source_repository.py \
  task_server/api_testing/adapters task_server/api_testing/services/source_service.py \
  tests/api_testing/fixtures tests/api_testing/test_source_service.py
git commit -m "Add versioned API source imports"
```

### Task 4: Editable Environment Revisions and Runtime Resolution

**Files:**
- Create: `task_server/api_testing/contracts/environment.py`
- Create: `task_server/api_testing/repositories/environment_repository.py`
- Create: `task_server/api_testing/services/environment_service.py`
- Test: `tests/api_testing/test_environment_service.py`

**Interfaces:**
- Produces: `EnvironmentService.create_revision(environment_id, payload, secret_updates, actor_id) -> EnvironmentView`.
- Produces: `EnvironmentService.resolve_runtime(environment_revision_id, overrides) -> ResolvedEnvironment`.
- `ResolvedEnvironment` contains base URLs, public variables, plaintext secrets only in memory, and default headers.

- [ ] **Step 1: Write failing environment tests**

```python
def test_imported_environment_is_editable_without_mutating_source(environment_service):
    imported = environment_service.import_from_source(PRODUCTION_ENV, "admin")
    edited = environment_service.create_revision(
        imported.id,
        {"name": "生产环境（腾讯云）", "variables": {"Biz": "ZXB"}},
        {"ZXBToken": BUSINESS_TOKEN},
        "admin",
    )
    assert edited.revision == 2
    assert edited.variables["ZXBToken"].configured is True
    assert BUSINESS_TOKEN not in repr(edited)


def test_runtime_resolves_nested_placeholders_and_rejects_missing_values(environment_service):
    runtime = environment_service.resolve_runtime(ENV_REVISION_ID, {"userId": "135"})
    assert runtime.headers["Authorization"] == f"Bearer {BUSINESS_TOKEN}"
    with pytest.raises(UnresolvedVariableError, match="missingModelId"):
        runtime.render("{{missingModelId}}")
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest tests/api_testing/test_environment_service.py -v`

- [ ] **Step 3: Implement immutable editable revisions**

Persist public values and encrypted secret values separately. Read models return `{name, configured, fingerprint, updated_at}` for secrets. A revision update copies unchanged secret references and replaces only explicit secret updates.

- [ ] **Step 4: Implement strict placeholder resolution**

Resolve `{{name}}` in base URLs, headers, path, query, and JSON string values with cycle detection and a maximum depth of 10. Undefined variables fail preflight before a network request is sent.

- [ ] **Step 5: Verify GREEN and masking**

Run:

```bash
python3 -m pytest tests/api_testing/test_environment_service.py -v
rg -n "phase1-secret-fixture" /tmp/api-testing-test-output && exit 1 || true
```

- [ ] **Step 6: Commit environments**

```bash
git add task_server/api_testing/contracts/environment.py \
  task_server/api_testing/repositories/environment_repository.py \
  task_server/api_testing/services/environment_service.py \
  tests/api_testing/test_environment_service.py
git commit -m "Add editable API environments"
```

### Task 5: Structured Case Drafts, Validation, and Baselines

**Files:**
- Create: `task_server/api_testing/contracts/case.py`
- Create: `task_server/api_testing/repositories/case_repository.py`
- Create: `task_server/api_testing/services/case_service.py`
- Create: `task_server/api_testing/validation.py`
- Test: `tests/api_testing/test_case_service.py`

**Interfaces:**
- Produces: `CaseService.create_draft(endpoint_id, payload, origin, actor_id) -> CaseVersionView`.
- Produces: `validate_case(case_version, endpoint, environment_metadata) -> ValidationResult`.
- Produces: `CaseService.adopt_baseline(case_version_id, debug_execution_case_id, actor_id) -> BaselineView`.

- [ ] **Step 1: Write failing draft and baseline tests**

```python
def test_draft_can_be_saved_before_debug(case_service):
    draft = case_service.create_draft(ENDPOINT_ID, VALID_CASE, "manual", "admin")
    assert draft.status == "draft"


def test_baseline_requires_passing_debug(case_service):
    with pytest.raises(BaselineGateError, match="passing debug"):
        case_service.adopt_baseline(CASE_VERSION_ID, FAILED_DEBUG_ID, "admin")
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest tests/api_testing/test_case_service.py -v`

- [ ] **Step 3: Implement structured contract and validation**

The case contract must include purpose, priority, request parts, data rows, assertions, extraction rules, dependencies, and optional declarative processing. Validate method/path identity, required parameters, JSON types, assertion syntax, extraction target names, unresolved variables, and unsafe URLs.

- [ ] **Step 4: Implement immutable versions and baseline gate**

Every edit creates a new version. Baseline adoption requires a `PASSED` debug case referencing the same case version and environment revision. Adoption records actor, time, and debug evidence.

- [ ] **Step 5: Verify GREEN**

Run: `python3 -m pytest tests/api_testing/test_case_service.py -v`

- [ ] **Step 6: Commit case assets**

```bash
git add task_server/api_testing/contracts/case.py \
  task_server/api_testing/repositories/case_repository.py \
  task_server/api_testing/services/case_service.py \
  task_server/api_testing/validation.py tests/api_testing/test_case_service.py
git commit -m "Add versioned API case drafts"
```

### Task 6: HTTP Executor, Truthful Statuses, Queue, and SSE Events

**Files:**
- Create: `task_server/api_testing/executor.py`
- Create: `task_server/api_testing/assertions.py`
- Create: `task_server/api_testing/events.py`
- Create: `task_server/api_testing/repositories/execution_repository.py`
- Create: `task_server/api_testing/services/execution_service.py`
- Create: `task_server/api_testing/tasks.py`
- Test: `tests/api_testing/test_executor.py`
- Test: `tests/api_testing/test_execution_service.py`

**Interfaces:**
- Produces: `execute_case(case_version_id, environment_revision_id, overrides) -> CaseExecutionResult`.
- Produces: `ExecutionService.submit(request, actor_id, idempotency_key) -> ExecutionView`.
- Produces: `EventStream.append(execution_id, event_type, payload) -> int` and `EventStream.read(execution_id, after_id, block_ms) -> list[ExecutionEvent]`.

- [ ] **Step 1: Write failing executor status tests**

```python
def test_http_200_with_failed_business_assertion_is_failed(executor, target):
    result = executor.run(case_for(target, assertions=[{"json_path": "$.code", "equals": 0}]))
    assert result.status == "FAILED"
    assert result.failure_category == "product_assertion"


def test_missing_environment_variable_is_broken_without_request(executor, target):
    result = executor.run(case_with_body("{{missing}}"))
    assert result.status == "BROKEN"
    assert result.failure_category == "environment"
    assert target.request_count == 0
```

- [ ] **Step 2: Write failing event and idempotency tests**

```python
def test_submit_is_idempotent(execution_service):
    first = execution_service.submit(REQUEST, "admin", "same-key")
    second = execution_service.submit(REQUEST, "admin", "same-key")
    assert second.id == first.id


def test_event_resume_returns_only_new_events(event_stream):
    first = event_stream.append(EXECUTION_ID, "started", {})
    event_stream.append(EXECUTION_ID, "case_finished", {"status": "PASSED"})
    assert [event.type for event in event_stream.read(EXECUTION_ID, first, 0)] == ["case_finished"]
```

- [ ] **Step 3: Run tests and confirm RED**

Run: `python3 -m pytest tests/api_testing/test_executor.py tests/api_testing/test_execution_service.py -v`

- [ ] **Step 4: Implement deterministic request execution**

Use `task_server.core.http_client` or a small adapter around it. Enforce host policy, timeout, response-size limit, secret masking, request timing, JSON/text parsing, extraction, and assertions. Persist sanitized resolved requests and responses.

- [ ] **Step 5: Implement task state machine and Celery task**

Use compare-and-set state transitions. The worker emits started, request, response, assertion, case-finished, and execution-finished events. Worker exceptions finalize the task as `BROKEN`, never `PASSED` or product `FAILED`.

- [ ] **Step 6: Implement Redis event streams and cancellation**

Use one bounded Redis Stream per active execution with a 24-hour expiry. PostgreSQL remains the durable event source. Cancellation writes a database intent and Redis signal; the worker checks it before each request and assertion block.

- [ ] **Step 7: Verify GREEN**

Run:

```bash
python3 -m pytest tests/api_testing/test_executor.py tests/api_testing/test_execution_service.py -v
python3 -m py_compile $(find task_server/api_testing -name '*.py' -print)
```

- [ ] **Step 8: Commit execution engine**

```bash
git add task_server/api_testing/executor.py task_server/api_testing/assertions.py \
  task_server/api_testing/events.py task_server/api_testing/tasks.py \
  task_server/api_testing/repositories/execution_repository.py \
  task_server/api_testing/services/execution_service.py \
  tests/api_testing/test_executor.py tests/api_testing/test_execution_service.py
git commit -m "Add asynchronous API execution engine"
```

### Task 7: AI Case Generation Through the Existing Gateway

**Files:**
- Create: `ai_skills/api_case_generation.v1.md`
- Create: `ai_skills/schemas/api_case_generation.v1.json`
- Create: `task_server/api_testing/services/ai_service.py`
- Create: `task_server/api_testing/repositories/ai_job_repository.py`
- Modify: `ai-gateway/config/model-router.json`
- Test: `tests/api_testing/test_ai_service.py`
- Test: `ai_skills/evals/cases/api_case_generation.json`

**Interfaces:**
- Produces: `AiCaseService.submit(endpoint_ids, environment_revision_id, actor_id) -> AiJobView`.
- Produces validated case drafts through `CaseService.create_draft(..., origin="ai", ...)`.
- Model choice follows the existing Gateway route; requested and actual models are recorded.

- [ ] **Step 1: Write failing AI result validation tests**

```python
def test_ai_output_becomes_drafts_only(ai_service, fake_gateway):
    job = ai_service.submit([FAVORITE_LIST_ENDPOINT], ENVIRONMENT_ID, "admin")
    ai_service.process(job.id)
    drafts = ai_service.list_generated_drafts(job.id)
    assert drafts
    assert {draft.status for draft in drafts} == {"draft"}


def test_invalid_ai_case_is_rejected_without_partial_baseline(ai_service, fake_gateway):
    fake_gateway.return_value = {"cases": [{"request": {"path": "http://metadata/"}}]}
    result = ai_service.process(AI_JOB_ID)
    assert result.status == "failed_validation"
    assert result.baseline_count == 0
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest tests/api_testing/test_ai_service.py -v`

- [ ] **Step 3: Add structured skill and schema**

The prompt receives normalized endpoint contracts, non-secret environment variable names, and user intent. It requests positive, negative, boundary, authorization, and dependency candidates with explicit assertions and data requirements. It must never receive plaintext secrets.

- [ ] **Step 4: Implement asynchronous generation and validation**

Chunk endpoints in bounded batches. Persist every batch state and actual model. Completed batches survive timeout or later-batch failure. Pass every candidate through the deterministic case validator before creating a draft.

- [ ] **Step 5: Verify GREEN and skill evals**

Run:

```bash
python3 -m pytest tests/api_testing/test_ai_service.py -v
python3 ai_skills/evals/run_skill_evals.py
python3 tests/ai_gateway_static_checks.py
node tests/ai_gateway_catalog_checks.mjs
```

- [ ] **Step 6: Commit AI generation**

```bash
git add ai_skills/api_case_generation.v1.md ai_skills/schemas/api_case_generation.v1.json \
  ai_skills/evals/cases/api_case_generation.json ai-gateway/config/model-router.json \
  task_server/api_testing/services/ai_service.py \
  task_server/api_testing/repositories/ai_job_repository.py \
  tests/api_testing/test_ai_service.py
git commit -m "Add AI-assisted API case drafts"
```

### Task 8: Authenticated HTTP and SSE API Boundary

**Files:**
- Create: `task_server/api_testing/http.py`
- Create: `task_server/api_testing/routes.py`
- Modify: `task_server/router.py`
- Modify: `task_server/app.py`
- Test: `tests/api_testing/test_http_contract.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- Produces `/api/api-testing/v1/*` JSON resources and `/api/api-testing/v1/executions/{id}/events` SSE.
- Consumes existing bearer session and maps the current user to reserved `owner_id` and audit actor fields.
- Produces error envelope `{ok: false, error: {code, message, details}, request_id}`.

- [ ] **Step 1: Write failing route-boundary tests**

```python
def test_api_routes_require_existing_session(http_client):
    response = http_client.get("/api/api-testing/v1/projects")
    assert response.status == 401


def test_router_registers_only_one_api_testing_prefix():
    source = Path("task_server/router.py").read_text()
    assert source.count("register_api_testing_routes(") == 1
    assert "api_testing.services" not in source
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest tests/api_testing/test_http_contract.py -v`

- [ ] **Step 3: Implement resource dispatch and public contracts**

Register one GET, POST, and DELETE prefix handler through `register_api_testing_routes`. Route internally by path segments in `http.py`, require authorization before body parsing, validate UUIDs and payload size, and map domain exceptions to stable status codes.

- [ ] **Step 4: Implement SSE response support**

Add a narrow response helper that sends `text/event-stream`, `Cache-Control: no-cache`, and event identifiers. Resume from `Last-Event-ID`, send heartbeats every 15 seconds, and terminate after completion or client disconnect.

- [ ] **Step 5: Verify GREEN and router size discipline**

Run:

```bash
python3 -m pytest tests/api_testing/test_http_contract.py -v
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/router.py task_server/app.py $(find task_server/api_testing -name '*.py' -print)
git diff --check
```

- [ ] **Step 6: Commit API boundary**

```bash
git add task_server/api_testing/http.py task_server/api_testing/routes.py \
  task_server/router.py task_server/app.py tests/api_testing/test_http_contract.py \
  tests/backend_static_checks.py
git commit -m "Expose API testing module endpoints"
```

### Task 9: Vue Application Shell and Saved Workspace Context

**Files:**
- Create: `api-testing-ui/package.json`
- Create: `api-testing-ui/vite.config.ts`
- Create: `api-testing-ui/tsconfig.json`
- Create: `api-testing-ui/src/main.ts`
- Create: `api-testing-ui/src/App.vue`
- Create: `api-testing-ui/src/router.ts`
- Create: `api-testing-ui/src/api/client.ts`
- Create: `api-testing-ui/src/api/contracts.ts`
- Create: `api-testing-ui/src/stores/context.ts`
- Create: `api-testing-ui/src/styles/tokens.css`
- Create: `api-testing-ui/src/styles/app.css`
- Create: `api-testing-ui/src/views/WorkbenchView.vue`
- Create: `api-testing-ui/src/views/AssetsView.vue`
- Create: `api-testing-ui/src/views/RunsView.vue`
- Create: `api-testing-ui/src/views/ReportsView.vue`
- Create: `api-testing-ui/src/views/SettingsView.vue`
- Test: `api-testing-ui/src/stores/context.spec.ts`
- Modify: `task-manager.html`
- Modify: `tests/frontend_static_checks.py`

**Interfaces:**
- Produces same-tab application at `/api-test/`.
- Consumes `sessionStorage.sessionToken` and redirects to `/task-manager.html` when absent or rejected.
- Context store exposes `projectId`, `sourceRevisionId`, `environmentRevisionId`, and `loadSavedContext()`.

- [ ] **Step 1: Write failing context-store tests**

```typescript
it('loads the saved server context without starting a source refresh', async () => {
  const api = createFakeApi({ workspace: SAVED_WORKSPACE })
  const store = useContextStore()
  await store.loadSavedContext(api)
  expect(store.projectId).toBe(SAVED_WORKSPACE.project.id)
  expect(api.calls).toEqual(['/api/api-testing/v1/workspace'])
})
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `npm --prefix api-testing-ui test -- --run src/stores/context.spec.ts`

Expected: package or store is missing.

- [ ] **Step 3: Create the isolated Vue application**

Use Vue Router routes `/`, `/assets`, `/runs`, `/reports`, and `/settings`. Keep one compact rail with Chinese labels and Lucide icons. Do not introduce marketing sections, nested cards, text badges pretending to be icons, or viewport-scaled type.

- [ ] **Step 4: Implement auth and typed client**

Attach `Authorization: Bearer <sessionToken>` to API requests. On 401, clear only invalid session values and navigate to the existing login page with a `return_to=/api-test/` query. Never put business environment tokens into web storage.

- [ ] **Step 5: Add one main-platform navigation link**

The existing sidebar gets one `API 测试` link using a Lucide-compatible icon or existing icon system. It navigates in the same tab so same-origin session storage remains available.

- [ ] **Step 6: Verify GREEN, build, and static integration**

Run:

```bash
npm --prefix api-testing-ui install
npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
python3 tests/frontend_static_checks.py
git diff --check
```

- [ ] **Step 7: Commit frontend shell**

```bash
git add api-testing-ui api-test task-manager.html tests/frontend_static_checks.py
git commit -m "Add API testing Vue workspace"
```

### Task 10: Endpoint Selection, Case Editor, AI Drafts, and Debug Drawer

**Files:**
- Create: `api-testing-ui/src/stores/assets.ts`
- Create: `api-testing-ui/src/stores/cases.ts`
- Create: `api-testing-ui/src/components/ContextBar.vue`
- Create: `api-testing-ui/src/components/EndpointTree.vue`
- Create: `api-testing-ui/src/components/EndpointDetail.vue`
- Create: `api-testing-ui/src/components/CaseEditor.vue`
- Create: `api-testing-ui/src/components/AiAssistant.vue`
- Create: `api-testing-ui/src/components/DebugDrawer.vue`
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Test: `api-testing-ui/src/components/EndpointTree.spec.ts`
- Test: `api-testing-ui/src/components/CaseEditor.spec.ts`
- Test: `api-testing-ui/src/components/DebugDrawer.spec.ts`

**Interfaces:**
- Endpoint selection emits stable endpoint IDs.
- Case editor consumes and emits the public `CaseDraft` contract.
- Debug drawer submits selected case version IDs and one environment revision ID.

- [ ] **Step 1: Write failing component behavior tests**

```typescript
it('keeps endpoint selection while filtering the tree', async () => {
  const wrapper = mount(EndpointTree, { props: { endpoints: FAVORITES } })
  await wrapper.find('[data-testid="endpoint-1"]').setValue(true)
  await wrapper.find('[data-testid="endpoint-search"]').setValue('删除收藏')
  expect(wrapper.emitted('selection-change')?.at(-1)?.[0]).toEqual(['endpoint-1'])
})


it('debugs a draft without requiring baseline adoption', async () => {
  const wrapper = mount(DebugDrawer, { props: { caseVersionId: 'draft-1' } })
  await wrapper.find('[data-testid="debug-send"]').trigger('click')
  expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({ caseVersionIds: ['draft-1'] })
})
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `npm --prefix api-testing-ui test -- --run src/components`

- [ ] **Step 3: Implement the three-column work surface**

Left: searchable module/endpoint tree with selected count. Center: endpoint tabs and structured case editor. Right: collapsible AI assistant with analysis, generate, assertion, and failure actions. Provide explicit loading, empty, partial, failed, and saved states.

- [ ] **Step 4: Implement structured case editing**

Render headers, query, path, cookies, body, data rows, extraction, and assertions as editable sections. Raw JSON is a secondary tab. Display validation errors next to the affected field and preserve unsaved edits across tab changes.

- [ ] **Step 5: Implement AI job feedback and debug drawer**

Show queued/running/completed/partial/failed AI batches with model name and retry. Debug results show resolved request, sanitized response, assertions, failure category, logs, and `采纳为基线` only after a passing result.

- [ ] **Step 6: Verify GREEN and responsive layout**

Run:

```bash
npm --prefix api-testing-ui test -- --run src/components
npm --prefix api-testing-ui run build
node tests/visual_smoke_check.js
```

- [ ] **Step 7: Commit design and debug flow**

```bash
git add api-testing-ui/src/stores api-testing-ui/src/components \
  api-testing-ui/src/views/WorkbenchView.vue api-test
git commit -m "Add API case design and debugging flow"
```

### Task 11: Stable Live Console and Truthful Report UI

**Files:**
- Create: `api-testing-ui/src/stores/executions.ts`
- Create: `api-testing-ui/src/components/ExecutionConsole.vue`
- Create: `api-testing-ui/src/components/ExecutionLog.vue`
- Create: `api-testing-ui/src/components/CaseResultList.vue`
- Create: `api-testing-ui/src/components/ExecutionDetailDrawer.vue`
- Create: `api-testing-ui/src/components/ReportSummary.vue`
- Create: `api-testing-ui/src/components/FailureAnalysis.vue`
- Modify: `api-testing-ui/src/views/RunsView.vue`
- Modify: `api-testing-ui/src/views/ReportsView.vue`
- Test: `api-testing-ui/src/components/ExecutionLog.spec.ts`
- Test: `api-testing-ui/src/components/ReportSummary.spec.ts`

**Interfaces:**
- Execution store uses `EventSource` with `Last-Event-ID` recovery and does not replace existing events.
- Report summary consumes distinct execution status and failure category fields.

- [ ] **Step 1: Write failing stable-log tests**

```typescript
it('appends SSE events without resetting user scroll', async () => {
  const wrapper = mount(ExecutionLog, { props: { events: [EVENT_1], followLatest: false } })
  wrapper.element.scrollTop = 20
  await wrapper.setProps({ events: [EVENT_1, EVENT_2] })
  expect(wrapper.element.scrollTop).toBe(20)
})


it('does not count broken as product failed', () => {
  const wrapper = mount(ReportSummary, { props: { results: [PASSED, FAILED, BROKEN] } })
  expect(wrapper.get('[data-testid="failed-count"]').text()).toBe('1')
  expect(wrapper.get('[data-testid="broken-count"]').text()).toBe('1')
})
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `npm --prefix api-testing-ui test -- --run src/components/ExecutionLog.spec.ts src/components/ReportSummary.spec.ts`

- [ ] **Step 3: Implement resumable realtime console**

Append events by monotonic ID, deduplicate reconnects, maintain follow/pause state, and provide level and case filters. Completed executions stop reconnecting. A disconnected stream shows a compact reconnect state without clearing existing lines.

- [ ] **Step 4: Implement report and failure analysis**

Show totals, duration, environment name, case rows, request/response drawer, assertion details, failure categories, AI analysis evidence, edit-case action, rerun-failed action, and previous baseline comparison placeholder only when comparison data exists.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
python3 tests/frontend_static_checks.py
```

- [ ] **Step 6: Commit execution console and reports**

```bash
git add api-testing-ui/src/stores/executions.ts api-testing-ui/src/components \
  api-testing-ui/src/views/RunsView.vue api-testing-ui/src/views/ReportsView.vue \
  api-test tests/frontend_static_checks.py
git commit -m "Add live API execution reports"
```

### Task 12: End-to-End `我的收藏` Acceptance and Deployment Gate

**Files:**
- Create: `tests/api_testing_e2e.spec.mjs`
- Create: `tests/fixtures/api-testing/favorites-target.mjs`
- Modify: `package.json`
- Modify: `tests/backend_static_checks.py`
- Modify: `tests/frontend_static_checks.py`
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Produces `npm run test:api-testing` for backend, frontend, migration, worker, and browser acceptance.
- Proves the exact Phase 1 user workflow with three `我的收藏` endpoints.

- [ ] **Step 1: Write the failing browser acceptance**

The Playwright test must:

1. log in through the existing platform
2. enter `API 测试`
3. import the three-endpoint fixture and save a source revision
4. create `生产环境（腾讯云）` and configure `Biz` plus a masked `ZXBToken`
5. select all three `我的收藏` endpoints
6. generate AI drafts from deterministic Gateway fixtures
7. edit one assertion
8. debug selected cases without baseline adoption
9. adopt passing cases as baselines
10. execute the baseline set
11. observe SSE events without a full page reload
12. verify passed, failed, and broken counters from real execution records
13. verify the token never appears in DOM, logs, screenshots, or report JSON

- [ ] **Step 2: Run acceptance and confirm RED**

Run: `npx playwright test tests/api_testing_e2e.spec.mjs --project=chromium`

Expected: missing fixture server or incomplete workflow fails at the first unmet step.

- [ ] **Step 3: Add deterministic target and test orchestration**

The fixture server exposes success, business-failure, and network/timeout behaviors. The test starts disposable PostgreSQL and Redis, applies migrations, starts the Task server and Celery worker on test ports, and tears them down without touching production data.

- [ ] **Step 4: Run the complete Phase 1 gate**

Run:

```bash
npm run test:api-testing
npm run test:static
npm run test:visual
git diff --check
```

Expected: all focused API tests and existing platform checks pass.

- [ ] **Step 5: Perform manual visual verification**

Use Playwright screenshots at 1440x900 and 390x844. Verify:

- project/revision/environment context is visible without technical IDs
- three-column layout does not overlap
- long paths and Chinese case names wrap or truncate with tooltips
- the right AI panel can collapse
- the log remains readable while events arrive
- no secret appears
- the mobile view becomes a deliberate stacked/tabbed workflow rather than squeezed columns

- [ ] **Step 6: Update operational state**

Add a dated `CODEX_STATE.md` section containing:

- Phase 1 behavior delivered
- migration and deployment commands
- health/readiness results
- exact automated test commands and counts
- browser acceptance evidence
- known Phase 2 boundary

- [ ] **Step 7: Commit the Phase 1 gate**

```bash
git add tests/api_testing_e2e.spec.mjs tests/fixtures/api-testing package.json \
  tests/backend_static_checks.py tests/frontend_static_checks.py CODEX_STATE.md
git commit -m "Verify API testing Phase 1 workflow"
```

---

## Final Phase 1 Verification

After all task commits, run from a clean worktree:

```bash
docker compose -f deploy/api-testing-compose.yml up -d
API_TESTING_ENABLED=1 deploy/api-testing-migrate.sh
npm run test:api-testing
npm run test:static
npm run test:visual
git status --short
```

Expected:

- PostgreSQL and Redis health checks pass.
- The Task server, API worker, and Vue application start.
- The three `我的收藏` endpoints complete the full workflow.
- No secret appears in logs, DOM, screenshots, reports, or test artifacts.
- Existing UI Agent, Runner, Sonic, and YAML checks pass.
- Only intentional feature files and the user-owned pre-existing dirty files appear in status.

## Deferred to Separate Plans

The following are required by the approved design but intentionally receive separate implementation plans after Phase 1 is stable:

- Phase 2: suite DAGs, extraction across cases, batch orchestration, contract-drift impact analysis, and complete version comparison.
- Phase 3: multi-worker leases, schedules, notifications, retention, and trends.
- Phase 4: mock rules, performance smoke profiles, security-oriented contract checks, and quality gates.
- Phase 5: project roles, multi-user administration, shared dashboards, and CI/CD triggers.
