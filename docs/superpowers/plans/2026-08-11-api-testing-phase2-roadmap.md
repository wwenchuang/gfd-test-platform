# API Testing Phase 2 Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved M0-M3 API testing roadmap as four independently deployable and verifiable milestones.

**Architecture:** Extend the existing PostgreSQL/Redis/Celery/Vue API testing module. Keep Apifox manual-only, preserve immutable source/test/execution layers, and route all new HTTP behavior through `task_server/api_testing` without restructuring `router.py`.

**Tech Stack:** Python 3.10, SQLAlchemy 2, Alembic, PostgreSQL 16, Redis 7, Celery, Vue 3, Pinia, TypeScript, Vitest, Playwright, Server-Sent Events.

## Global Constraints

- Preserve `/api/api-testing/v1`, PostgreSQL, Redis, Celery, SSE, Vue 3, and AI Gateway boundaries.
- Do not refactor `task_server/router.py`.
- Apifox refresh remains manual and never overwrites an active revision without confirmation.
- AI drafts and summarizes; deterministic validation decides executability and status.
- Preserve truthful `PASSED` / `FAILED` / `BROKEN` / `SKIPPED` / `CANCELLED` child results.
- Never expose database passwords, Apifox tokens, business tokens, cookies, or environment secrets.
- Do not implement schedules, distributed leases, notifications, Mock, performance testing, security scanning, or full RBAC in M0-M3.
- Each milestone uses TDD, its own migration when needed, focused tests, the full API gate, browser verification, and a production acceptance checkpoint.

---

## Delivery Order

1. [M0 Production Closure](2026-08-11-api-testing-m0-production-closure.md)
2. [M1 Suite Orchestration](2026-08-11-api-testing-m1-suite-orchestration.md)
3. [M2 Execution Workflows](2026-08-11-api-testing-m2-execution-workflows.md)
4. [M3 Contract Impact and Audit](2026-08-11-api-testing-m3-impact-audit.md)

M1 may start only after the M0 readiness endpoint is green in production. M2 depends on the immutable suite and execution snapshot introduced by M1. M3 depends on the suite references and execution lineage introduced by M1-M2.

## Branch and Commit Strategy

- Start each milestone from the verified commit produced by the previous milestone.
- Keep migrations append-only: `0004` for M1, `0005` for M2, and `0006` for M3.
- M0 has no domain schema requirement; if implementation proves a schema change is unavoidable, stop and amend this roadmap before creating a migration.
- Use small commits named by behavior, not layer, for example:
  - `Add API testing readiness diagnostics`
  - `Block deployment on API database drift`
  - `Add immutable API suite versions`
  - `Execute API suites as validated DAGs`
  - `Add failed execution reruns`
  - `Report source revision impact`

## Milestone Gate

Run after every milestone:

```bash
API_TESTING_POSTGRES_PASSWORD='task5-test-postgres-only' \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
bash tests/run_api_testing_gate.sh

python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
python3 tests/ai_gateway_static_checks.py
git diff --check
```

Expected: every command exits `0`; the API gate reports all backend, frontend, build, visual, and Playwright checks passed.

## Production Gate

After deployment, do not infer readiness from process state. Verify:

```bash
curl -fsS http://127.0.0.1:8091/api/health
curl -fsS http://127.0.0.1:8091/api/api-testing/v1/readiness \
  -H "Authorization: Bearer ${TASK_SESSION_TOKEN}"
curl -fsS http://127.0.0.1:8088/ai-gateway/health
```

Expected: Task health has `ok=true`; API readiness has `ready=true` and matching migration revisions; AI Gateway reports `ok=true` on port `8090`.

Then complete the milestone-specific browser workflow and retain the execution ID, terminal state, report URL, and request IDs for any failures.

