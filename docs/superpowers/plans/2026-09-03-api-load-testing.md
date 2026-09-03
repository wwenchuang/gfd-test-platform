# API Load Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add production-grade single-interface and ordered-workflow load testing to the existing API platform through isolated, priority-aware Docker k6 agents, deterministic reports, and evidence-grounded AI diagnosis.

**Architecture:** Add a separate load-testing domain beside the existing functional execution domain. The platform owns immutable scenarios, scheduling, access control, state, aggregation, reports, and AI analysis; outbound-only Docker agents own k6 processes and return idempotent five-second metric buckets. The first release supports one or multiple agents without allowing remote agents to connect directly to PostgreSQL or Redis.

**Tech Stack:** Python 3, SQLAlchemy 2, Alembic, PostgreSQL 16, Redis 7, k6 Docker image, Vue 3, Pinia, TypeScript, Vitest, Playwright, existing SSE and AI Gateway patterns.

## Global Constraints

- Do not route load traffic through `ExecutionService` or create one database row per HTTP request.
- Do not broadly refactor `router.py`; mount load-testing dispatch through the existing API-testing boundary.
- All configured environments are selectable. Production runs require existing `api.production` plus the new load-execution permission and explicit risk confirmation.
- Supported first-release k6 executors are `constant-vus`, `ramping-vus`, `constant-arrival-rate`, and `ramping-arrival-rate`; their requested load and actual achieved load are reported separately.
- Every user-visible protocol term has a Chinese name and short explanation. For example, show `固定并发（constant-vus）` and explain when to use it; node tier, task priority, threshold, state, and capacity fields must also explain purpose and risk in Chinese instead of exposing unexplained English values.
- The platform host defaults to `fallback`; automatic allocation excludes fallback agents unless the run explicitly allows them.
- Agent hard limits always win over platform soft limits and task requests.
- Remote agents use platform HTTP APIs only; they never connect directly to Redis or PostgreSQL.
- Public HTTP transport must not carry environment secrets. Remote secret delivery requires HTTPS or explicitly configured controlled private transport.
- A run verdict is `passed` only when requested load is reached and all required thresholds pass. Unreached load or lost shards is `inconclusive`.
- HTTP status, business assertion, workflow iteration, dropped-iteration, and load-generator failures remain separate.
- Real printing, device control, payment, SMS, deletion of non-owned data, and high-cost AI calls remain blocked from load-scenario admission in the first release.
- AI receives a redacted deterministic evidence package; AI failure never removes or changes the deterministic report.
- Every production behavior follows RED → GREEN → focused regression → integrated gate.
- The user deploys the main platform and additional agent servers. Implementation may create scripts and images but must not deploy or restart remote hosts.

---

## Delivery Slice 1: Durable Domain and Agent Control Plane

### Task 1: Load-Testing Models, Migration, and Repository

**Files:**
- Create: `task_server/api_testing/models/load_testing.py`
- Create: `task_server/api_testing/repositories/load_testing_repository.py`
- Create: `task_server/api_testing/migrations/versions/0010_load_testing.py`
- Modify: `task_server/api_testing/models/__init__.py`
- Modify: `task_server/api_testing/repositories/__init__.py`
- Create: `tests/api_testing/test_load_testing_repository.py`

**Interfaces:**
- Produces ORM records `ApiLoadAgent`, `ApiLoadAgentEnrollment`, `ApiLoadScenario`, `ApiLoadScenarioVersion`, `ApiLoadDataset`, `ApiLoadRun`, `ApiLoadRunShard`, `ApiLoadMetricBucket`, `ApiLoadSample`, `ApiLoadEvent`, and `ApiLoadAiAnalysis`.
- Produces `LoadTestingRepository` methods used by all later tasks.
- Metric idempotency key is `(run_id, shard_id, scenario_step_id, bucket_started_at)`.

- [x] **Step 1: Write failing repository tests**

Add tests that create a project/environment, persist an immutable scenario version, create a run and two shards, insert the same metric bucket twice, and assert one stored bucket with the replacement payload. Add a transition test that rejects `finished -> running` and a retention test that caps samples per `(run, shard, step, kind)`.

```python
def test_metric_bucket_upsert_is_idempotent(load_repository, load_run_with_shard):
    repository = load_repository
    run, shard = load_run_with_shard
    repository.upsert_metric_bucket(run.id, shard.id, "search", "2026-09-03T10:00:00Z", {"requests": 10})
    repository.upsert_metric_bucket(run.id, shard.id, "search", "2026-09-03T10:00:00Z", {"requests": 12})
    assert repository.list_metric_buckets(run.id)[0].metrics["requests"] == 12
    assert len(repository.list_metric_buckets(run.id)) == 1
```

- [x] **Step 2: Run the tests and observe the missing module failure**

Run:

```bash
TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene@127.0.0.1:5432/midscene_api_testing' \
  .venv/bin/python -m pytest tests/api_testing/test_load_testing_repository.py -q
```

Expected: collection fails because `models.load_testing` and `LoadTestingRepository` do not exist.

- [x] **Step 3: Add normalized models and migration**

Use existing `PrimaryRecord` fields. Keep immutable scenario definitions in `ApiLoadScenarioVersion.definition` JSONB, run configuration in `ApiLoadRun.configuration` JSONB, summaries in JSONB, and indexed state/time columns. Store dataset metadata only; filesystem/object content uses `storage_ref`. Add foreign-key delete behavior exactly as specified by the design.

```python
class ApiLoadMetricBucket(PrimaryRecord, Base):
    __tablename__ = "api_load_metric_buckets"
    __table_args__ = (
        UniqueConstraint("run_id", "shard_id", "scenario_step_id", "bucket_started_at"),
        Index("ix_api_load_metric_buckets_run_time", "run_id", "bucket_started_at"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("api_load_runs.id", ondelete="CASCADE"))
    shard_id: Mapped[str] = mapped_column(ForeignKey("api_load_run_shards.id", ondelete="CASCADE"))
    scenario_step_id: Mapped[str] = mapped_column(String(120), server_default="")
    bucket_started_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
```

- [x] **Step 4: Implement repository state and idempotency methods**

Define and use these exact signatures:

```python
class LoadTestingRepository:
    def create_scenario(self, project_id: str, name: str, scenario_type: str, actor_id: str): ...
    def create_scenario_version(self, scenario_id: str, definition: dict, compiler_version: str, actor_id: str): ...
    def create_run(self, scenario_version_id: str, environment_revision_id: str, configuration: dict, actor_id: str): ...
    def create_shard(self, run_id: str, agent_id: str, sequence: int, allocation: dict, actor_id: str): ...
    def transition_run(self, run_id: str, expected: tuple[str, ...], target: str, *, summary: dict | None = None): ...
    def upsert_metric_bucket(self, run_id: str, shard_id: str, step_id: str, started_at: object, metrics: dict): ...
    def append_event(self, run_id: str, event_type: str, payload: dict): ...
    def append_bounded_sample(self, run_id: str, shard_id: str, step_id: str, kind: str, payload: dict, limit: int = 20): ...
```

- [x] **Step 5: Run migration and repository tests**

Expected: migration upgrades and downgrades cleanly; focused tests pass with no duplicate bucket or invalid transition.

- [x] **Step 6: Commit the domain foundation**

```bash
git add task_server/api_testing/models task_server/api_testing/repositories \
  task_server/api_testing/migrations/versions/0010_load_testing.py \
  tests/api_testing/test_load_testing_repository.py
git commit -m "feat(load): add durable load testing domain"
```

### Task 2: Load Permissions and Agent Enrollment

**Files:**
- Modify: `task_server/identity.py`
- Modify: `js/identity-management.js`
- Modify: `task_server/api_testing/access.py`
- Create: `task_server/api_testing/services/load_agent_service.py`
- Create: `tests/api_testing/test_load_agent_service.py`
- Modify: `tests/test_identity.py`
- Modify: `tests/identity_frontend_check.js`

**Interfaces:**
- Produces permissions `api.loadtest.view`, `api.loadtest.edit`, `api.loadtest.execute`, `api.loadtest.manage_agents`.
- Produces `LoadAgentService.create_enrollment()`, `register()`, `authenticate()`, `heartbeat()`, and `update_agent()`.
- Agent credentials are random 32-byte tokens stored only as SHA-256 hashes.

- [x] **Step 1: Write failing identity and agent lifecycle tests**

Cover permission prerequisites, delegated role constraints, one-time enrollment, expiry, replay rejection, credential revocation, heartbeat capacity validation, and prohibition on soft limits above hard limits.

```python
def test_enrollment_is_one_time_and_agent_secret_is_hashed(session_factory):
    service = LoadAgentService(session_factory, now=lambda: FIXED_NOW)
    enrollment = service.create_enrollment({"name": "load-01", "scheduling_tier": "preferred"}, "admin")
    registration = service.register(enrollment.token, AGENT_CAPABILITIES)
    assert service.register(enrollment.token, AGENT_CAPABILITIES).code == "enrollment_used"
    assert registration.secret not in service.debug_persisted_values()
```

- [x] **Step 2: Run focused tests and verify expected failures**

Expected: unknown permission and missing service failures.

- [x] **Step 3: Add permission catalog entries and prerequisites**

Use:

```python
"api.loadtest.view": ("api.view",)
"api.loadtest.edit": ("api.view", "api.loadtest.view")
"api.loadtest.execute": ("api.view", "api.execute", "api.loadtest.view")
"api.loadtest.manage_agents": ("api.view", "api.loadtest.view")
```

Super administrators receive all permissions through the existing catalog. Preset tester roles do not automatically receive Agent-management permission.

- [x] **Step 4: Implement enrollment and heartbeat validation**

Accept exact tiers `preferred`, `normal`, `fallback`, and `disabled`. Validate positive hard/soft `max_vus`, `max_iterations_per_second`, `max_duration_seconds`, CPU and memory values. Heartbeat never accepts platform ownership or project scope from the Agent.

- [x] **Step 5: Run identity, agent, and frontend permission tests**

```bash
.venv/bin/python -m pytest tests/test_identity.py tests/api_testing/test_load_agent_service.py -q
node tests/identity_frontend_check.js
```

- [x] **Step 6: Commit access and enrollment**

```bash
git add task_server/identity.py task_server/api_testing/access.py \
  task_server/api_testing/services/load_agent_service.py js/identity-management.js \
  tests/test_identity.py tests/api_testing/test_load_agent_service.py tests/identity_frontend_check.js
git commit -m "feat(load): add agent enrollment and permissions"
```

### Task 3: Agent HTTP Protocol

**Files:**
- Create: `task_server/api_testing/load_agent_http.py`
- Create: `tests/api_testing/test_load_agent_http.py`
- Modify: `task_server/api_testing/http.py`
- Modify: `task_server/api_testing/routes.py`

**Interfaces:**
- Mounts `/api/api-testing/load-agent/v1/*` beside user-facing `/api/api-testing/v1/*`.
- Agent authentication uses `Authorization: Agent <token>` and never accepts browser session tokens.
- Produces register, heartbeat, claim, command, started, metric, sample, event, and finish endpoints.

- [x] **Step 1: Write failing HTTP boundary tests**

Exercise missing/expired/revoked Agent tokens, browser-token rejection, registration replay, payload size limits, shard ownership, duplicate metric upload, and cross-Agent finish rejection.

- [x] **Step 2: Run the HTTP tests and verify missing dispatch**

```bash
.venv/bin/python -m pytest tests/api_testing/test_load_agent_http.py -q
```

- [x] **Step 3: Implement a narrow Agent dispatcher**

```python
AGENT_API_PREFIX = "/api/api-testing/load-agent/v1"

def dispatch_load_agent_request(handler, method: str, path: str, query: dict) -> bool:
    """Return True only when an Agent route was matched and answered."""
```

Keep request parsing, response envelopes and size limits consistent with `http.py`, but do not add Agent branches throughout the existing large dispatcher.

- [x] **Step 4: Wire service operations and idempotency**

Metric uploads use a client batch ID plus the database unique bucket key. `claim` returns at most one shard. `commands` returns `stop` only for the authenticated Agent's current shard.

- [x] **Step 5: Run focused tests and backend static checks**

```bash
.venv/bin/python -m pytest tests/api_testing/test_load_agent_http.py tests/api_testing/test_load_agent_service.py -q
python3 tests/backend_static_checks.py
```

- [x] **Step 6: Commit Agent protocol**

```bash
git add task_server/api_testing/load_agent_http.py task_server/api_testing/http.py \
  task_server/api_testing/routes.py tests/api_testing/test_load_agent_http.py
git commit -m "feat(load): expose isolated load agent protocol"
```

---

## Delivery Slice 2: Scenario Compilation and Load Orchestration

### Task 4: Scenario Contract, Admission, and k6 Compiler

**Files:**
- Create: `task_server/api_testing/contracts/load_testing.py`
- Create: `task_server/api_testing/services/load_scenario_service.py`
- Create: `task_server/api_testing/services/load_scenario_compiler.py`
- Create: `tests/api_testing/test_load_scenario_service.py`
- Create: `tests/api_testing/test_load_scenario_compiler.py`

**Interfaces:**
- Consumes existing source endpoints, environment-variable references, case versions, assertions and extraction contracts.
- Produces immutable definitions with `mode`, `steps`, `dataset_contract`, `risk`, and `source_snapshot`.
- `compile_scenario(definition: dict, workload: dict) -> CompiledLoadScenario` returns script text, non-secret k6 options, SHA-256 and step manifest.

- [x] **Step 1: Write failing admission and compiler tests**

Cover a GET endpoint, an ordered login/search/detail chain, JSONPath extraction, business-code assertion, CSV variable, Unicode payload, request-name tags, fixed VU and fixed arrival-rate output. Prove real-print, payment, SMS, device-control, high-cost AI, missing cleanup and unknown k6 action cases are rejected with Chinese remedies.

```python
def test_compiler_separates_transport_business_and_iteration_checks():
    compiled = compile_scenario(SEARCH_CHAIN, FIXED_RATE)
    assert 'check(response, {"HTTP状态符合预期"' in compiled.script
    assert '业务断言：$.code' in compiled.script
    assert 'workflow_iteration_success' in compiled.script
    assert "constant-arrival-rate" in compiled.script
```

- [x] **Step 2: Run compiler tests and verify missing behavior**

Expected: missing modules/functions.

- [x] **Step 3: Implement strict definition parsing**

Reject unknown fields. Use exact scope names `setup_once`, `agent_setup`, `vu_once`, `iteration`, `cleanup_once`. Restrict generated k6 behavior to HTTP requests, bounded sleep, checks, variable extraction and data selection; do not accept arbitrary JavaScript.

- [x] **Step 4: Implement case-copy and safety policy**

Copy the full source version snapshot. Return structured issues:

```python
{"level": "error", "code": "hardware_action_blocked", "step_id": "create-print", "message": "真实打印不能进入压测场景"}
```

- [x] **Step 5: Implement deterministic k6 generation**

Generate stable scripts from sorted/canonical input so the same definition has the same hash. Environment secrets remain `__ENV` references and are absent from source text.

- [x] **Step 6: Run focused tests and scan generated fixtures for secrets**

```bash
.venv/bin/python -m pytest tests/api_testing/test_load_scenario_service.py \
  tests/api_testing/test_load_scenario_compiler.py -q
rg -n "Authorization: Bearer|Cookie:|password" tests/artifacts/load-testing || true
```

Expected: tests pass; secret scan prints no generated credential values.

- [x] **Step 7: Commit scenario compilation**

```bash
git add task_server/api_testing/contracts/load_testing.py \
  task_server/api_testing/services/load_scenario_service.py \
  task_server/api_testing/services/load_scenario_compiler.py tests/api_testing/test_load_scenario_*.py
git commit -m "feat(load): compile safe API scenarios for k6"
```

### Task 5: Dataset Storage and Deterministic Sharding

**Files:**
- Create: `task_server/api_testing/services/load_dataset_service.py`
- Create: `task_server/api_testing/services/load_allocator.py`
- Create: `tests/api_testing/test_load_dataset_service.py`
- Create: `tests/api_testing/test_load_allocator.py`

**Interfaces:**
- `LoadDatasetService.import_bytes(project_id, name, filename, content, mode, actor_id)` validates and stores CSV/JSON under a configured private directory.
- `allocate_run(workload, agents, allow_fallback) -> tuple[ShardAllocation, ...]` returns exact non-overlapping fractions and dataset ranges.

- [x] **Step 1: Write failing dataset and allocation tests**

Cover UTF-8 Chinese values, duplicate headers, inconsistent rows, oversized uploads, path traversal, cycle/fixed/exclusive modes, two-Agent weighted splits, fallback exclusion, insufficient capacity and integer rounding whose total exactly equals requested rate.

```python
def test_allocator_never_uses_fallback_without_opt_in():
    allocations = allocate_run({"rate": 5000}, [PREFERRED_4000, FALLBACK_2000], False)
    assert [item.agent_id for item in allocations] == [PREFERRED_4000.id]
    assert allocations[0].capacity_shortfall == 1000
```

- [x] **Step 2: Run tests and verify missing services**

- [x] **Step 3: Implement private dataset storage**

Use a non-public root configured by `API_LOAD_DATA_DIR`, file mode `0600`, generated filenames and content SHA-256. Previews return field names and redacted sample cells only.

- [x] **Step 4: Implement tiered capacity allocation**

Allocate `preferred`, then `normal`, then opted-in `fallback`, using available soft capacity bounded by hard capacity. Return shortfall instead of silently exceeding a node.

Only Agents with a current successful local calibration are eligible. Effective capacity is the minimum of reported hard limits, platform soft limits, and calibrated sustainable limits; an Agent/k6 version or hardware-signature change invalidates calibration.

- [x] **Step 5: Implement deterministic data ranges**

Exclusive rows are partitioned without overlap. Fixed-per-VU mode fails preflight when rows are fewer than allocated VUs. Cycle mode allows repeats and records the fact in the run snapshot.

- [x] **Step 6: Run focused tests and commit**

```bash
.venv/bin/python -m pytest tests/api_testing/test_load_dataset_service.py \
  tests/api_testing/test_load_allocator.py -q
git add task_server/api_testing/services/load_dataset_service.py \
  task_server/api_testing/services/load_allocator.py tests/api_testing/test_load_allocator.py \
  tests/api_testing/test_load_dataset_service.py
git commit -m "feat(load): shard load capacity and datasets safely"
```

### Task 6: Preflight and Run Orchestration

**Files:**
- Create: `task_server/api_testing/services/load_run_service.py`
- Create: `task_server/api_testing/services/load_preflight_service.py`
- Create: `tests/api_testing/test_load_run_service.py`
- Create: `tests/api_testing/test_load_preflight_service.py`
- Modify: `task_server/api_testing/config.py`

**Interfaces:**
- `LoadRunService.create()`, `preflight()`, `start()`, `stop()`, `claim_shard()`, `finish_shard()`, and `recover_stale_runs()`.
- `LoadPreflightService.run_once()` executes exactly one iteration without k6 load and returns validation, observed duration, cleanup result and capacity estimate.

- [x] **Step 1: Write failing state-machine and preflight tests**

Cover project/environment authorization, production permission, duplicate start, preflight failure, capacity shortfall, explicit run-anyway yielding `inconclusive`, uncalibrated/expired/version-mismatched Agent hard blocking, per-Agent target connectivity, all-Agent start barrier, stop before start, stop while running, one lost shard and recovery after process restart.

- [x] **Step 2: Run tests and confirm missing services**

- [x] **Step 3: Implement preflight using isolated functional request primitives**

Reuse low-volume request resolution/assertion helpers, not `ExecutionService` persistence. Capture only preflight evidence and always attempt owned cleanup.

- [x] **Step 4: Implement run creation and snapshots**

Freeze scenario version, environment revision ID/name, workload, thresholds, allocation policy, compiler hash and selected Agent capabilities in `ApiLoadRun.configuration`.

Require a valid local calibration for every selected Agent. Store calibration ID/time/signature and calibrated VU/rate limits in the run snapshot, then compare the scenario preflight duration with those limits to estimate required VUs.

- [x] **Step 5: Implement start, stop, and stale recovery**

Use compare-and-swap state transitions. Start becomes `running` only after assigned Agents acknowledge; failure to reach the start barrier ends as `failed` without partial hidden load. Stop creates durable commands read by Agent long polling.

- [x] **Step 6: Run service tests and commit**

```bash
.venv/bin/python -m pytest tests/api_testing/test_load_run_service.py \
  tests/api_testing/test_load_preflight_service.py -q
git add task_server/api_testing/services/load_run_service.py \
  task_server/api_testing/services/load_preflight_service.py \
  task_server/api_testing/config.py tests/api_testing/test_load_run_service.py \
  tests/api_testing/test_load_preflight_service.py
git commit -m "feat(load): orchestrate preflight and distributed runs"
```

---

## Delivery Slice 3: Docker Agent Runtime and Metrics

### Task 7: Docker Agent Client and k6 Process Lifecycle

**Files:**
- Create: `load_agent/__init__.py`
- Create: `load_agent/config.py`
- Create: `load_agent/client.py`
- Create: `load_agent/runtime.py`
- Create: `load_agent/k6_metrics.py`
- Create: `load_agent/main.py`
- Create: `load_agent/requirements.txt`
- Create: `load_agent/Dockerfile`
- Create: `tests/load_agent/test_client.py`
- Create: `tests/load_agent/test_runtime.py`
- Create: `tests/load_agent/test_k6_metrics.py`

**Interfaces:**
- Agent polls the control API, materializes a private temporary script/config/data directory, runs one k6 subprocess, posts five-second buckets and deletes the directory.
- `K6Runtime.run(shard, command_source, metric_sink) -> ShardResult` owns graceful and forced termination.
- `MetricAggregator.accept(point)` and `flush(window)` produce platform bucket payloads.

- [x] **Step 1: Write failing client, process, and aggregation tests**

Use a fake control server and fake executable. Verify registration persistence, heartbeat, local calibration without business traffic, calibration expiry/signature invalidation, claim, secret-free logs, SIGINT stop, SIGKILL after grace period, crash summary, five-second percentile buckets, bounded samples and retry without duplicate batch IDs.

- [x] **Step 2: Run tests and verify missing package failures**

```bash
.venv/bin/python -m pytest tests/load_agent -q
```

- [x] **Step 3: Implement strict environment configuration**

Require `PLATFORM_URL`, `AGENT_DATA_DIR`, and either `ENROLL_TOKEN` for first registration or a persisted credential. Reject insecure public HTTP secret transport unless `ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT=1`.

- [x] **Step 4: Implement control client and credential storage**

Store the Agent token in a `0600` file under the mounted data directory. Never log request Authorization or job secrets.

Implement the calibration command with a bounded local-only k6 target. Record sustainable VU/rate, CPU and memory peaks, Agent/k6 versions, hardware signature, calibration ID and seven-day validity; never send calibration traffic to a configured business environment.

- [x] **Step 5: Implement k6 output aggregation**

Parse k6 JSON output incrementally from stdout or a named pipe. Keep t-digest-equivalent bounded latency samples per window or exact bounded arrays with an enforced maximum; do not retain the full stream.

- [x] **Step 6: Implement process lifecycle and cleanup**

Send graceful interrupt on `stop`, wait the configured grace period, force terminate if necessary, upload final partial summary, overwrite/delete secret material, and remove the work directory.

- [ ] **Step 7: Build and inspect the image**

```bash
docker build -t midscene-load-agent:test -f load_agent/Dockerfile .
docker run --rm midscene-load-agent:test k6 version
docker history --no-trunc midscene-load-agent:test
```

Expected: pinned k6 version prints; image history contains no enrollment token or environment secret.

- [x] **Step 8: Run Agent tests and commit**

```bash
.venv/bin/python -m pytest tests/load_agent -q
git add load_agent tests/load_agent
git commit -m "feat(load): add isolated Docker k6 agent"
```

### Task 8: Metrics Ingestion, SSE, and Deterministic Report

**Files:**
- Create: `task_server/api_testing/services/load_metric_service.py`
- Create: `task_server/api_testing/services/load_report_service.py`
- Create: `tests/api_testing/test_load_metric_service.py`
- Create: `tests/api_testing/test_load_report_service.py`
- Modify: `task_server/api_testing/events.py`

**Interfaces:**
- `LoadMetricService.ingest()` validates shard ownership, bucket order, metric schema and idempotency.
- `LoadReportService.build(run_id, actor_id) -> dict` returns goal attainment, threshold verdict, series, rankings, shard evidence, sample summaries and comparison.

- [x] **Step 1: Write failing aggregation and verdict tests**

Cover two-shard sums, percentile aggregation from compatible histograms, duplicate buckets, missing windows, dropped iterations, HTTP 200 with business failure, reached-load threshold failure, unreached-load `inconclusive`, lost shard, stopped run and incompatible historical comparison.

```python
def test_unreached_rate_is_inconclusive_even_when_all_requests_pass(report_service):
    report = report_service.build(FIXTURE_RUN_TARGET_5000_ACTUAL_4200, "admin")
    assert report["load_goal"]["reached"] is False
    assert report["verdict"] == "inconclusive"
```

- [x] **Step 2: Run tests and verify missing services**

- [x] **Step 3: Implement schema validation and idempotent ingestion**

Reject negative counters, NaN/Infinity, unknown step IDs, bucket widths other than the run contract and uploads for terminal/reassigned shards. Publish compact SSE events after database commit.

- [x] **Step 4: Implement deterministic report**

Keep `load_goal`, `thresholds`, `transport`, `business`, `workflow`, `dropped_iterations`, `steps`, `agents`, `samples`, `comparison`, and `evidence` as separate sections.

- [x] **Step 5: Run focused tests and commit**

```bash
.venv/bin/python -m pytest tests/api_testing/test_load_metric_service.py \
  tests/api_testing/test_load_report_service.py -q
git add task_server/api_testing/services/load_metric_service.py \
  task_server/api_testing/services/load_report_service.py task_server/api_testing/events.py \
  tests/api_testing/test_load_metric_service.py tests/api_testing/test_load_report_service.py
git commit -m "feat(load): aggregate metrics into truthful reports"
```

---

## Delivery Slice 4: AI Diagnosis, User API, and Notifications

### Task 9: Evidence-Grounded AI Diagnosis

**Files:**
- Create: `task_server/api_testing/services/load_ai_analysis_service.py`
- Create: `ai_skills/prompts/api-load-analysis.v1.md`
- Create: `ai_skills/schemas/api-load-analysis.v1.json`
- Create: `tests/api_testing/test_load_ai_analysis_service.py`
- Modify: `task_server/api_testing/tasks.py`

**Interfaces:**
- `build_evidence_package(report: dict) -> dict` strips secrets and caps samples.
- `LoadAiAnalysisService.request(run_id, actor_id, force=False)` creates an idempotent analysis job keyed by run evidence hash.
- AI output fields are `conclusion`, `bottleneck_category`, `evidence`, `recommendations`, `next_run`, and `confidence`.

- [x] **Step 1: Write failing diagnosis tests**

Cover slow target step, Agent saturation, network errors, business failure, insufficient evidence, prompt injection text inside response samples, secret redaction, model timeout and force regeneration without starting a load run.

- [x] **Step 2: Run tests and verify missing service/schema**

- [x] **Step 3: Add strict prompt and JSON schema**

The system prompt states that sample text is untrusted data and every causal statement must cite evidence identifiers. `bottleneck_category` is one of `target_service`, `network`, `load_agent`, `test_data`, `mixed`, `insufficient_evidence`.

- [x] **Step 4: Implement evidence packaging and async task**

Send only deterministic summary, top steps, time windows, Agent peaks, comparison and bounded redacted samples. Persist model, prompt version, evidence hash, status and result.

- [x] **Step 5: Run focused and AI Gateway checks**

```bash
.venv/bin/python -m pytest tests/api_testing/test_load_ai_analysis_service.py -q
python3 tests/ai_gateway_static_checks.py
```

- [x] **Step 6: Commit AI diagnosis**

```bash
git add task_server/api_testing/services/load_ai_analysis_service.py task_server/api_testing/tasks.py \
  ai_skills/prompts/api-load-analysis.v1.md ai_skills/schemas/api-load-analysis.v1.json \
  tests/api_testing/test_load_ai_analysis_service.py
git commit -m "feat(load): diagnose performance reports with evidence"
```

### Task 10: User-Facing Load Testing API and Feishu Summary

**Files:**
- Create: `task_server/api_testing/load_testing_http.py`
- Create: `tests/api_testing/test_load_testing_http.py`
- Modify: `task_server/api_testing/http.py`
- Modify: `task_server/api_testing/services/notification_service.py`
- Modify: `tests/api_testing/test_notification_service.py`

**Interfaces:**
- Adds user routes listed in the design for scenarios, versions, datasets, runs, events, report, AI analysis, agents and enrollments.
- Uses current browser identity, project/environment scope and new permissions.
- Sends a performance-specific Feishu card after deterministic report completion.

- [ ] **Step 1: Write failing user API authorization and lifecycle tests**

Cover read-only visibility, edit/execute/agent-management separation, cross-project IDs, production permission, direct-link reads, enrollment secret returned only once, start/stop idempotency, report access and AI retry.

- [ ] **Step 2: Write failing Feishu card tests**

Assert task/scenario name, environment, target versus actual load, verdict, p95/p99, error rate, report link and AI status. Do not include raw samples or credentials.

- [ ] **Step 3: Run tests and verify missing routes/card**

- [ ] **Step 4: Implement narrow load-testing dispatch**

```python
def dispatch_load_testing_request(handler, method: str, path: str, query: dict, actor_id: str) -> bool:
    """Handle /load-* user routes without expanding router.py."""
```

Call it once from the current API-testing dispatch before the generic not-found response.

- [ ] **Step 5: Implement performance notification presentation**

Use a separate formatter so existing functional-regression cards remain unchanged.

- [ ] **Step 6: Run HTTP, authorization and notification tests**

```bash
.venv/bin/python -m pytest tests/api_testing/test_load_testing_http.py \
  tests/api_testing/test_notification_service.py -q
```

- [ ] **Step 7: Commit user API and notification**

```bash
git add task_server/api_testing/load_testing_http.py task_server/api_testing/http.py \
  task_server/api_testing/services/notification_service.py \
  tests/api_testing/test_load_testing_http.py tests/api_testing/test_notification_service.py
git commit -m "feat(load): expose performance workflows and notifications"
```

---

## Delivery Slice 5: API Platform Experience

### Task 11: Frontend Contracts, Stores, Navigation, and Node Management

**Files:**
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/router.ts`
- Modify: `api-testing-ui/src/App.vue`
- Create: `api-testing-ui/src/stores/loadTesting.ts`
- Create: `api-testing-ui/src/views/LoadAgentsView.vue`
- Create: `api-testing-ui/src/views/LoadAgentsView.spec.ts`
- Modify: `api-testing-ui/package.json`

**Interfaces:**
- Adds lazy routes `/load-scenarios`, `/load-runs`, `/load-reports`, `/load-agents` under a “性能测试” navigation group.
- Store exposes `loadAgents`, `createEnrollment`, `updateAgent`, `loadScenarios`, `loadRuns`, `startRun`, `stopRun`, and report/AI reads.

- [ ] **Step 1: Write failing navigation and node-page tests**

Assert permission-aware navigation, loading/error/empty states, Chinese search composition, enrollment token shown once, copy feedback, preferred/normal/fallback/disabled controls, local hard versus soft versus calibrated capacity, calibration action and the five Chinese calibration states, disabled action reasons and responsive layout. Every Agent tier, capacity field and status must have a visible Chinese name or explanation.

- [ ] **Step 2: Run the targeted Vitest file and observe failures**

```bash
npm --prefix api-testing-ui test -- --run src/views/LoadAgentsView.spec.ts src/App.spec.ts
```

- [ ] **Step 3: Add typed contracts and Pinia operations**

Do not use `any` for load definitions, run states, metric buckets or AI output. Store mutations update from server responses rather than optimistic assumptions.

- [ ] **Step 4: Add lazy navigation and Agent page**

Show “本机备用节点不会自动参与” beside fallback nodes. Enrollment dialog contains platform URL, expiration and exact Docker command, with explicit HTTPS/private-network warning. Newly registered nodes show “先校准，再执行压测”; calibration state, last time, validity, measured capacity and failure remedy remain visible after refresh.

- [ ] **Step 5: Run tests and responsive component checks**

- [ ] **Step 6: Commit frontend foundation**

```bash
git add api-testing-ui/src/api/contracts.ts api-testing-ui/src/router.ts api-testing-ui/src/App.vue \
  api-testing-ui/src/stores/loadTesting.ts api-testing-ui/src/views/LoadAgentsView.* \
  api-testing-ui/package.json api-testing-ui/package-lock.json
git commit -m "feat(load): add performance navigation and agent management"
```

### Task 12: Scenario and Run Wizards

**Files:**
- Create: `api-testing-ui/src/views/LoadScenariosView.vue`
- Create: `api-testing-ui/src/views/LoadScenariosView.spec.ts`
- Create: `api-testing-ui/src/components/LoadScenarioWizard.vue`
- Create: `api-testing-ui/src/components/LoadScenarioWizard.spec.ts`
- Create: `api-testing-ui/src/views/LoadRunsView.vue`
- Create: `api-testing-ui/src/views/LoadRunsView.spec.ts`
- Create: `api-testing-ui/src/components/LoadRunWizard.vue`
- Create: `api-testing-ui/src/components/LoadRunWizard.spec.ts`

**Interfaces:**
- Scenario wizard emits only server-validated definitions.
- Run wizard emits workload, thresholds, allocation policy and risk acknowledgment.

- [ ] **Step 1: Write failing scenario wizard tests**

Click every step, source selector, single/workflow switch, data mode, validation issue, preflight, version save, cancel and reopen action. Verify dangerous endpoints show the backend reason and cannot be silently selected. Assert Chinese names and usage guidance for all four k6 executors, thresholds and data modes.

- [ ] **Step 2: Write failing run wizard tests**

Cover four load models, stage editing, threshold rows, automatic/specific/group nodes, fallback opt-in, priority, uncalibrated-node blocking, calibrated capacity and expiry, capacity shortfall, per-node connectivity, request estimate, production confirmation, start and post-start navigation.

- [ ] **Step 3: Run tests and verify missing views**

- [ ] **Step 4: Implement scenario wizard with one clear next action**

Each step shows completion state and keeps previously entered data when navigating backward. Search inputs use the existing IME composition guard.

- [ ] **Step 5: Implement run wizard and capacity summary**

Show target, estimated capacity, chosen nodes, per-node allocation, fallback participation, duration and estimated requests before enabling start.

- [ ] **Step 6: Run targeted and existing API UI tests**

```bash
npm --prefix api-testing-ui test -- --run src/components/LoadScenarioWizard.spec.ts \
  src/components/LoadRunWizard.spec.ts src/views/LoadScenariosView.spec.ts src/views/LoadRunsView.spec.ts
```

- [ ] **Step 7: Commit scenario and run UI**

```bash
git add api-testing-ui/src/views/LoadScenariosView.* api-testing-ui/src/views/LoadRunsView.* \
  api-testing-ui/src/components/LoadScenarioWizard.* api-testing-ui/src/components/LoadRunWizard.*
git commit -m "feat(load): guide scenario and load run creation"
```

### Task 13: Real-Time Console, Performance Report, and AI Panel

**Files:**
- Create: `api-testing-ui/src/views/LoadReportsView.vue`
- Create: `api-testing-ui/src/views/LoadReportsView.spec.ts`
- Create: `api-testing-ui/src/components/LoadRunConsole.vue`
- Create: `api-testing-ui/src/components/LoadRunConsole.spec.ts`
- Create: `api-testing-ui/src/components/LoadMetricChart.vue`
- Create: `api-testing-ui/src/components/LoadMetricChart.spec.ts`
- Create: `api-testing-ui/src/components/LoadAiAnalysis.vue`
- Create: `api-testing-ui/src/components/LoadAiAnalysis.spec.ts`

**Interfaces:**
- SSE console reconnects using the last event ID and falls back to bounded polling.
- Report shows deterministic evidence before AI analysis.
- Chart component has an accessible table equivalent and handles missing windows explicitly.

- [ ] **Step 1: Write failing real-time console tests**

Cover queued/starting/running/stopping/finished/failed/cancelled, started versus planned Agent count, stop confirmation, duplicate clicks, SSE reconnect, fallback polling and a lost shard.

- [ ] **Step 2: Write failing report and AI tests**

Assert target attainment and threshold verdict are separate, p50/p90/p95/p99 values are labeled, transport/business/workflow failures are separate, Agent drill-down works, incompatible comparison is marked, AI cites evidence, low-confidence output is visible, and AI failure leaves the report intact.

- [ ] **Step 3: Run targeted tests and verify missing components**

- [ ] **Step 4: Implement real-time and report presentation**

Use route-level lazy loading. Render time-series with a focused SVG component and accessible tabular fallback rather than adding a full chart framework to the main bundle.

- [ ] **Step 5: Implement AI panel and reanalysis**

“重新诊断” displays model/version/evidence hash, never says it reruns load, and refreshes only the analysis section.

- [ ] **Step 6: Run UI suite, production build, and visual smoke**

```bash
npm --prefix api-testing-ui test -- --run
npm --prefix api-testing-ui run build
python3 tests/frontend_static_checks.py
```

Capture desktop, tablet, phone and short-screen screenshots for all new views and verify no hidden controls or horizontal overflow.

- [ ] **Step 7: Commit reporting UI**

```bash
git add api-testing-ui/src/views/LoadReportsView.* api-testing-ui/src/components/LoadRunConsole.* \
  api-testing-ui/src/components/LoadMetricChart.* api-testing-ui/src/components/LoadAiAnalysis.*
git commit -m "feat(load): show real-time metrics and AI diagnosis"
```

---

## Delivery Slice 6: Packaging and End-to-End Evidence

### Task 14: Docker Deployment and Operations

**Files:**
- Create: `deploy/load-agent/docker-compose.yml`
- Create: `deploy/load-agent/.env.example`
- Create: `deploy/load-agent/install.sh`
- Create: `deploy/load-agent/upgrade.sh`
- Create: `deploy/load-agent/uninstall.sh`
- Create: `deploy/load-agent/check.sh`
- Create: `docs/api-load-agent-operations.md`
- Modify: `deploy/package-server.sh`
- Modify: `deploy/install-server.sh`
- Create: `tests/load_agent/test_deploy_scripts.py`

**Interfaces:**
- Install accepts platform URL, one-time enrollment token, local hard limits and scheduling tier.
- Upgrade changes only the pinned image and preserves credential/data volume.
- Uninstall requires an explicit purge flag before deleting registered credentials.

- [ ] **Step 1: Write failing deployment-script tests**

Test shell syntax, Compose rendering, required variables, `0600` env file, CPU/memory limits, fallback defaults, token omission from process arguments after enrollment, idempotent install and non-destructive uninstall.

- [ ] **Step 2: Run tests and observe missing files**

- [ ] **Step 3: Implement Compose and scripts**

The Compose service has `restart: unless-stopped`, read-only root filesystem where possible, bounded tmpfs/work volume, healthcheck and explicit CPU/memory limits. Do not mount Docker socket.

- [ ] **Step 4: Write operations guide**

Include exact first-node and second-node commands, registration check, upgrade, log tail, stop, uninstall, HTTPS/private-network requirement and capacity examples.

- [ ] **Step 5: Build package and inspect contents**

```bash
bash -n deploy/load-agent/*.sh deploy/install-server.sh deploy/package-server.sh
.venv/bin/python -m pytest tests/load_agent/test_deploy_scripts.py -q
bash deploy/package-server.sh
```

Expected: archive contains load Agent image/build context, Compose/scripts and operations guide; it contains no `.env`, Agent credential, dataset or identity database.

- [ ] **Step 6: Commit deployment tooling**

```bash
git add deploy/load-agent deploy/package-server.sh deploy/install-server.sh \
  docs/api-load-agent-operations.md tests/load_agent/test_deploy_scripts.py
git commit -m "feat(load): package Docker load agents"
```

### Task 15: Integrated Gate and Real One-/Two-Agent Acceptance

**Files:**
- Create: `tests/api_testing/test_load_testing_e2e.py`
- Create: `tests/run_load_testing_gate.sh`
- Create: `docs/evidence/api-load-testing-acceptance-2026-09.md`
- Modify: `CODEX_STATE.md`
- Update: `docs/superpowers/plans/2026-09-03-api-load-testing.md`

**Interfaces:**
- Gate provisions a controlled local target exposing fast, delayed, HTTP-error and HTTP-200/business-error endpoints.
- Browser acceptance covers every new actionable control and post-action evidence.

- [ ] **Step 1: Add an end-to-end controlled target and failing gate**

The target produces deterministic 20 ms/200 ms latency, 503 errors and `HTTP 200 + code=1001`. The test registers two isolated Agents and asserts correct sharding, counts and diagnosis categories.

- [ ] **Step 2: Run the gate and fix only observed integration gaps**

```bash
bash tests/run_load_testing_gate.sh
```

Expected before final fixes: at least one real integration assertion fails for a concrete missing handoff; record and repair via TDD rather than weakening expectations.

- [ ] **Step 3: Run complete repository gates**

```bash
bash tests/run_api_testing_gate.sh
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
python3 tests/ai_gateway_static_checks.py
git diff --check
```

- [ ] **Step 4: Perform local real-browser acceptance**

Using a fresh browser session, click every performance navigation item and actionable control. Complete scenario create/edit/version/preflight, node enrollment/update/disable, run create/start/live/stop, report filters/drill-down/comparison, AI reanalysis, refresh, deep link and known temporary-data cleanup. Record exact run IDs and screenshots.

- [ ] **Step 5: Validate one real Docker Agent**

Run fixed VU and fixed arrival-rate scenarios against the controlled target. Match platform report totals to k6 summary and confirm the host process stays within Docker limits.

- [ ] **Step 6: Provide second-server deployment commands and validate two Agents**

The user runs the documented command on the second server. After it registers, execute a bounded two-Agent run, prove per-Agent allocations sum to the target, dataset ranges do not overlap, duplicate buckets do not inflate totals, and stopping reaches both nodes.

- [ ] **Step 7: Validate AI evidence and failure isolation**

Run delayed, 503, business-failure and constrained-Agent fixtures. Confirm deterministic categories first, AI citations second, and an unavailable AI service leaves the report complete.

- [ ] **Step 8: Update evidence, state, and plan checkboxes**

Record exact commit, deployment revision, Agent IDs/versions, run IDs, raw totals, resource peaks, known limitations and cleanup results. Do not claim production capacity from controlled-target tests.

- [ ] **Step 9: Final review and commit**

Review secret exposure, cross-project authorization, public HTTP transport, state recovery, metric idempotency, Agent capacity and all user-visible empty/error/loading states. Then commit and push only after all applicable gates pass.

```bash
git add tests/api_testing/test_load_testing_e2e.py tests/run_load_testing_gate.sh \
  docs/evidence/api-load-testing-acceptance-2026-09.md CODEX_STATE.md \
  docs/superpowers/plans/2026-09-03-api-load-testing.md
git commit -m "test(load): verify distributed performance workflow"
git push origin main
```

## Plan Self-Review Checklist

- [x] Every design requirement maps to a task: scenarios (Task 4), data (Task 5), orchestration (Task 6), Agent (Tasks 2/3/7), metrics/report (Task 8), AI (Task 9), API/notification (Task 10), UI (Tasks 11-13), deployment (Task 14), real evidence (Task 15).
- [x] No production behavior is scheduled before its failing test.
- [x] Model, service and frontend names remain consistent across tasks.
- [x] The first usable vertical slice ends with Agent registration; the first load-producing slice ends with Task 8; the complete user workflow ends with Task 15.
- [x] No task requires a remote deployment before local controlled-target verification.
