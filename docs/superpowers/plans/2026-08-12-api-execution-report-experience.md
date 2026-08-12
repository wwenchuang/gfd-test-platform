# API Execution and Diagnostic Report Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved structured API execution console and diagnostic test report using the platform's existing execution snapshots, durable SSE events, evidence, and failure analyses.

**Architecture:** Keep the backend execution model unchanged and add only the persisted event timestamp to outgoing SSE frames. In Vue, create presentation-only overview, evidence, and diagnostic report components; existing stores remain the single data source, and components emit edit, rerun, reconnect, and inspect actions without making their own API calls.

**Tech Stack:** Python 3.10, existing threaded HTTP API and PostgreSQL event store, Vue 3, Pinia, TypeScript, Vitest, Playwright, Lucide.

## Global Constraints

- Work directly on `main`; do not create a branch or worktree.
- Preserve deterministic `FAILED`, `BROKEN`, `SKIPPED`, `CANCELLED`, and `PASSED` semantics.
- AI may explain deterministic results but cannot replace execution states or counts.
- Reuse `ExecutionView`, `ExecutionEventView`, `ExecutionCaseResult`, and existing SSE endpoints.
- Do not add database tables, migrations, execution modes, or a parallel report API.
- Display only sanitized request, response, trace, and event payloads.
- Do not modify UI Agent, Midscene YAML, Runner, Sonic, or historical YAML.

---

### Task 1: Event Time and Shared Execution Presentation

**Files:**
- Modify: `task_server/api_testing/http.py`
- Modify: `tests/api_testing/test_http_contract.py`
- Modify: `api-testing-ui/src/api/contracts.ts`
- Modify: `api-testing-ui/src/stores/executions.ts`
- Modify: `api-testing-ui/src/stores/executions.spec.ts`
- Create: `api-testing-ui/src/utils/executionPresentation.ts`
- Create: `api-testing-ui/src/utils/executionPresentation.spec.ts`
- Create: `api-testing-ui/src/components/ExecutionOverview.vue`
- Create: `api-testing-ui/src/components/ExecutionOverview.spec.ts`

**Interfaces:**
- SSE payload adds reserved string field `_event_created_at` only when the durable event has a timestamp.
- `ExecutionEventView` adds optional `createdAt: string`.
- `executionPresentation.ts` produces status labels, terminal conclusions, duration, pass rate, summary counts, and deterministic failure buckets from `ExecutionView`.
- `ExecutionOverview` consumes one `ExecutionView` and renders task ID, environment, conclusion, counts, pass rate, and duration.

- [x] **Step 1: Write failing backend and frontend tests**

Add a backend test asserting a durable event created at `2026-08-12T07:09:38+00:00` produces an SSE `data` object containing `_event_created_at`. Add store tests asserting that field becomes `ExecutionEventView.createdAt`. Add utility tests for mixed `PASSED`, `FAILED`, `BROKEN`, and `SKIPPED` results, including truthful pass rate and failure buckets. Add a component test that verifies environment, conclusion, counts, and duration.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/api_testing/test_http_contract.py -k 'sse_event_timestamp' -q
npm --prefix api-testing-ui test -- --run src/stores/executions.spec.ts src/utils/executionPresentation.spec.ts src/components/ExecutionOverview.spec.ts
```

Expected: tests fail because the timestamp field, utility, and overview component do not exist.

- [x] **Step 3: Implement the minimal shared presentation layer**

Pass the event timestamp to `_write_sse` without changing persisted payloads. Parse it in the execution store. Implement pure functions for status labels and statistics, then render those values in `ExecutionOverview`.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the commands from Step 2. Expected: all selected tests pass.

### Task 2: Structured Execution Console and Evidence

**Files:**
- Create: `api-testing-ui/src/components/CaseEvidence.vue`
- Create: `api-testing-ui/src/components/CaseEvidence.spec.ts`
- Create: `api-testing-ui/src/components/ExecutionConsole.spec.ts`
- Modify: `api-testing-ui/src/components/ExecutionConsole.vue`
- Modify: `api-testing-ui/src/components/ExecutionLog.vue`
- Modify: `api-testing-ui/src/components/ExecutionLog.spec.ts`
- Modify: `api-testing-ui/src/components/CaseResultList.vue`
- Modify: `api-testing-ui/src/components/ExecutionDetailDrawer.vue`
- Modify: `api-testing-ui/src/components/ExecutionDetailDrawer.spec.ts`
- Modify: `api-testing-ui/src/views/RunsView.vue`

**Interfaces:**
- `CaseEvidence` consumes an `ExecutionCaseResult` and emits `edit` and `rerun`.
- `ExecutionConsole` emits `inspect(result)` in addition to existing execution actions.
- `ExecutionLog` receives optional `caseLabels: Record<string,string>` so filters and rows show case names rather than truncated IDs.
- `ExecutionDetailDrawer` accepts optional `initialCaseId` and delegates evidence display to `CaseEvidence`.

- [x] **Step 1: Write failing component tests**

Cover these behaviors separately: execution overview and three tabs render; realtime view shows case list beside logs; clicking a case emits the exact result; case filters show readable names; event timestamps render instead of sequence-only labels; user scrolling away from the bottom pauses following and shows a new-log notice; evidence renders request summary, response summary, individual assertion outcomes, trace, and failure analysis without exposing an authorization value.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```bash
npm --prefix api-testing-ui test -- --run src/components/ExecutionConsole.spec.ts src/components/ExecutionLog.spec.ts src/components/CaseEvidence.spec.ts src/components/ExecutionDetailDrawer.spec.ts
```

Expected: tests fail on missing layout, interactions, and evidence component.

- [x] **Step 3: Implement the structured console**

Use `ExecutionOverview` above tabs. Default to `实时轨迹`, render `CaseResultList` and `ExecutionLog` as a stable two-column workspace, add `用例明细` using the same list and evidence component, and add a compact `测试报告` projection. Keep filter state local. Pause automatic following on upward scroll and resume only through the follow control. Do not auto-open terminal execution drawers.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 3: Diagnostic Report History and Full Report

**Files:**
- Create: `api-testing-ui/src/components/DiagnosticReport.vue`
- Create: `api-testing-ui/src/components/DiagnosticReport.spec.ts`
- Create: `api-testing-ui/src/views/ReportsView.spec.ts`
- Modify: `api-testing-ui/src/components/ReportSummary.vue`
- Modify: `api-testing-ui/src/components/ReportSummary.spec.ts`
- Modify: `api-testing-ui/src/views/ReportsView.vue`

**Interfaces:**
- `DiagnosticReport` consumes one `ExecutionView` and emits `back`, `edit(result, execution)`, and `rerun(execution)`.
- It composes `ExecutionOverview`, `CaseResultList`, `CaseEvidence`, and deterministic failure buckets.
- `ReportsView` owns history/full-report navigation; the report component does not fetch data.

- [x] **Step 1: Write failing report tests**

Verify that report history rows show environment, conclusion, pass rate, counts, and duration without five repeated statistic tiles. Verify that the full report renders deterministic conclusion, failure categories, AI summaries when present, a case table/list, selected-case evidence, and technical log disclosure. Verify a parent failure does not relabel a passed child.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```bash
npm --prefix api-testing-ui test -- --run src/components/ReportSummary.spec.ts src/components/DiagnosticReport.spec.ts src/views/ReportsView.spec.ts
```

Expected: tests fail because diagnostic report and compact history behavior do not exist.

- [x] **Step 3: Implement diagnostic reports**

Replace repeated history grids with compact report rows. Render the approved diagnostic report in the page: conclusion, metrics, deterministic categories, existing AI analysis summaries, case details, failure evidence, and an expandable technical trace. Fall back to deterministic text when no AI analysis exists.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 4: Responsive Styling, Browser Flow, and Full Verification

**Files:**
- Modify: `api-testing-ui/src/styles/app.css`
- Modify: `tests/api_testing_ui_visual_check.js`
- Modify: `tests/api_testing_e2e.spec.mjs`
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Desktop execution detail uses two stable columns; report uses main detail and evidence side panel.
- Below 920 px execution sections stack; below 620 px metrics, filters, actions, and evidence remain readable without overlap.

- [x] **Step 1: Extend visual and browser acceptance checks**

Add assertions and screenshots for the execution overview, structured logs, diagnostic report, failure evidence, and mobile stacking. Extend the existing real Chromium API flow rather than creating a separate synthetic app.

- [x] **Step 2: Run visual and browser checks and verify RED**

Run:

```bash
node tests/api_testing_ui_visual_check.js
npx playwright test tests/api_testing_e2e.spec.mjs --project=chromium
```

Expected before final CSS/e2e adjustments: at least one new selector or screenshot assertion fails.

- [x] **Step 3: Implement responsive and visual adjustments**

Use restrained light surfaces, status colors, 6px-or-less radii, stable grid tracks, readable technical log contrast, and Lucide icons already in the project. Do not add decorative gradients, oversized cards, or nested cards.

- [x] **Step 4: Run the complete gate**

Run:

```bash
bash tests/run_api_testing_gate.sh
PATH="$PWD/.venv/bin:$PATH" npm run test:static
git diff --check
```

Expected: backend, Vue tests, typecheck, production build, desktop/mobile visual checks, Chromium real flow, and static checks all pass.

- [x] **Step 5: Review the implementation against the approved spec**

Confirm every acceptance criterion in `docs/superpowers/specs/2026-08-12-api-execution-report-experience-design.md` has implementation and test evidence. Record exact command outputs and any residual limitations in `CODEX_STATE.md`.

- [x] **Step 6: Commit and push main**

```bash
git add api-testing-ui task_server/api_testing/http.py tests CODEX_STATE.md docs/superpowers/plans/2026-08-12-api-execution-report-experience.md
git commit -m "Improve API execution details and reports"
git push origin main
```
