# Performance Report Executive Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the performance report hero with a decision-first five-part summary for leadership readers.

**Architecture:** Derive presentation-only summary text from the existing deterministic report, run configuration, monitoring snapshot, and validated next-run policy. Keep detailed evidence components unchanged below the summary.

**Tech Stack:** Vue 3, TypeScript, Vitest, Vite, Safari accessibility inspection.

## Global Constraints

- Do not infer capacity or safety from a passing small run.
- Do not present missing monitoring or samples as zero.
- AI may supply a validated next action but may not change the deterministic verdict or start a run.
- Preserve history, export, evidence, monitoring, and AI detail workflows.

---

### Task 1: Lock the summary contract

**Files:**
- Modify: `api-testing-ui/src/views/LoadReportsView.spec.ts`

**Interfaces:**
- Consumes: existing `LoadReport`, `LoadRun`, and `LoadAiAnalysis` fixtures.
- Produces: DOM contract `data-testid="load-report-executive-grid"` with five labelled summary items.

- [ ] Add a failing test that expects 测了什么、怎么测的、结果怎么样、主要风险、下一步做什么 and verifies missing monitoring is called out.
- [ ] Run `npm test -- --run src/views/LoadReportsView.spec.ts` from `api-testing-ui` and confirm it fails because the five-item grid does not exist.

### Task 2: Implement deterministic summary derivation and layout

**Files:**
- Modify: `api-testing-ui/src/views/LoadReportsView.vue`

**Interfaces:**
- Consumes: `selectedRun`, `report`, `monitoring`, `analysis`.
- Produces: computed test subject, pressure description, risk, and next-action strings.

- [ ] Add minimal computed values that preserve unknown states and use validated `next_run_strategy.can_prefill` only for the optional next action.
- [ ] Replace the three-column hero facts with the five-item executive grid; keep verdict and explanation above it.
- [ ] Add responsive styling for a readable desktop grid and sequential mobile cards.
- [ ] Re-run the focused test and confirm it passes.

### Task 3: Verify regressions and browser presentation

**Files:**
- Modify: `CODEX_STATE.md`
- Regenerate: `api-test/`

**Interfaces:**
- Consumes: built frontend and report fixture.
- Produces: deployable static assets and verification evidence.

- [ ] Run all frontend tests, production build, frontend static checks, backend static checks, and `git diff --check`.
- [ ] Inspect a failed report and a report with real monitoring data in Safari at desktop and narrow widths; confirm the five-item order, no overflow, and explicit evidence gaps.
- [ ] Update `CODEX_STATE.md`, commit on `main`, and push `origin main`.

