# MeterSphere 3.6.5 Real Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace guessed MeterSphere write paths with an exact, idempotent MeterSphere `v3.6.5-lts` API case/scenario execution adapter and recover a real remote report.

**Architecture:** Keep `metersphere_service.py` as the local orchestration boundary and add a focused `MeterSphereV365Adapter` for remote contracts. Select the adapter only after exact version probing; preserve the legacy configurable-path adapter for compatibility and persist only redacted remote evidence plus stable ID bindings.

**Tech Stack:** Python 3, `urllib`, `cryptography`, JSON file storage, MeterSphere 3.6.5 HTTP API, existing static-check test harness.

## Global Constraints

- Do not modify, stage, revert, or overwrite the protected user YAML files, `sonic_service.py`, `yaml_executable_scorer.py`, the local Windows Runner script, or `server-tasks/AI_Agent_草稿/`.
- Do not push; the user owns push and deployment.
- Do not persist or return MeterSphere credentials, signatures, environment variable values, or request authorization material.
- Do not infer write readiness from configured path strings.
- Do not silently drop unsupported case-contract fields or assertions.
- Do not migrate the UI Agent, Runner, Sonic, or canonical report ownership in this phase.

---

### Task 1: Exact Authentication And Redaction

**Files:**
- Create: `task_server/services/metersphere_v365_adapter.py`
- Create: `tests/metersphere_v365_adapter_checks.py`
- Modify: `task_server/services/metersphere_service.py`
- Modify: `deploy/install-server.sh`

**Interfaces:**
- Produces: `build_v365_auth_headers(access_key, secret_key, now_ms=None, nonce=None) -> dict[str, str]`.
- Produces: stronger `sanitize_metersphere_data(value) -> Any` handling nested header key/value records.

- [x] **Step 1: Write failing auth-vector and nested-redaction tests**

```python
headers = build_v365_auth_headers("1234567890abcdef", "abcdef1234567890", now_ms=1700000000000, nonce="fixed")
assert set(headers) == {"accessKey", "signature"}
assert decrypt_fixture(headers["signature"]) == "1234567890abcdef|fixed|1700000000000"
assert sanitize_metersphere_data({"headers": [{"key": "Authorization", "value": "Bearer secret"}]}) == {"headers": []}
```

- [x] **Step 2: Run `python3 tests/metersphere_v365_adapter_checks.py` and verify RED**

Expected: import/function assertion failure before implementation.

- [x] **Step 3: Implement AES-CBC/PKCS7 signature generation and fail-closed dependency handling**

```python
plaintext = f"{access_key}|{nonce or uuid.uuid4()}|{now_ms or int(time.time() * 1000)}"
encryptor = Cipher(algorithms.AES(secret_key.encode()), modes.CBC(access_key.encode())).encryptor()
signature = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()
```

- [x] **Step 4: Delegate 3.6.5 Access Key headers from `metersphere_service.py` and add `cryptography` installation**

Expected: no `timestamp` header and no HMAC for Access Key mode.

- [x] **Step 5: Run the focused tests and verify GREEN**

Run: `python3 tests/metersphere_v365_adapter_checks.py`
Expected: PASS.

### Task 2: Version Probe And Runtime Capabilities

**Files:**
- Modify: `task_server/services/metersphere_v365_adapter.py`
- Modify: `task_server/services/metersphere_service.py`
- Test: `tests/metersphere_v365_adapter_checks.py`

**Interfaces:**
- Produces: `MeterSphereV365Adapter.probe(project_id, environment_id) -> dict`.
- Consumes: injected `request_json(method, path, payload=None, timeout=30)` callback.

- [x] **Step 1: Add failing tests for exact version, selected project/environment and unsupported build**

```python
probe = adapter.probe("project-a", "env-a")
assert probe["version"] == "v3.6.5-lts-f043cdd2"
assert probe["capabilities"]["ready"] is True
assert unsupported.probe("project-a", "env-a")["capabilities"]["can_push"] is False
```

- [x] **Step 2: Run the focused tests and verify RED**

- [x] **Step 3: Implement fixed official endpoints and capability evidence**

Use `/system/version/current`, `/project/get/{id}`, `/api/test/env-list/{id}`, `/api/definition/page`, `/api/case/page`, `/api/scenario/module/tree`, and `/api/report/scenario/page`.

- [x] **Step 4: Integrate live project/environment metadata without exposing environment details**

3.6.5 uses official endpoints automatically; legacy custom paths remain fallback only.

- [x] **Step 5: Run focused and existing backend tests**

Run: `python3 tests/metersphere_v365_adapter_checks.py && python3 tests/backend_static_checks.py`
Expected: PASS.

### Task 3: Idempotent API Case Materialization

**Files:**
- Modify: `task_server/services/metersphere_v365_adapter.py`
- Test: `tests/metersphere_v365_adapter_checks.py`

**Interfaces:**
- Produces: `upsert_plan_cases(plan) -> dict` with `remote_case_ids`, `created`, `updated`, `unchanged`, `blocked`.
- Produces: binding files under `api-testing/metersphere-bindings/<plan_id>.json` without request data.

- [x] **Step 1: Add failing definition-match, mapping, ambiguity, create/update/no-op and binding-recovery tests**

```python
first = adapter.upsert_plan_cases(plan)
second = adapter.upsert_plan_cases(plan)
assert first["created"] == 1
assert second["created"] == 0 and second["unchanged"] == 1
assert first["remote_case_ids"] == second["remote_case_ids"]
```

- [x] **Step 2: Run tests and verify RED**

- [x] **Step 3: Implement exact method/path definition matching**

Reject missing or ambiguous matches; never use name-only matching.

- [x] **Step 4: Materialize MeterSphere HTTP request and assertions**

Map path/query/header/body, status assertions, and schema structural coverage; reject unknown contract fields.

- [x] **Step 5: Implement stable ownership markers and content-hash upsert**

Only update an object whose marker, project and definition match the binding.

- [x] **Step 6: Run focused tests and verify GREEN**

### Task 4: Scenario Binding, Trigger, Poll And Report

**Files:**
- Modify: `task_server/services/metersphere_v365_adapter.py`
- Test: `tests/metersphere_v365_adapter_checks.py`

**Interfaces:**
- Produces: `upsert_plan_scenario(plan, remote_cases) -> dict`.
- Produces: `trigger_plan(plan_id) -> dict`, posting the official `ApiScenarioDebugRequest` with a client-generated UUID `reportId` and stable step `uniqueId`, and accepting only the server-returned `taskItem.reportId` as the real run ID.
- Produces: `get_run(report_id) -> dict` and `get_report(report_id) -> dict`.

- [x] **Step 1: Add failing scenario create/update/no-op and real-report-ID tests**

```python
trigger = adapter.trigger_plan(plan_id)
assert trigger == {"ok": True, "run_id": "remote-report-1", "status": "running"}
assert adapter.trigger_plan("missing")["ok"] is False
```

- [x] **Step 2: Run tests and verify RED**

- [x] **Step 3: Implement managed scenario module and API_CASE REF steps**

Use `/api/scenario/module/tree|add`, `/api/scenario/page|add|update|get/{id}` and bind the selected environment.

- [x] **Step 4: Implement trigger and terminal-state normalization**

Use `POST /api/scenario/run` and `/api/report/scenario/get/{report_id}`; never invent a run ID. Fail closed with `provider_terminal_state_missing` when all request steps are terminal but the main report remains non-terminal for more than five minutes.

- [x] **Step 5: Build a redacted report payload with remote case identities and summary**

- [x] **Step 6: Run focused tests and verify GREEN**

### Task 5: Existing Orchestrator Integration

**Files:**
- Modify: `task_server/services/metersphere_service.py`
- Modify: `tests/backend_static_checks.py`
- Modify: `package.json`

**Interfaces:**
- Consumes: adapter push/trigger/poll/report methods.
- Preserves: existing public routes and local `execution_id` state machine.

- [x] **Step 1: Add failing tests proving 3.6.5 ignores manual paths and legacy mode still works**

```python
assert push_plan_to_metersphere(plan_id)["adapter"] == "metersphere_v3.6.5"
assert create_metersphere_run(plan_id)["run_id"] == "remote-report-1"
```

- [x] **Step 2: Run focused/static tests and verify RED**

- [x] **Step 3: Route probe, metadata, push, run status and report through the v3.6.5 adapter**

Persist adapter selection in push/run records; keep compatibility helpers for non-matching versions.

- [x] **Step 4: Register the focused test in `npm test`**

- [x] **Step 5: Run `npm test` and verify GREEN**

### Task 6: Real QA Exit Evidence

**Files:**
- Modify: `CODEX_STATE.md`
- Modify: `docs/superpowers/specs/2026-07-22-production-evolution-roadmap.md`

**Interfaces:**
- Consumes: deployed QA MeterSphere `v3.6.5-lts-f043cdd2` and server-side credentials supplied by the user.
- Produces: redacted IDs/status/count evidence only.

- [x] **Step 1: Validate Access Key auth and exact version against QA**

Expected: key validation succeeds and exact version is reported.

- [x] **Step 2: Push one safe executable case twice**

Expected: first call creates or updates one managed case; second call reports unchanged and retains the same remote ID.

- [ ] **Step 3: Trigger the managed scenario and poll to a real terminal report**

Expected: non-empty remote report ID, explicit remote terminal status, and report counts. Current QA returns a real report ID and HTTP 200 step evidence but leaves the main report `PENDING`; the adapter correctly ends this as `provider_terminal_state_missing`, so the remote-terminal portion remains an environment blocker rather than a passed check.

- [x] **Step 4: Run the complete local verification suite**

Run: `npm test`
Expected: all suites PASS.

- [x] **Step 5: Update state and roadmap with evidence, inspect protected files, and commit without pushing**

```bash
git diff --check
git status --short
git add CODEX_STATE.md docs/superpowers task_server/services/metersphere_service.py task_server/services/metersphere_v365_adapter.py tests/metersphere_v365_adapter_checks.py tests/backend_static_checks.py package.json deploy/install-server.sh
git commit -m "Integrate MeterSphere 3.6.5 execution adapter"
```
