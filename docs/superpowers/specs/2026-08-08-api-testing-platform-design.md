# API Testing Platform Replacement Design

## Context

The previous API automation runtime was intentionally removed because its product flow had become fragmented, stateful frontend behavior was difficult to reason about, and repeated incremental fixes did not produce a simple testing experience.

The replacement must preserve the useful platform capabilities already proven by the project while rebuilding the API domain behind a clean boundary. The target is not a clone of MeterSphere, Easy-Test, or ITP. It is a focused API testing workspace integrated with the existing Task platform, AI Gateway, authentication, deployment, and reporting conventions.

Reference products inform the design as follows:

- Easy-Test contributes domain concepts: cases, suites, dependencies, asynchronous execution, schedules, debugging, mock rules, notifications, and multidimensional reports.
- ITP contributes workflow concepts: environment variables, scenario execution, extraction, pre/post processing, role boundaries, and operational dashboards.
- The provided AIPPT test console contributes interaction concepts: a small command surface, a stable live log, visible execution history, and a report available in the same workspace.

No reference implementation is copied wholesale. Easy-Test's reusable frontend and backend components are MIT licensed, but its framework versions and large monolithic files are not suitable foundations for this platform. The public ITP repositories mainly provide deployment files and screenshots rather than a complete reusable source tree.

## Goals

1. Provide one obvious end-to-end workflow:

   `manual source refresh -> saved snapshot -> endpoint selection -> AI test design -> debug -> baseline or suite -> execute -> live log -> report`

2. Support the complete capability set requested by the user:

   - Apifox and OpenAPI source import
   - saved and versioned API definitions and environments
   - single and batch case management
   - editable AI-generated cases
   - single-case and batch debugging before baseline adoption
   - suites and scenario dependencies
   - variable extraction and injection
   - pre/post processing
   - local and distributed asynchronous execution
   - realtime logs, cancellation, retry, and rerun
   - baseline regression
   - schedules
   - mock data and rules
   - reports, trends, failure analysis, and notifications
   - audit history and future multi-user permissions

3. Keep API automation isolated from UI Agent, Midscene YAML, Sonic, and Runner behavior.

4. Make the first delivery a complete usable loop instead of a collection of disconnected placeholders.

5. Keep implementation maintainable: bounded modules, schema migrations, explicit state machines, typed frontend contracts, and focused tests.

## Non-Goals

- Reintroducing MeterSphere as a dependency or execution backend.
- Automatically syncing Apifox in the background. Source refresh is explicitly manual.
- Rewriting the existing UI Agent frontend in Vue.
- Copying Easy-Test or ITP source code into the repository.
- Reusing preserved legacy API SQLite files as the live database.
- Storing secrets, tokens, or credentials in browser local storage or execution reports.
- Making AI-generated drafts executable without deterministic validation.

## Product Principles

### One Primary Workflow

Users should not have to understand internal service boundaries. The primary workspace always answers:

1. What project and API revision am I testing?
2. Which environment will be used?
3. Which endpoints and cases are selected?
4. What did AI generate or recommend?
5. What is running now and what failed?

### Progressive Disclosure

Frequent actions stay on the primary workspace. Advanced capabilities appear in drawers or secondary views:

- Primary: endpoint selection, case editing, debug, execute, live log, report.
- Secondary: environment editing, dependency graph, schedules, mock, notifications, versions, and audit.

### Source Data Is Not Test Data

The platform maintains three separate layers:

1. **Source definitions**: immutable snapshots imported from Apifox or OpenAPI.
2. **Test assets**: editable cases, suites, dependencies, assertions, data templates, and baselines.
3. **Execution records**: immutable resolved requests, responses, logs, assertions, and results.

Changing a source snapshot never silently overwrites a confirmed test asset or historical execution.

### AI Assists, Deterministic Gates Decide

AI may analyze contracts, draft cases, suggest assertions and dependencies, and analyze failures. AI output remains a versioned draft until deterministic schema, safety, and executability checks pass. AI cannot silently overwrite confirmed baselines.

## User Experience

### Navigation

The existing Task platform receives one primary navigation entry: `API 测试`.

Inside the Vue application, a compact navigation rail exposes:

- `工作台`
- `测试资产`
- `执行记录`
- `测试报告`
- `配置`

Schedules and mock rules are reached from `配置`; they are not first-level global navigation items. AI is embedded in endpoint and report contexts rather than presented as a disconnected top-level page.

### Workspace Layout

The primary workspace uses three stable columns:

1. **Left: scope**
   - project and saved source revision
   - module and endpoint tree
   - test suite tree
   - search and filters

2. **Center: work surface**
   - endpoint definition
   - request parameters
   - response schema
   - test cases
   - debug result
   - execution record

3. **Right: AI assistant**
   - contract summary
   - risk and missing-data detection
   - generate positive, negative, boundary, authorization, and dependency cases
   - suggest assertions and extraction rules
   - analyze the selected failure

The right column is collapsible. Column widths are stable and user-resizable without changing content dimensions unexpectedly.

### Context Bar

A fixed context bar shows only:

- current project
- source revision and last manual refresh time
- execution environment
- unsaved draft indicator
- primary action for the current state

Internal IDs are available in technical detail drawers, not used as user-facing names.

### Source Refresh

Apifox is a manual source only.

1. The user selects a saved source and clicks `检查 Apifox 更新`.
2. The server fetches the current OpenAPI and environment metadata.
3. The platform computes a deterministic diff without changing the active revision.
4. The user reviews additions, changes, deletions, and affected cases.
5. The user clicks `保存为新版本` to activate a new immutable snapshot.

Entering the workspace never refreshes Apifox. The last saved snapshot renders immediately from PostgreSQL.

Manual OpenAPI file or URL import follows the same snapshot and diff model.

### Environment Management

An environment contains:

- display name
- one default base URL plus service-specific base URLs
- public variables
- secret variables
- default headers
- cookie or authorization strategy
- optional setup action used to obtain a token

Imported Apifox environment values are copied into an editable platform revision. Local-only Apifox values that are unavailable remotely are shown as unresolved rather than fabricated.

Secrets are encrypted at rest. The browser receives only secret names, masks, update intent, and non-sensitive fingerprints. An execution snapshot records which secret revision was used without storing the plaintext in reports.

### Case Design and Debugging

Users can create cases manually or ask AI to generate drafts from selected endpoints.

The structured editor has explicit sections:

- purpose and priority
- request path and method
- headers, query, path variables, cookies, and body
- test data rows
- extraction rules
- assertions
- pre/post processing
- dependencies

Raw JSON is an advanced view, not the default editor.

Debugging does not require baseline adoption. The user can:

- debug one case
- debug selected cases
- debug a suite
- override non-secret environment values for one run
- inspect resolved request, response, assertions, logs, and AI analysis

A passing debug result enables `采纳为基线`. Drafts may still be saved after a failed debug. A privileged future role may adopt an unverified baseline only with an explicit reason in the audit log.

### Suites and Dependencies

A suite is an ordered scenario graph rather than a flat list only.

- Cases may run sequentially or in parallel groups.
- Dependencies form a directed acyclic graph.
- Extraction outputs become named runtime variables.
- Dependency failures skip downstream cases with an explicit reason.
- Cycles and unresolved variables are rejected before execution.
- A visual graph is available for complex suites; the default editor remains a compact ordered list.

### Execution Console

Execution is created as a persistent task and dispatched to an API worker.

The console contains:

- stable task header and state
- progress counters
- append-only realtime log
- case result list
- request/response detail drawer
- cancel action
- rerun failed or selected actions

Logs arrive through Server-Sent Events. The page does not reload or replace the log container. Users can follow the latest line, pause scrolling, search, and filter to warnings or failures.

### Reports

Reports preserve two dimensions:

1. **Execution status**: queued, running, passed, failed, broken, skipped, cancelled.
2. **Failure category**: product assertion, authentication, test data, script, environment, network, timeout, contract drift, or unknown.

The report includes:

- totals, duration, pass rate, and environment
- per-case request, response, extraction, assertions, and logs
- failure clusters with evidence
- AI summary and repair suggestions
- comparison with the previous baseline run
- links to rerun failed cases or edit the originating case

AI analysis never changes the original execution result.

## System Architecture

### Deployment Topology

The replacement is a modular monolith with independent workers:

```text
Nginx
  |-- existing Task static pages and APIs
  |-- /api-test/ -> Vue 3 application
  `-- /api/api-testing/v1/ -> API testing module

Task Python process
  |-- authentication adapter
  |-- API testing HTTP/SSE endpoints
  `-- orchestration services

PostgreSQL
  `-- authoritative API testing data

Redis
  |-- job queues
  |-- event streams
  `-- scheduler and concurrency locks

API worker processes
  `-- isolated HTTP execution and report materialization
```

The first worker may run on the application server. The queue contract supports additional workers later without changing the user-facing API.

### Backend Boundary

New code lives under a bounded package such as:

```text
task_server/api_testing/
  api/
  domain/
  repositories/
  services/
  workers/
  adapters/
  migrations/
```

`task_server/router.py` only mounts the module router and SSE handler. It does not contain API testing business logic.

### Frontend Boundary

The Vue 3 + TypeScript application lives in a dedicated directory and builds to versioned static assets. It reuses the platform session and visual tokens but owns its routing and state.

Recommended libraries are deliberately small:

- Vue 3 and Vue Router
- Pinia
- a typed HTTP client generated or validated from the module OpenAPI contract
- a focused code editor only for raw JSON and scripts
- Lucide icons

The application does not share mutable global variables with the legacy Task frontend.

## Data Model

All primary tables use UUID identifiers, timestamps, `owner_id`, audit fields, and optimistic version columns where edits are expected.

### Projects and Sources

- `api_projects`
- `api_project_members`
- `api_sources`
- `api_source_revisions`
- `api_source_endpoints`
- `api_source_schemas`
- `api_source_diffs`

Source revision payloads and normalized schemas use PostgreSQL JSONB. Stable endpoint identity is derived from source, method, normalized path, and source operation identity rather than display name.

### Environments

- `api_environments`
- `api_environment_revisions`
- `api_environment_variables`
- `api_environment_services`
- `api_secret_values`

Secret values are encrypted using a server-managed master key and are never returned through read APIs.

### Test Assets

- `api_cases`
- `api_case_versions`
- `api_case_data_rows`
- `api_case_assertions`
- `api_case_extractions`
- `api_case_scripts`
- `api_suites`
- `api_suite_versions`
- `api_suite_nodes`
- `api_suite_edges`
- `api_baselines`

Confirmed cases and suites are immutable versions. Editing creates a draft version and preserves the previous baseline.

### Execution

- `api_executions`
- `api_execution_cases`
- `api_execution_attempts`
- `api_execution_events`
- `api_execution_artifacts`
- `api_failure_analyses`

Each execution records immutable references to the source, environment, case, and suite revisions used. Resolved requests and responses are sanitized before persistence.

### Operations

- `api_schedules`
- `api_schedule_runs`
- `api_notification_channels`
- `api_notification_deliveries`
- `api_mock_rules`
- `api_mock_requests`
- `api_audit_events`

### Legacy Data

Preserved legacy SQLite and report files remain read-only. Migration is a separate explicit import command after the new schema is stable. They are not read during normal startup.

## Execution Model

### State Machine

Execution states are explicit:

```text
QUEUED -> RUNNING -> PASSED
                  -> FAILED
                  -> BROKEN
                  -> CANCELLED
QUEUED ----------> CANCELLED
```

Case attempts may also be `SKIPPED` when a dependency fails or a condition evaluates false.

`FAILED` means the product response violated an assertion. `BROKEN` means the case, script, environment, network, or infrastructure prevented a valid product verdict.

### Job Guarantees

- Every submission has an idempotency key.
- Workers claim jobs with a lease and heartbeat.
- Lost leases return to the queue after a bounded timeout.
- Cancellation is cooperative and checked between network and script steps.
- Retries create attempts; they do not rewrite prior results.
- Project and environment concurrency limits prevent accidental load spikes.
- Request timeouts, response size limits, and log size limits are enforced.

### Script Safety

Pre/post processing runs in a constrained subprocess with:

- execution timeout
- memory and output limits
- no direct database credentials
- an allowlisted context API
- masked secrets

The first implementation may support declarative extraction and assertions before enabling Python scripts. Script support is delivered only with the isolation boundary in place.

## AI Integration

The existing AI Gateway remains the only model access layer.

AI tasks are versioned capabilities rather than prompt strings embedded in UI code:

- `api_contract_analysis`
- `api_case_generation`
- `api_assertion_generation`
- `api_dependency_generation`
- `api_failure_analysis`
- `api_case_repair`

Each AI record stores:

- capability and schema version
- requested model and actual model
- input revision references
- structured output
- validation result
- latency and failure status
- accepted or rejected changes

Generation is asynchronous and cancellable. Large endpoint selections are chunked with bounded concurrency. Partial batches are visible and retryable.

## Mock, Scheduling, and Notifications

### Mock

Mock rules bind method and path matchers to static, templated, or conditional responses. Rule priority and environment scope are explicit. Mock traffic is isolated from production execution credentials and retains bounded request history.

### Scheduling

Schedules target confirmed suites or baselines, not mutable drafts. Redis provides distributed locks, while PostgreSQL records the schedule and every trigger. Missed-run behavior and time zone are explicit.

### Notifications

Notification channels are adapters. Initial channels are email and a generic webhook; Feishu can use the webhook adapter. Messages link to the platform report and contain no secret or full sensitive response body.

## API Contract

All new endpoints use `/api/api-testing/v1/` and return a consistent envelope with request identifiers and structured errors.

Resource groups include:

- `/projects`
- `/sources` and `/source-revisions`
- `/environments`
- `/endpoints`
- `/cases` and `/case-versions`
- `/suites` and `/suite-versions`
- `/executions` and `/executions/{id}/events`
- `/reports`
- `/mocks`
- `/schedules`
- `/notifications`
- `/ai-jobs`

The frontend consumes only public response models. ORM entities, encrypted fields, queue payloads, and internal exception text are not serialized directly.

## Failure Handling

- Source refresh failure leaves the active snapshot untouched.
- A source diff can be saved only after the complete document parses and normalizes successfully.
- Environment validation identifies unresolved variables before execution.
- AI timeout preserves completed batches and offers retry for failed batches.
- Worker loss is visible as infrastructure recovery, not product failure.
- SSE reconnect resumes from the last event identifier.
- Report materialization is idempotent and recoverable from execution records.
- Database and Redis health are included in a dedicated API testing readiness endpoint without changing the platform's existing health contract.

## Security

- Secrets are encrypted at rest and masked in APIs, logs, reports, notifications, and AI prompts by default.
- Outbound requests enforce configurable host allowlists and block unsafe loopback or metadata targets unless an administrator explicitly permits them.
- File uploads have type and size limits.
- Scripts run in a constrained process.
- Audit events cover source changes, environment changes, baseline adoption, execution, cancellation, schedule changes, and secret updates.
- PostgreSQL schema reserves owners, project members, and roles from the first migration; the first release continues to use the existing platform login.

## Delivery Phases

Every phase ends in a deployable and independently verified state. Later phases extend the same data model and workflow rather than replacing earlier work.

### Phase 1: Foundation and Complete Manual Loop

- PostgreSQL and Redis deployment
- schema migrations and repository layer
- Vue application shell integrated with existing authentication
- project and manual Apifox/OpenAPI source snapshots
- source diff and activation
- editable environments and encrypted secrets
- endpoint tree and search
- manual case editor
- AI case draft generation
- deterministic case validation
- single and selected-case debugging
- asynchronous local worker
- SSE log console
- execution detail and basic report
- passing debug result adoption as baseline

Acceptance: a user can manually refresh Apifox, save a revision, select the three `我的收藏` endpoints, configure the production environment token, generate and edit cases, debug them, execute them, watch stable live logs, and inspect a truthful report without using MeterSphere.

### Phase 2: Test Assets and Scenario Orchestration

- case and suite versioning
- ordered and parallel suite nodes
- dependency graph validation
- extraction and variable injection
- declarative preconditions and post-processing
- batch debug
- baseline regression and failed-case rerun
- contract drift impact analysis
- audit history and version comparison

Acceptance: multi-interface flows execute deterministically, downstream cases consume extracted values, and contract changes identify affected confirmed assets.

### Phase 3: Operations and Distributed Execution

- additional worker registration and leases
- concurrency, rate limits, retry, timeout, and cancellation
- schedules
- email and webhook notifications
- execution history filters and trend reports
- archival and retention policies

Acceptance: scheduled and manually submitted suites can run on multiple workers without duplicate completion, while progress and results remain truthful.

### Phase 4: Mock and Advanced Quality Capabilities

- mock rules and request history
- performance smoke profiles
- security-oriented contract checks
- reusable data generators
- report comparison and quality gates

Acceptance: advanced checks reuse the same environment, execution, event, and report contracts rather than introducing separate products.

### Phase 5: Multi-User Productization

- project membership and roles
- per-action authorization
- ownership transfer
- shared dashboards
- CI/CD trigger API and webhooks
- administrative capacity and secret rotation views

Acceptance: colleagues can use shared projects with explicit permissions and complete audit trails without migrating existing records.

## Testing Strategy

### Backend

- migration tests from an empty database and between every schema version
- repository tests against PostgreSQL
- contract tests for source normalization, environment resolution, case validation, suite DAGs, execution state transitions, and report classification
- worker tests for lease recovery, idempotency, cancellation, timeout, retries, and secret masking
- adapter tests for Apifox, OpenAPI, AI Gateway, email, and webhooks

### Frontend

- TypeScript and build checks
- component tests for editors, selectors, logs, drawers, and state transitions
- contract fixtures for loading, empty, partial, failed, and completed states
- Playwright desktop and mobile smoke tests
- browser checks for no console errors, no full-page polling, stable scroll behavior, and long text containment

### End-to-End

- local disposable PostgreSQL and Redis
- deterministic mock HTTP target
- manual source refresh and revision activation
- AI response fixtures plus one optional real model smoke test
- single debug, batch debug, baseline adoption, suite execution, cancellation, retry, and report verification
- deployment smoke test through Nginx and the existing platform login

### Phase Gate

No phase is complete until:

1. focused tests pass
2. repository static checks pass
3. migrations apply and roll forward on a clean database
4. browser smoke tests pass at desktop and mobile widths
5. secrets are absent from logs and generated artifacts
6. existing UI Agent, Runner, Sonic, and YAML checks remain unchanged and passing
7. `CODEX_STATE.md` records the implemented behavior, verification, and remaining phase

## Maintainability Rules

- No API testing business logic in `router.py`.
- No frontend module may exceed a reasonable single-responsibility boundary; views compose domain components and stores.
- No polling loop may replace complete page state when an incremental event channel exists.
- No raw JSON blob is the sole editable representation of a core asset.
- No background task may report success before worker completion and report persistence.
- No product failure may be inferred from infrastructure or script failure.
- No source refresh may mutate confirmed cases or baselines.
- No new execution mode is added outside the API testing module.
- Every schema and API contract change includes a migration or compatibility test.

## Success Criteria

The replacement is successful when a new colleague can complete the primary workflow without understanding internal IDs or visiting disconnected technical pages, while an experienced tester can still configure dependencies, environments, schedules, mocks, and advanced assertions without leaving the same product boundary.

The system remains successful operationally when API tasks can run asynchronously and later distributively, realtime logs remain stable, reports preserve truthful status categories, source refreshes are versioned and recoverable, and future multi-user support requires enabling reserved authorization rules rather than redesigning the database.
