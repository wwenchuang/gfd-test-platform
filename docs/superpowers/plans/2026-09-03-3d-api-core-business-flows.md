# 3D API Core Business Flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute a small v11 baseline suite that proves the critical 3D home and shared API business flows without replacing the 291-case full regression.

**Architecture:** Reuse proven old-version workflow cases as templates, but create separate v11 case identities so the old scheduled baseline remains untouched. Each admitted case acquires runtime identifiers through setup steps, verifies one central business action, and always cleans up its own mutations. Stable, shared, and hardware/AI-dependent flows remain separately runnable.

**Tech Stack:** Midscene Task Platform API testing backend, Vue API testing UI, PostgreSQL-backed case/baseline assets, Safari, production HTTP executor.

## Global Constraints

- Use project `3D家用`, source `默认模块 v11`, and environment `生产环境（新）-腾讯云 v30`.
- Preserve all existing cases, baselines, schedules, reports, and historical executions.
- Do not deploy, restart, or operate the bastion host.
- Do not store credentials, tokens, Camera view tokens, private URLs, or long-lived IDs in the repository or saved cases.
- HTTP 200 is not business success; assert exact business codes and meaningful domain fields.
- Every mutation must be reversible and cleanup must run after main success or failure.
- Do not admit a real-print case unless cancellation and cancellation verification both pass.
- Keep AI/hardware flows outside the stable daily core suite.

---

### Task 1: Freeze the Current Inventory and Candidate Matrix

**Files:**
- Create: `docs/evidence/3d-api-core-flow-inventory-2026-09-03.json`
- Modify: `docs/superpowers/plans/2026-09-03-3d-api-core-business-flows.md`

**Interfaces:**
- Consumes: `/api/health`, `/api/api-testing/v1/context-options`, `/endpoints`, `/cases`, `/baselines`, `/scheduled-jobs`, `/executions`.
- Produces: a sanitized candidate matrix containing stable keys, current endpoint IDs, reusable case version IDs, data source, mutation class, and admission status.

- [ ] **Step 1: Verify the live revision and idle workload state**

Run `curl -sS http://101.34.197.12:8088/api/health` and require `release_revision=1aac3f31ec67b34204f655fa0a126033ac065b88`, `active_large_requests=0`, and `heavy_workloads_active=0` before starting executions.

- [ ] **Step 2: Read v11 endpoints and current assets without exposing secrets**

Use authenticated read-only platform endpoints and write only IDs, names, paths, methods, tags, state, counts, and sanitized execution summaries to the evidence JSON.

- [ ] **Step 3: Classify candidates**

Classify every candidate as `stable_read`, `stable_reversible`, `shared_stable`, `ai_extended`, `hardware_extended`, `blocked_external_data`, or `excluded_destructive`.

- [ ] **Step 4: Validate the evidence file**

Run `python3 -m json.tool docs/evidence/3d-api-core-flow-inventory-2026-09-03.json >/dev/null` and confirm no value matches token, authorization, cookie, password, secret, or private URL fields.

### Task 2: Create Current-Version Stable Home Flow Cases

**Files:**
- Modify: `docs/evidence/3d-api-core-flow-inventory-2026-09-03.json`

**Interfaces:**
- Consumes: v11 endpoint IDs and the proven old-version case structures recorded by Task 1.
- Produces: new v11 cases under `核心链路 / 家用 / 稳定回归`.

- [ ] **Step 1: Create new case identities**

Create v11 cases for model/favorite, device status, print history, download lifecycle, and learning read-only flows. Do not create new versions on old case identities because that would supersede existing active baselines.

- [ ] **Step 2: Validate each case against environment v30**

Call each case version validation endpoint and require zero blocking errors. Record warnings without silently changing business assertions.

- [ ] **Step 3: Open every case in Safari**

From 用例管理, search the exact `核心链路` name, open the case, inspect structured setup/main/cleanup content, and confirm the displayed source is v11.

### Task 3: Execute and Admit Stable Home Flows

**Files:**
- Modify: `docs/evidence/3d-api-core-flow-inventory-2026-09-03.json`

**Interfaces:**
- Consumes: Task 2 v11 case versions.
- Produces: completed debug execution IDs and active baseline IDs.

- [ ] **Step 1: Debug one stable flow at a time**

Use environment v30 and wait for a terminal execution state before starting the next mutation-heavy flow.

- [ ] **Step 2: Inspect detailed evidence**

For each result, inspect request, response, assertions, setup trace, cleanup trace, and sanitized technical log. Require exact business success and successful cleanup.

- [ ] **Step 3: Adopt only passed case versions**

Adopt using the debug execution case ID. Keep failed drafts outside the fixed core baseline and record the blocker.

- [ ] **Step 4: Repeat stable mutations**

Run the favorite and download lifecycle cases at least twice to prove cleanup prevents state leakage.

### Task 4: Build the Shared Stable Core Selection

**Files:**
- Modify: `docs/evidence/3d-api-core-flow-inventory-2026-09-03.json`

**Interfaces:**
- Consumes: the eight active shared baselines and their latest 8/8 execution evidence.
- Produces: a separately named shared core task/selection, preserving business scope.

- [ ] **Step 1: Reopen all eight shared baselines in Safari**

Verify application, business, request method/path, assertions, and latest execution evidence.

- [ ] **Step 2: Create a shared core task**

Create `3D核心链路-共享稳定回归` referencing only the verified shared baseline IDs.

- [ ] **Step 3: Execute the shared task**

Record the new execution ID and require 8/8 before marking this task ready.

### Task 5: Create and Execute the Combined Stable Core Task

**Files:**
- Modify: `docs/evidence/3d-api-core-flow-inventory-2026-09-03.json`

**Interfaces:**
- Consumes: active passed baseline IDs from Tasks 3 and 4.
- Produces: `3D核心业务链路回归` and a completed combined execution.

- [ ] **Step 1: Create the combined baseline task**

Reference only passed stable baseline IDs. Do not include AI, device-control, real-print, repair-draft, or one-time candidates.

- [ ] **Step 2: Execute once in Safari**

Confirm the production warning, start the task, and wait for completion.

- [ ] **Step 3: Verify report evidence**

Record execution ID, raw totals, duration, and notification state; inspect at least one read-only and one cleanup-bearing case.

### Task 6: Evaluate v11 Extended Flows and Request Exact External Data

**Files:**
- Modify: `docs/evidence/3d-api-core-flow-inventory-2026-09-03.json`

**Interfaces:**
- Consumes: the 25 uncovered v11 endpoints and live precondition responses.
- Produces: passed extended baselines or exact external-data requests.

- [ ] **Step 1: Try data discovery without mutation**

Check CAD, JD Home, EPOne, Camera, print, and shared prerequisites using list/status/capability endpoints. Do not execute physical device actions during discovery.

- [ ] **Step 2: Execute safe AI lifecycle candidates**

Where authorized by the approved design, create a task, extract its ID, poll within the documented limit, and delete/cancel it. Record cost/time implications.

- [ ] **Step 3: Stop at physical or missing-data boundaries**

For each blocked flow, record the exact field, acceptable state, source endpoint attempted, and why no safe dynamic source exists. Request only those values from the user.

### Task 7: Final Verification and Handoff

**Files:**
- Modify: `CODEX_STATE.md`
- Modify: `docs/evidence/3d-api-core-flow-inventory-2026-09-03.json`

**Interfaces:**
- Consumes: all execution, baseline, and task IDs from earlier tasks.
- Produces: a reproducible handoff distinguishing ready, failed, and externally blocked flows.

- [ ] **Step 1: Re-read live task and baseline counts**

Confirm all new assets are current-version assets and the original 291 activity history remains intact except for intentionally added new baselines.

- [ ] **Step 2: Reopen the combined task and report in Safari**

Verify task name, selected targets, execution result, report navigation, and detailed evidence.

- [ ] **Step 3: Validate local documentation**

Run `python3 -m json.tool docs/evidence/3d-api-core-flow-inventory-2026-09-03.json >/dev/null` and `git diff --check`.

- [ ] **Step 4: Commit and push the documentation**

Commit only the specification, plan, evidence, and `CODEX_STATE.md`; do not persist authentication material or raw sensitive responses.
