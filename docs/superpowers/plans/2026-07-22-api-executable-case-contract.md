# Executable API Case Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert API plan drafts from prose-only cases into platform-validated executable request/assertion contracts with deterministic readiness and revision-stale execution gates.

**Architecture:** Add a pure API case-contract service between immutable OpenAPI endpoints and plan persistence. `api_test_plan_service` owns plan generation and revision state, while MeterSphere only receives the executable subset. AI proposes contract content through the existing `api_test_designer`; platform normalization and validation remain authoritative.

**Tech Stack:** Python standard library, existing JSON storage helpers, existing AI Gateway/Skill runtime, vanilla JavaScript/CSS, `unittest`, static checks, and Playwright.

## Global Constraints

- Preserve existing OpenAPI upload, Apifox sync, plan, MeterSphere and report routes.
- Unknown required values must produce `needs_review`; never invent credentials, identities, order IDs, phone numbers or business data.
- AI output cannot set authoritative readiness, revision freshness or execution eligibility.
- Old prose-only plans remain readable but cannot silently become executable.
- MeterSphere receives only cases whose platform readiness is `executable`.
- Do not modify UI Agent, Midscene YAML, Runner, Sonic, scorer or protected user files.
- Do not push; the user owns push and deployment.

---

### Task 1: Pure Executable Case Contract

**Files:**
- Create: `task_server/services/api_case_contract_service.py`
- Create: `tests/api_case_contract_checks.py`
- Modify: `package.json`

**Interfaces:**
- Consumes: endpoint dictionaries from `api_asset_service.build_revision_endpoints()`.
- Produces: `build_api_case_contract(endpoint, case_type, omitted_field="", proposed=None) -> dict`.
- Produces: `normalize_api_case_contract(case, endpoint, known_case_ids=None) -> dict`.
- Produces: `summarize_api_case_readiness(cases) -> dict`.

- [x] Write failing tests proving a documented example produces an executable positive request with a structured status/schema assertion.
- [x] Write failing tests proving missing required path/query/body data produces `needs_review` with exact missing locations and no fake value.
- [x] Write failing tests for negative omission, auth cases, unsupported parameter locations, invalid AI methods/paths/assertions and unknown dependencies.
- [x] Run `python3 tests/api_case_contract_checks.py -v` and confirm RED because the service does not exist.
- [x] Implement deterministic example/default/enum extraction, request materialization and assertion normalization.
- [x] Re-run the focused suite and register it in `npm run test:static`.

### Task 2: Plan Generation and AI Contract

**Files:**
- Modify: `task_server/services/api_test_plan_service.py`
- Modify: `ai_skills/prompts/api_test_designer.v1.md`
- Modify: `ai_skills/schemas/api_test_designer.schema.json`
- Modify: `ai-gateway/server.js`
- Modify: `tests/api_case_contract_checks.py`
- Modify: `tests/ai_gateway_static_checks.py`
- Modify: `ai_skills/evals/run_skill_evals.py`
- Create: `ai_skills/evals/fixtures/api_executable_contract.json`

**Interfaces:**
- Plan cases use structured `request`, `assertions`, `variables`, `dependencies`, `readiness`, plus existing display metadata.
- Plan AI metadata exposes normalized `decision_trace` with `skill`, `action`, provider/model, fallback, input hash, output summary, timing/success/error where available.

- [x] Add failing plan tests for local contracts, AI proposed values being revalidated, duplicate IDs and plan readiness counts.
- [x] Add failing Gateway/eval checks requiring explicit `api_test_designer: generate_case` and the new schema fields.
- [x] Update local case generation and AI normalization to call the pure contract service.
- [x] Update the prompt/schema to request the executable contract without MeterSphere-private fields.
- [x] Normalize and persist the API AI decision trace without storing prompt bodies or credentials.
- [x] Run focused contract, Gateway static and Skill eval suites.

### Task 3: Revision Freshness and Confirmation Gate

**Files:**
- Modify: `task_server/services/api_test_plan_service.py`
- Modify: `task_server/services/api_schema_diff_service.py`
- Modify: `tests/api_case_contract_checks.py`

**Interfaces:**
- Produces: `evaluate_api_plan(plan) -> dict`, adding `revision_state` and `execution_readiness`.
- Produces: `executable_api_cases(plan) -> list[dict]`.
- `confirm_api_test_plan(plan_id)` rejects stale plans and plans with zero executable cases.

- [x] Add failing tests for unchanged/add-only revision staying fresh, changed/removed selected endpoints becoming stale, and legacy plans becoming `needs_review`.
- [x] Implement active-revision comparison using deterministic diff/impact services.
- [x] Ensure list/get/confirm all return the same evaluated state without rewriting immutable historical plan input.
- [x] Re-run the focused suite.

### Task 4: MeterSphere Execution Boundary

**Files:**
- Modify: `task_server/services/metersphere_service.py`
- Modify: `tests/api_case_contract_checks.py`
- Modify: `tests/backend_static_checks.py`

**Interfaces:**
- `_execution_plan()` requires `execution_readiness.can_execute=true`.
- `_meter_payload_for_plan()` includes only `executable_api_cases(plan)` and emits contract version `api_case_contract/v1`.

- [x] Add failing tests proving draft, stale, zero-executable and all-needs-review plans cannot push/run.
- [x] Add a failing test proving mixed plans push only executable cases and retain total/excluded counts for audit.
- [x] Implement the boundary checks without changing MeterSphere remote paths.
- [x] Re-run focused and backend static suites.

### Task 5: Plan Console Readiness

**Files:**
- Modify: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `task-manager.html`
- Modify: `tests/frontend_static_checks.py`
- Modify: `tests/visual_smoke_check.js`

**Interfaces:**
- Plan detail renders executable/review counts, missing data, structured request/assertion summary and stale state.
- Confirm is disabled when `execution_readiness.can_confirm` is false; execution is disabled when `can_execute` is false.

- [x] Add failing static/Playwright checks for readiness counts, missing-data visibility and disabled stale/zero-executable actions.
- [x] Render compact case readiness without nested cards or exposing secrets.
- [x] Bump frontend cache versions and verify desktop/mobile overflow.
- [x] Run frontend static and visual smoke suites.

### Task 6: Completion Evidence

**Files:**
- Modify: `CODEX_STATE.md`
- Modify: this plan checklist

- [x] Run `python3 tests/api_case_contract_checks.py -v`.
- [x] Run `python3 tests/api_asset_sync_checks.py -v`.
- [x] Run `python3 -m py_compile` for all changed Python modules.
- [x] Run `git diff --check` and a credential scan.
- [x] Run full `npm test`.
- [x] Inspect the staged file list and exclude every protected user file.
- [x] Commit the Phase B implementation without pushing.
