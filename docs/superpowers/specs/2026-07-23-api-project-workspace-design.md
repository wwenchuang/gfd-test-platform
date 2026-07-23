# API Project Workspace and Modular Planning Design

## Context

The API closure already has real building blocks:

- Apifox sources use independent `source_id` values and write-only access tokens.
- Each source owns an immutable API asset/revision chain.
- API plans contain deterministic request, assertion, dependency and readiness contracts.
- MeterSphere `v3.6.5-lts` has an exact adapter for project/environment probing,
  idempotent case/scenario writes, execution and report synchronization.

The current product surface does not carry that separation end to end:

- The UI edits the selected source but cannot create and switch API projects explicitly.
- A revision with 971 endpoints is rendered as one flat, initially selected table.
- `module` is an Apifox folder path, but there is no module tree or persisted sync scope.
- Plans do not expose `source_id` and the frontend reads plans globally.
- More than 12 selected endpoints silently disable AI and use deterministic local cases.
- MeterSphere project/environment selection is global, so a second API project could run
  against the wrong remote project.
- The platform has no first-class business authentication binding. `auth_ref` remains the
  generic `environment_default` placeholder and the exact adapter refuses sensitive
  request headers.

This design completes the current Phase C product flow without starting Phase D/E/F.
It does not migrate the UI Agent path, create a second asset system, add tenant/RBAC
semantics, or unify all reports.

## Goals

- Treat each existing API `source_id` as one selectable API project workspace.
- Let users create, edit and switch Apifox projects without exposing stored tokens.
- Discover and display the current source as a module/folder tree with endpoint counts.
- Support full-project or selected-module synchronization through an explicit persisted
  scope.
- Bind plans to source, immutable revision, module scope and endpoint keys.
- Generate large selections as auditable AI batches of at most 12 endpoints instead of
  silently changing generation mode.
- Make case correctness reviewable through deterministic readiness, request, assertion,
  authentication and revision evidence.
- Bind each API project to one verified MeterSphere project/environment.
- Configure static Bearer or API-key credentials from the platform while keeping the
  secret only in the selected MeterSphere environment.
- Prevent cross-source plan display, execution, polling and report linkage.
- Preserve old sources, revisions, plans, remote ownership markers and routes.

## Non-Goals

- Do not add organizations, tenants, row-level permissions or project membership. Those
  remain Phase E.
- Do not discover Apifox projects through undocumented APIs. A project is created with a
  user-provided Apifox Project ID and write-only access token.
- Do not treat Apifox folders as stable endpoint identity. `endpoint_key` remains the
  identity fact.
- Do not add dynamic login flows, refresh-token workflows, dependency extraction or
  script-based authentication in this slice.
- Do not store a business token in a plan, revision, event, report, browser response or
  platform JSON file.
- Do not allow one request to generate AI cases for all 971 endpoints.
- Do not start canonical execution/report migration or alter the UI Agent/Runner path.

## Terminology

- **API project workspace**: an existing `source_id` and its source, asset revisions,
  plans, MeterSphere binding and authentication reference.
- **Provider project ID**: `source.project_id`, the Apifox project identifier.
- **Module path**: a display/sync selection path derived from `x-apifox-folder`, then tag,
  then URL path fallback.
- **Execution binding**: the selected MeterSphere project/environment for one
  `source_id`.
- **Authentication binding**: a non-secret reference describing which MeterSphere
  environment variable provides Bearer/API-key authentication.

## Architecture

```text
API source (source_id)
  |
  +-- Apifox project/branch/token
  +-- sync scope + discovered module catalog
  |
  v
immutable asset revision
  |
  +-- endpoint_key facts
  +-- module path display metadata
  |
  v
plan generation job
  |
  +-- <= 12 endpoints per sequential AI batch
  +-- one persisted child plan per batch
  +-- deterministic readiness and revision checks
  |
  v
source execution binding
  |
  +-- MeterSphere project/environment
  +-- auth_ref -> MeterSphere environment variable
  |
  v
MeterSphere v3.6.5 execution and report
```

The source remains the workspace boundary. No new `workspace_id` is introduced because
it would duplicate an existing stable boundary and prematurely mix Phase E authorization
concerns into this delivery.

## API Source and Sync Scope

The source model gains additive fields:

```json
{
  "source_id": "api_source_xxx",
  "project_id": "5904970",
  "sync_scope": {
    "mode": "all",
    "module_paths": [],
    "matcher_version": "apifox_folder_v1"
  },
  "module_catalog": [
    {
      "path": "家用业务/app接口/我的/我的收藏",
      "name": "我的收藏",
      "parent_path": "家用业务/app接口/我的",
      "depth": 4,
      "endpoint_count": 3
    }
  ],
  "scope_fingerprint": "..."
}
```

Rules:

1. `mode` is `all` or `selected`.
2. `selected` requires at least one normalized module path.
3. Matching is a folder boundary match: selecting `A/B` includes exactly `A/B` and
   descendants `A/B/...`; it does not include `A/BB`.
4. The adapter still fetches the documented full OpenAPI export. The platform derives a
   module catalog from the full document, then filters operations locally before staging.
   This avoids depending on an unverified Apifox module-list or folder-export API.
5. Components, security schemes, servers and document metadata remain in the filtered
   document. Only path operations outside the selected scope are removed.
6. The revision document hash includes the scope fingerprint. Changing scope therefore
   creates a new immutable revision even when upstream schemas are unchanged.
7. Sync records store scope and module counts. A missing selected module fails before
   activation and preserves the previous active revision.
8. The source keeps the latest full module catalog so users can expand the scope after a
   filtered synchronization.

Apifox's documented `moduleId` export option may be added later when a supported module
discovery contract is available. It is not required for this folder-boundary scope.

## Asset Module Read Model

Each endpoint keeps its current `module` field for compatibility and gains:

```json
{
  "module_path": "家用业务/app接口/我的/我的收藏",
  "module_segments": ["家用业务", "app接口", "我的", "我的收藏"]
}
```

The backend returns a deterministic module tree/read model:

```json
{
  "module_summary": {
    "total_modules": 42,
    "total_endpoints": 971,
    "roots": []
  }
}
```

The tree is derived from the immutable revision. It is not stored as endpoint identity and
does not replace `endpoint_key`.

## Plan Scope and Batched Generation

Every new plan stores:

```json
{
  "source_id": "api_source_xxx",
  "asset_id": "api_asset_xxx",
  "asset_revision_id": "api_revision_xxx",
  "module_paths": ["家用业务/app接口/我的"],
  "selected_endpoint_keys": ["apifox:123"],
  "scope_fingerprint": "...",
  "generation_id": "api_plan_generation_xxx",
  "batch_index": 1,
  "batch_count": 3,
  "execution_binding_id": "api_execution_binding_xxx",
  "binding_fingerprint": "..."
}
```

Generation is an asynchronous server job:

```json
{
  "generation_id": "api_plan_generation_xxx",
  "source_id": "api_source_xxx",
  "asset_revision_id": "api_revision_xxx",
  "status": "running",
  "batch_size": 12,
  "batch_count": 3,
  "completed_batches": 1,
  "failed_batches": 0,
  "batches": [
    {
      "batch_index": 1,
      "status": "succeeded",
      "endpoint_count": 12,
      "plan_id": "api_plan_xxx",
      "error": ""
    }
  ],
  "poll_after_ms": 1000
}
```

Rules:

1. One generation accepts 1-60 endpoints. The frontend never defaults to all endpoints.
2. Batches contain at most 12 endpoints and run sequentially through the existing
   `api_test_designer` skill.
3. Each batch persists one ordinary API plan. Existing plan confirmation, stale checks
   and MeterSphere execution remain reusable.
4. A batch failure is explicit. It is not labeled AI output and is not silently replaced
   by deterministic local output. The failed batch can be retried.
5. All batches freeze the same source, revision, module scope and binding fingerprint.
6. Generation revalidates every selected endpoint key against the requested revision.
7. Plan list/read APIs accept `source_id` and never mix projects in the project workspace
   UI.
8. Confirmation revalidates source ownership, active revision, endpoint scope, execution
   binding and authentication reference.
9. A changed source scope or execution binding makes an old plan non-executable and
   requires regeneration.
10. Legacy plans remain readable. Plans without source metadata use the existing legacy
    path and are never silently assigned to a new source.

## Correctness Review

The UI does not label a generated plan simply "correct". It displays server facts:

- source and immutable revision;
- selected modules and endpoint count;
- AI provider/model/trace or explicit fallback/failure;
- concrete method/path, parameters, body and assertions;
- authentication reference status;
- executable and needs-review counts;
- missing required values;
- fresh/stale revision state;
- current execution-binding state.

The platform remains authoritative for route matching, required values, assertion types,
dependency validity, authentication binding and execution readiness. AI enriches cases
but cannot set readiness or bypass a failed batch.

## MeterSphere Project Binding

Connection credentials and organization/workspace configuration remain global because
they describe one MeterSphere installation. Project/environment selection becomes
source-specific:

```json
{
  "binding_id": "api_execution_binding_xxx",
  "source_id": "api_source_xxx",
  "provider": "metersphere",
  "project_id": "ms-project-id",
  "project_name": "3D业务",
  "environment_id": "ms-environment-id",
  "environment_name": "APP测试环境",
  "verified_at": "2026-07-23 15:00:00",
  "config_fingerprint": "...",
  "created_at": "",
  "updated_at": ""
}
```

Rules:

1. Binding save reads live MeterSphere project/environment metadata and fails closed when
   either ID is not available.
2. MeterSphere project options come from the exact `v3.6.5-lts` organization project
   options endpoint. Environments come from the exact selected-project API test
   environment endpoint.
3. The exact adapter request callback receives the bound project configuration. It must
   not reuse global `PROJECT` headers for another source.
4. An execution record persists source, binding, project and environment IDs before its
   worker starts.
5. Every worker refresh, case push, scenario trigger, run poll and report fetch uses that
   persisted binding.
6. Execution context, plans and runs are filtered by `source_id`.
7. The existing single source may lazily adopt the legacy global project/environment
   selection. A second source never inherits that selection automatically.

## Business Authentication

The first delivery supports:

- Bearer token in the `Authorization` header;
- API key in a validated custom header.

The public authentication binding contains no secret:

```json
{
  "auth_ref": "api_auth_xxx",
  "type": "bearer",
  "header_name": "Authorization",
  "variable_name": "MTP_API_AUTH_ABC123",
  "configured": true,
  "verified_at": "",
  "updated_at": ""
}
```

Saving a credential performs the following exact flow:

1. Receive the secret through the authenticated platform route.
2. Load the selected MeterSphere `v3.6.5-lts` environment detail.
3. Upsert one platform-owned constant environment variable through the exact environment
   update multipart contract.
4. Verify that the variable exists and is non-empty without returning its value.
5. Persist only the `auth_ref`, type, header name, variable name and timestamps.
6. Discard the request secret.

Clearing removes only the platform-owned variable and clears the non-secret binding.

Generated positive cases for secured endpoints store `request.auth_ref`. At remote case
materialization the adapter resolves that reference to:

```text
Authorization: Bearer ${MTP_API_AUTH_ABC123}
```

or:

```text
X-API-Key: ${MTP_API_AUTH_ABC123}
```

Only this exact environment-variable form is allowed for sensitive headers. Literal
sensitive values remain blocked. The remote case contains a reference, not a token.

If authentication is missing, belongs to another source/environment, or no longer
matches the binding fingerprint, secured positive cases become `needs_review` and
execution is blocked.

Dynamic login/token extraction remains a later project because it requires explicit
dependency and variable contracts.

## API Contracts

Existing routes remain available. Additive or extended routes:

```text
GET    /api/api-testing/sources
POST   /api/api-testing/sources
GET    /api/api-testing/assets?source_id=...
POST   /api/api-testing/sources/{source_id}/sync

GET    /api/api-testing/sources/{source_id}/execution-binding
POST   /api/api-testing/sources/{source_id}/execution-binding
POST   /api/api-testing/sources/{source_id}/auth-binding
DELETE /api/api-testing/sources/{source_id}/auth-binding

POST   /api/api-testing/plan-generations
GET    /api/api-testing/plan-generations/{generation_id}
POST   /api/api-testing/plan-generations/{generation_id}/retry
GET    /api/api-testing/plans?source_id=...

GET    /api/api-testing/metersphere/execution-context?source_id=...
POST   /api/api-testing/metersphere/executions
```

All configuration, generation, confirmation and execution writes use existing user
authentication.

## User Experience

### Project Header

Every API page uses the same compact project selector:

```text
API 项目 [3D业务 / 5904970] [+]
```

The add action opens an empty project configuration state. Editing and creating remain
distinct, and switching a project clears revision/module/plan/execution request state.

### Asset Page

- The left pane is a module/folder tree with endpoint counts and selected state.
- The right pane shows only the current module, with method/search filters and pagination.
- Module and endpoint selection are kept by `source_id + revision_id`.
- Nothing is selected by default after a project/revision change.
- Synchronization settings expose `全部` or `选中模块`.
- Technical sync logs keep the existing stable expansion and scroll behavior.

### Plan Page

- Generation requires a current source/revision and explicit endpoint selection.
- Real generation progress shows `batch n/m`, model status and server plan IDs.
- A failed batch remains visible and can be retried alone.
- Each child plan shows source, revision, module, request/assertion/readiness and binding
  evidence before confirmation.
- A stale plan provides a direct regenerate action using the current revision.

### Execution Page

- The selected API project is visible and immutable for the current context.
- MeterSphere project/environment selection saves an execution binding for that source.
- Business authentication is a separate panel from Apifox and MeterSphere connection
  credentials.
- Plan cards show the bound project/environment and authentication state.
- Old execution polling responses are discarded after source changes.

## Compatibility and Migration

- Existing source files gain defaults lazily; no bulk rewrite is required.
- Existing assets and revision IDs remain unchanged.
- Existing `module` remains readable; new module path fields are additive.
- Existing plan IDs and remote ownership markers remain unchanged.
- A single existing source may adopt the legacy global MeterSphere selection once.
- Legacy plans remain visible but do not gain false source/binding/auth evidence.
- Existing global MeterSphere config remains the connection/settings source and no longer
  acts as the normal per-project selection after migration.

## Security

- Apifox tokens keep current write-only behavior.
- MeterSphere connection secrets keep current write-only behavior.
- Business secrets are sent directly to the bound MeterSphere environment and are not
  persisted locally.
- Source, plan, binding, generation, execution and report public values contain no secret.
- `Authorization`, `Cookie`, token, API key, access key, secret, signature, password and
  credential fields are recursively redacted.
- Sensitive OpenAPI header examples/defaults are never materialized into API cases.
- The adapter accepts a sensitive header only when its value exactly matches the
  authorized MeterSphere variable-reference template.

## Testing

### Backend

- Multiple sources keep independent assets, scopes, plans and bindings.
- Module discovery, parent-boundary filtering and scope fingerprint behavior.
- Scope changes create a revision; missing modules preserve the previous revision.
- Plan source/revision/module/endpoint ownership and 12-endpoint batch boundaries.
- Batch failure/retry does not claim local fallback as AI.
- Per-source MeterSphere project/environment validation and execution snapshots.
- Legacy single-source binding migration and second-source non-inheritance.
- Bearer/API-key environment variable upsert, verify and clear.
- Business secret absent from every local file, public response, event and remote case.
- Sensitive OpenAPI header examples never enter a plan.
- Literal sensitive remote headers remain blocked; exact variable references pass.

### Frontend and Browser

- Create and switch two API projects without state leakage.
- Module tree count, parent/child selection and right-table filtering.
- No default selection for 971 endpoints.
- A 25-endpoint selection produces `12 / 12 / 1` sequential server batches.
- Failed-batch retry and stale-plan regeneration.
- Source-filtered plans and execution context.
- Binding and authentication inputs never refill a secret.
- Old generation/execution responses cannot redraw a newly selected project.
- Desktop and 390px screenshots for project switching, module tree, batch review,
  authentication and MeterSphere binding.

## Delivery Boundary

This is a Phase C completion slice. Phase D canonical execution/report registration,
Phase E RBAC/outbox/audit, Phase F unified asset index/dashboard and Qwen model migration
remain ordered exactly as documented in
`docs/superpowers/specs/2026-07-22-production-evolution-roadmap.md`.
