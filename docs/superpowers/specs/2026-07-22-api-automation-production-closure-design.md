# API Automation Production Closure Design

## Context

The platform already contains the first version of an API testing workspace:

- `api_asset_service.py` imports an OpenAPI document and stores endpoint snapshots.
- `api_test_plan_service.py` creates local or AI-assisted test-plan drafts.
- `metersphere_service.py` stores MeterSphere configuration, reads live project and
  environment metadata, orchestrates an asynchronous four-phase run, and normalizes
  reports.
- `api_report_service.py` stores API reports and performs lightweight failure
  classification.
- The MeterSphere daily execution console exposes real readiness and execution data.

The current modules are real, but they do not yet form a production closure:

1. API assets are one-time OpenAPI uploads. There is no Apifox source configuration,
   synchronization job, stable endpoint identity, revision history, deletion detection,
   or schema diff.
2. The current endpoint ID includes `schema_hash`. A schema change therefore looks like
   a deleted endpoint plus a new endpoint, which prevents reliable plan-impact analysis.
3. API plan cases contain human-readable steps and assertions, but not a complete,
   executable request/assertion contract suitable for deterministic MeterSphere mapping.
4. MeterSphere project and environment reads are connected to the live QA instance, but
   the case creation, plan execution, status, and report contracts are still generic
   configurable paths. Merely filling four URLs cannot make the current generic payloads
   compatible with MeterSphere `v3.6.5-lts`.
5. API execution and reports remain outside the future global execution/report contracts.
   Rewriting the UI Agent/Runner path now would add risk without helping the first API run.

This design closes the API path incrementally. It reuses the existing services, does not
replace the UI Agent main chain, and establishes contracts that can later be adopted by
the global `ExecutionFacade`.

## Goals

- Synchronize OpenAPI assets from Apifox through a server-side, read-only token.
- Support manual refresh and scheduled incremental synchronization through the same
  asynchronous job contract.
- Keep manual OpenAPI JSON upload as a fallback source.
- Preserve stable endpoint identity across schema revisions and produce deterministic
  added/changed/removed/unchanged diffs.
- Show which confirmed plans and generated cases are affected by an API revision.
- Generate API test cases with concrete request and assertion contracts; unresolved test
  data must block execution rather than becoming fake placeholders.
- Integrate the deployed MeterSphere `v3.6.5-lts` through a versioned adapter instead of
  asking users to guess internal API paths and payloads.
- Match platform endpoints to existing MeterSphere definitions where possible, create or
  update project cases idempotently, create/bind a test plan, trigger execution, poll the
  real run state, and synchronize a canonical report.
- Preserve current API routes and stored snapshots/plans while introducing explicit
  migration fields.
- Define the API execution and report contracts so a later global execution framework can
  adopt them without rewriting this closure.

## Non-Goals

- Do not write any data back to Apifox.
- Do not replace Apifox, MeterSphere, or their native permission models.
- Do not modify the UI Agent, Midscene YAML, Sonic, Runner, Figma, smoke gate, repair, or
  mobile-device execution behavior in this project.
- Do not migrate the whole platform from JSON files to a database in this project.
- Do not move Runner, repair reruns, and Sonic execution through `ExecutionFacade` yet.
- Do not introduce an event bus, Feishu notification center, or global asset center yet.
- Do not let AI decide whether a schema changed, whether a run succeeded, or whether an
  unresolved case is executable.

## Design Principles

1. **Deterministic facts first.** Source hashes, endpoint diffs, executable readiness,
   remote IDs, and run states are platform rules. AI may explain or enrich those facts.
2. **Immutable revisions.** A successful synchronization creates or reuses an immutable
   asset revision. The active pointer changes only after the new revision is fully parsed.
3. **Stable identity, versioned content.** Endpoint identity is independent of its schema
   hash. Every schema change creates a new endpoint revision under the same endpoint key.
4. **Versioned external adapters.** MeterSphere version differences stay behind an adapter
   contract. Frontend code never knows vendor paths or payloads.
5. **Idempotent remote writes.** Repeating a push after a timeout must look up the existing
   mapping before creating another case or plan.
6. **No false readiness.** A configured path is not a capability. A capability is ready
   only after the adapter validates its version, permissions, and response contract.
7. **Backward-compatible storage.** Existing OpenAPI snapshots and API plans remain
   readable. Migration is additive and reversible.

## Target Architecture

```text
Apifox API / uploaded OpenAPI
            |
            v
      API Source Adapters
            |
            v
    API Asset Sync Service
      |              |
      v              v
Asset Revisions   Deterministic Diff
      |              |
      +-------+------+
              v
      AI API Plan Designer
              |
              v
   Executable API Case Contract
              |
              v
 MeterSphere v3.6.5 Adapter
      |       |       |
   case map  run    status/report
              |
              v
      Canonical Test Report
```

The first implementation keeps orchestration in the API domain. It introduces an
execution request/handle contract compatible with the future global facade, but does not
move the existing Runner path.

## Component Boundaries

### 1. API Source Configuration

Add a server-side API source model. Credentials are never returned to the browser.

```json
{
  "source_id": "api_source_xxx",
  "source_type": "apifox",
  "name": "3D业务接口",
  "base_url": "https://api.apifox.com",
  "project_id": "...",
  "branch_id": "",
  "environment_id": "",
  "credential_mode": "access_token",
  "credential_configured": true,
  "sync_enabled": true,
  "sync_interval_minutes": 60,
  "last_sync_id": "",
  "last_success_at": "",
  "created_at": "",
  "updated_at": ""
}
```

Rules:

- Token input is write-only. Empty input preserves the current token; explicit clear is
  required to remove it.
- `source_type=openapi_upload` represents the existing upload path and has no token.
- The scheduler and manual refresh both call the same sync service.
- Only one active synchronization is allowed per `source_id`.
- Source configuration changes do not erase the last successful revision.

### 2. Source Adapter Contract

Introduce a small source interface rather than embedding Apifox HTTP details in the asset
service:

```python
class ApiSourceAdapter:
    def probe(self, source) -> SourceProbe: ...
    def fetch_openapi(self, source) -> SourceDocument: ...
```

Initial implementations:

- `ApifoxSourceAdapter`: calls the official project export endpoint
  `POST https://api.apifox.com/v1/projects/{projectId}/export-openapi?locale=zh-CN`
  using `Authorization: Bearer <token>` and
  `X-Apifox-Api-Version: 2024-03-28`, with an explicit
  `User-Agent: midscene-task-platform/api-sync`. The initial export requests all APIs as OpenAPI 3.0
  JSON and includes Apifox extension properties so a stable provider identifier can be
  used when the response exposes one. Optional configured environment IDs are passed in
  the documented `environmentIds` field. Apifox currently returns an undocumented `201`
  empty response for Python's default user agent; the explicit platform user agent returns
  the documented `200` JSON response. A bounded compatibility fallback to the current CLI
  `/api/v1/.../export-openapi` route is allowed only when the official route is unavailable
  (`404/405`) or returns an empty successful response.
- `UploadedOpenApiSourceAdapter`: wraps a user-uploaded document and preserves current
  behavior.

`SourceDocument` contains the raw document, source revision metadata when available, an
ETag/Last-Modified value when available, and a deterministic full-document hash. Vendor
responses and credentials do not enter stored traces.

### 3. Asynchronous Synchronization

Manual and scheduled synchronization return immediately with a stable `sync_id`:

```json
{
  "sync_id": "api_sync_xxx",
  "source_id": "api_source_xxx",
  "status": "queued",
  "phase": "fetch_source",
  "created_at": "",
  "started_at": "",
  "finished_at": "",
  "previous_revision_id": "",
  "revision_id": "",
  "summary": {
    "added": 0,
    "changed": 0,
    "removed": 0,
    "unchanged": 0,
    "affected_plans": 0
  },
  "error": ""
}
```

Phases are deterministic:

```text
fetch_source -> parse_document -> diff_revision -> persist_revision -> analyze_impact
```

If the source hash equals the active revision hash, the run succeeds with
`status=no_change` and reuses the active revision. A failed fetch or parse keeps the prior
active revision and records an error; it never produces a partial active asset set.

### 4. Stable Asset and Revision Model

Separate source, snapshot/revision, endpoint identity, and endpoint content.

```json
{
  "asset_id": "api_asset_xxx",
  "source_id": "api_source_xxx",
  "status": "active",
  "active_revision_id": "api_revision_xxx",
  "last_sync_at": "",
  "schema_version": "OpenAPI 3.0.1"
}
```

```json
{
  "revision_id": "api_revision_xxx",
  "asset_id": "api_asset_xxx",
  "source_revision": "",
  "document_hash": "...",
  "endpoint_count": 592,
  "created_at": "",
  "endpoints": []
}
```

Each endpoint contains:

- `endpoint_key`: stable identity. Prefer an immutable provider endpoint ID when Apifox
  exposes one; otherwise use a normalized `operationId` when it is unique and stable within
  the source; finally fall back to normalized `METHOD + path`.
- `endpoint_revision_id`: hash of `endpoint_key + schema_hash`.
- `schema_hash`: deterministic hash of request, response, parameter, and security facts.
- `source_ref`: source-specific identifier when available.
- Existing display and schema fields remain available.

When a provider/source identifier keeps the endpoint identity stable, method/path changes
are reported as identity-field changes. For uploaded documents without a stable source
identifier, a method/path rename is conservatively reported as one removal plus one
addition; the platform must not guess that two unrelated operations are the same endpoint.

The current `endpoint_id` is retained as a compatibility alias while old plans are
migrated. New plans store `endpoint_key` and `asset_revision_id`.

### 5. Deterministic Schema Diff

Add an API schema diff service that compares two immutable revisions by endpoint key.

Change categories:

- endpoint added or removed;
- method/path identity change;
- required/optional parameter change;
- request-body requiredness or schema change;
- response status/schema change;
- security requirement change;
- deprecation or metadata-only change.

The diff result stores old/new hashes and compact structured field changes. Plan impact is
computed by joining changed endpoint keys to plan endpoint keys. AI may add a summary and
regeneration recommendation, but the structured diff and affected plan list remain
deterministic.

Removed endpoints are marked `removed` in the latest revision view; historical revisions
and plans continue to resolve them.

### 6. Executable API Case Contract

Extend generated cases beyond prose steps:

```json
{
  "case_id": "API-001-P",
  "endpoint_key": "POST /print/order",
  "asset_revision_id": "api_revision_xxx",
  "name": "创建打印订单-成功响应",
  "priority": "P0",
  "case_type": "positive",
  "request": {
    "method": "POST",
    "path": "/print/order",
    "path_params": {},
    "query": {},
    "headers": {},
    "body": {},
    "auth_ref": "environment_default"
  },
  "assertions": [
    {"type": "status", "operator": "in", "expected": [200, 201]},
    {"type": "schema", "schema_ref": "response:2xx"}
  ],
  "variables": [],
  "dependencies": [],
  "readiness": {
    "state": "executable",
    "missing": []
  },
  "source": "ai",
  "ai_trace_id": "ai_trace_xxx"
}
```

Platform rules validate methods, paths, parameter locations, required values, assertion
types, dependency references, and environment bindings. AI may propose values and
assertions but cannot mark a case executable. Unknown required test data produces
`needs_review` with an explicit missing list and is excluded from MeterSphere push.

Plans are generated from an immutable asset revision. When a later sync changes or removes
a selected endpoint, the plan is marked `stale` with affected case IDs. Execution requires
reconfirmation after regeneration or an explicit review that the change is non-breaking.

### 7. AI Participation and Trace

Reuse `api_test_designer` and the existing Gateway. Do not add another model client.

Normalize API AI traces to the same minimum fields already available in runtime traces:

```json
{
  "trace_id": "ai_trace_xxx",
  "skill": "api_test_designer",
  "action": "generate_case",
  "provider_id": "qwen_plus",
  "model": "qwen3.6-plus",
  "fallback_used": false,
  "input_hash": "...",
  "output_summary": "generated 24 cases; 3 need review",
  "started_at": "",
  "finished_at": "",
  "success": true,
  "error": ""
}
```

The Gateway's `SKILL_ACTION_MAP` must explicitly map `api_test_designer` to
`generate_case`; relying on the current default route is not auditable enough. Prompt
changes must be limited to the executable API case contract and evaluated with fixed
fixtures. Existing UI YAML prompts are outside this scope.

### 8. MeterSphere Version Adapter

Replace the generic "configured path means capability" assumption with a versioned
adapter contract:

```python
class MeterSphereAdapter:
    def probe(self) -> MeterSphereProbe: ...
    def list_projects(self) -> list[Project]: ...
    def list_environments(self, project_id) -> list[Environment]: ...
    def list_definitions(self, project_id) -> list[RemoteDefinition]: ...
    def upsert_cases(self, execution_request) -> CasePushResult: ...
    def prepare_execution(self, execution_request, pushed_cases) -> RemoteExecutionBinding: ...
    def trigger_execution(self, execution_binding) -> RemoteRunHandle: ...
    def get_run(self, run_id) -> RemoteRunState: ...
    def get_report(self, run_id) -> RemoteReport: ...
```

Initial adapter: `MeterSphereV365Adapter` for the deployed
`v3.6.5-lts-f043cdd2` instance.

Already verified metadata contracts are encapsulated by this adapter:

- project options: `/project/list/options/{organization_id}`;
- environments: `/api/test/env-list/{project_id}`.

Write/run/report endpoints are not considered implemented until their request/response
contracts are captured from the deployed version and verified against QA. The platform
must not save guessed paths merely to make readiness green.

The QA `3D业务` project currently exposes the `apiTest` module and rejects test-plan module
endpoints with a module-permission error. The adapter therefore probes and records an
execution strategy instead of assuming that a MeterSphere test plan always exists:

- `api_case_batch`: bind generated cases to API definitions and execute a case batch;
- `api_scenario_batch`: create/bind API scenarios and execute a scenario batch;
- `test_plan`: use a MeterSphere test plan only when the project module and permissions
  explicitly support it.

`RemoteExecutionBinding` exposes one stable platform contract regardless of the selected
strategy. The strategy and every remote case/scenario/plan ID remain auditable in the
mapping record.

Before case creation, the adapter lists MeterSphere API definitions and matches them by
normalized method/path, with operation/source identifiers as secondary evidence. Matching
results are persisted:

```json
{
  "endpoint_key": "POST /print/order",
  "metersphere_definition_id": "...",
  "match_type": "method_path_exact",
  "verified_at": ""
}
```

Ambiguous or missing definitions block only their affected cases. If the deployed API
supports idempotent definition import/upsert, that behavior is a separate explicit
capability; it is not silently inferred.

Remote case and execution-container mappings use a stable platform key and record the
remote ID. A retry first queries the mapping/remote object. Partial push stops before
execution and returns per-case success/failure data.

### 9. API Execution Contract

Introduce the minimum shared execution contract now:

```json
{
  "execution_id": "execution_xxx",
  "test_type": "api",
  "executor": "metersphere",
  "task_id": "api_plan_xxx",
  "asset_revision_id": "api_revision_xxx",
  "environment": {
    "project_id": "...",
    "environment_id": "..."
  },
  "model_context": {
    "ai_trace_ids": []
  },
  "idempotency_key": "..."
}
```

The existing four public phases remain valid for compatibility:

```text
push_cases -> trigger_plan -> metersphere_run -> sync_report
```

Internally `trigger_plan` means `prepare/trigger remote execution`; it may bind a test plan,
an API scenario batch, or an API case batch according to the verified adapter strategy. UI
copy can be generalized later without changing persisted historical events.

The API domain service owns this orchestration during the closure project. The contract is
placed in the execution package and the MeterSphere implementation conforms to it. A later
global framework can register Runner and MeterSphere adapters behind `ExecutionFacade`
without changing API plans, executions, or reports.

### 10. Canonical Report Envelope

Keep existing report files and routes, but normalize new API results to a shared envelope:

```json
{
  "report_id": "report_xxx",
  "test_type": "api",
  "executor": "metersphere",
  "task_id": "api_plan_xxx",
  "execution_id": "execution_xxx",
  "remote_run_id": "...",
  "status": "passed",
  "started_at": "",
  "finished_at": "",
  "duration_ms": 0,
  "summary": {"total": 24, "passed": 23, "failed": 1, "skipped": 0},
  "results": [],
  "failure_summary": {},
  "source_refs": {
    "asset_revision_id": "api_revision_xxx",
    "metersphere_report_id": "..."
  }
}
```

Raw MeterSphere responses are stored only when required for diagnostics, sanitized before
write, and referenced rather than embedded repeatedly. API classification remains in
`api_report_service.py` initially; the envelope allows a later `test_report_service` to
index UI and API reports together.

## API Contracts

Additive routes:

```text
GET    /api/api-testing/sources
POST   /api/api-testing/sources
POST   /api/api-testing/sources/{source_id}/sync
GET    /api/api-testing/syncs/{sync_id}
GET    /api/api-testing/assets/{asset_id}/revisions
GET    /api/api-testing/assets/{asset_id}/diff?from=...&to=...
GET    /api/api-testing/assets/{asset_id}/impact?revision_id=...
```

Existing asset, plan, MeterSphere execution, and report routes remain available. Their
responses gain revision, readiness, stale, mapping, and trace fields without removing
current fields.

Synchronization starts with HTTP 202 and a `sync_id`. The browser polls the sync endpoint
using backend-provided `poll_after_ms`. A scheduled synchronization writes the same record
and appears in the same history.

## User Experience

### API Assets

- Show the active source, last successful synchronization, revision, and source health.
- Primary action is `同步 Apifox`; upload remains a secondary fallback action.
- During sync, show real phases and counts from the sync record.
- After changes, show added/changed/removed counts and affected plans.
- Source failure shows the last successful revision as stale/read-only; it does not clear
  the table.

### AI Plans

- Plans display the bound asset revision and whether a newer revision affects them.
- Cases are grouped by `executable / needs review / blocked`.
- Confirming a plan only confirms cases that pass the deterministic executable contract.
- A stale plan cannot execute until its affected cases are reviewed or regenerated.

### MeterSphere Execution

- Business and environment remain live MeterSphere data.
- Version adapter and capability status replace unexplained blank path requirements.
- The console distinguishes connection, metadata, definition mapping, case push, run,
  status, and report capabilities.
- A capability shows verified only after a real adapter probe, not because a text field is
  non-empty.

## Failure Handling

- **Apifox unavailable/unauthorized:** retain the active revision, mark source degraded,
  stop before diff, and expose a sanitized error.
- **Invalid OpenAPI:** preserve the raw sync error metadata but do not activate a revision.
- **Concurrent sync:** return the existing active `sync_id` with HTTP 409/200 semantics;
  do not start another worker.
- **Partial asset persistence:** write the immutable revision first, then atomically update
  the asset pointer. An interrupted write leaves the previous pointer active.
- **AI generation failure:** retain deterministic seed cases or a reviewable draft and
  record fallback; never report that AI generated the fallback.
- **Unresolved required data:** mark affected cases `needs_review`; do not send them to
  MeterSphere.
- **Definition mismatch:** block the affected endpoint and return candidate matches; do not
  choose an ambiguous remote definition automatically.
- **Case push partial failure:** retain successful remote mappings, stop before run, and
  allow retry only for failed items.
- **Trigger timeout:** query by idempotency key/mapping before retrying creation.
- **Status query failure:** retain the last real remote state and mark status temporarily
  unavailable; do not infer failure or success from time.
- **Report failure:** preserve the MeterSphere terminal state and mark report sync as a
  separate failed phase.

## Security

- Apifox and MeterSphere credentials are server-side only and write-only through the UI.
- Config, sync records, execution events, reports, and AI traces recursively remove token,
  authorization, cookie, access key, secret key, signature, and password values.
- Source URLs and remote errors are sanitized before display.
- Raw OpenAPI documents follow current authenticated API boundaries and are not included in
  public execution logs.
- All configuration changes, sync starts, plan confirmations, and executions use existing
  platform authentication.

## Storage and Compatibility

Continue using atomic JSON storage under `API_TESTING_DIR` for this project:

```text
api-testing/
  sources/
  syncs/
  assets/
  revisions/
  diffs/
  endpoint-mappings/
  snapshots/              # legacy, retained
  plans/
  metersphere-executions/
  reports/
```

Migration is lazy:

- A legacy snapshot can be read as an immutable upload revision when first requested.
- Legacy endpoint IDs remain aliases in plan reads.
- Existing confirmed plans remain visible but are marked `legacy_contract`; execution
  requires conversion/validation against the executable case contract.
- No historical file is bulk rewritten during deployment.

## Observability

Record real sync, AI, mapping, and execution events with stable IDs:

- `sync_id` for source synchronization;
- `revision_id` for immutable asset content;
- `ai_trace_id` for model decisions;
- `execution_id` for platform orchestration;
- `remote_run_id` for MeterSphere;
- `report_id` for normalized results.

Trace linkage is by IDs, not copied full payloads. Technical logs remain expandable across
polls and contain no local placeholder events.

## Testing Strategy

### Unit and Contract Tests

- Apifox adapter authentication, ETag/no-change, timeout, invalid document, and redaction.
- Stable endpoint keys across schema changes.
- Added/changed/removed/unchanged diff classification.
- Affected plan/case calculation and stale gating.
- Legacy snapshot lazy conversion.
- Executable case validation for request fields, values, assertions, dependencies, and
  missing test data.
- AI trace records actual provider/model/fallback and does not claim fallback output as AI.
- MeterSphere v3.6.5 response normalization for every adapter operation.
- Idempotent remote case and execution-container mapping.
- Partial push, trigger timeout, status outage, remote failure, and report failure.
- Recursive credential redaction across all persisted/public values.

### Integration Tests

- Local fake Apifox server: first sync, no-change sync, changed schema, deleted endpoint,
  failed sync preserving active revision.
- Local fake MeterSphere v3.6.5 server: metadata, module/capability probe, definitions, case
  mapping/push, execution binding, execution, status polling, report normalization.
- Full local closure from source sync to canonical report using fixed fixtures.

### QA Acceptance

Use the configured QA Apifox project and only the selected `3D业务` MeterSphere project:

1. Synchronize the real Apifox project and record endpoint count/revision without exposing
   the token.
2. Perform a second no-change sync and confirm no duplicate revision or plan invalidation.
3. Generate and confirm a small executable plan against selected endpoints.
4. Match those endpoints to real MeterSphere definitions and verify which supported remote
   execution strategy the project exposes.
5. Push cases, trigger one real run through that verified strategy, poll to a terminal
   state, and synchronize the real report.
6. Verify all IDs, phases, failure classifications, and evidence come from real responses.

The QA write/run steps are not considered complete until they have run against the deployed
MeterSphere version. Local fixtures alone are necessary but insufficient.

## Delivery Phases

### Phase A: Source and Asset Foundation (P0)

- API source configuration and credential handling.
- Apifox read-only adapter and upload adapter.
- Asynchronous manual/scheduled sync.
- Stable asset/revision/endpoint identity.
- Deterministic diff and affected-plan analysis.
- Asset UI synchronization and revision states.

Exit criteria: real Apifox first/no-change sync works; revisions and diffs are correct; no
existing upload or plan disappears.

### Phase B: Executable Plan Contract (P0)

- Concrete request/assertion/dependency schema.
- Deterministic executable readiness and stale gates.
- `api_test_designer` prompt/schema update and explicit Gateway route.
- AI decision trace normalization.
- Legacy plan compatibility conversion.

Exit criteria: selected endpoints generate reviewable cases, only fully resolved cases can
be confirmed/executed, and schema changes identify the exact affected cases.

### Phase C: MeterSphere v3.6.5 Closure (P0)

- Version probe and adapter registry.
- Definition discovery and exact matching.
- Idempotent case upsert and execution binding for the probed project strategy.
- Real trigger, status polling, and report retrieval.
- Daily execution console readiness based on verified capabilities.

Exit criteria: one real QA plan reaches a terminal MeterSphere state and a normalized report
through the platform, with no guessed IDs or states.

### Phase D: Shared Execution and Report Contracts (P1)

- Move the API execution request/handle and report envelope into shared packages.
- Register MeterSphere behind `ExecutionFacade` without changing public API behavior.
- Add a Runner adapter in a separate, shadow-verified project.
- Preserve direct legacy routes as compatibility wrappers until parity is proven.

Exit criteria: API execution uses the shared facade, Runner shadow comparison passes, and
legacy route behavior is unchanged.

### Phase E: Platform Evolution (P1/P2)

- Canonical report index across UI/API executors.
- Shared failure taxonomy with executor-specific detail.
- Event center and Feishu consumers.
- Unified asset catalog with UI/API discriminators.
- DAG orchestration only after source/execution contracts are stable.

These are follow-on projects, not hidden scope inside the API closure.

## Architecture Roadmap

The broader platform sequence is:

```text
P0  API production closure
    source sync -> revisions/diff -> executable plans -> MeterSphere real run

P1  Global execution/report convergence
    shared contracts -> MeterSphere facade -> Runner shadow migration -> canonical reports

P2  Platform services
    event center -> Feishu -> unified asset catalog -> DAG/trace expansion
```

This order avoids building a global facade around unverified MeterSphere contracts while
ensuring the API closure does not create another dead-end execution model.

## Success Criteria

- Users configure Apifox once and synchronize without uploading files for normal work.
- A no-change sync creates no duplicate revision.
- API changes are deterministic and linked to affected plans/cases.
- Every executable case contains a concrete request and machine-checkable assertions.
- MeterSphere readiness reflects verified `v3.6.5-lts` capabilities, not non-empty paths.
- One real QA execution completes from Apifox revision to normalized report.
- Existing UI Agent, YAML, Runner, Sonic, and historical API assets remain compatible.
- The resulting contracts can be registered behind a future global `ExecutionFacade`
  without changing stored API plans, executions, or reports.
