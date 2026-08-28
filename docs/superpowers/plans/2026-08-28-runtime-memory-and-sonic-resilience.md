# Runtime Memory And Sonic Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the platform process from exhausting the QA host and make Sonic Eureka recover automatically after a process exit.

**Architecture:** Apply protection at the request, process, and container layers. The Python server bounds memory-amplifying inputs and concurrency, systemd contains the service, and a separate Sonic operations script configures Docker restart policy without coupling Sonic lifecycle to platform deployment.

**Tech Stack:** Python standard-library HTTP server, systemd, Bash, Docker CLI, pytest/static checks.

## Global Constraints

- Platform deployment must not automatically start, stop, or recreate Sonic containers.
- Normal JSON body limit is 64 MiB; raw report upload remains 300 MiB and streams to disk.
- Default request concurrency is 64, with at most 2 concurrent requests larger than 8 MiB.
- AI/Figma/mind-map/repair jobs use 2 persisted workers and an 8-item waiting queue.
- Default systemd limits are `MemoryHigh=2G`, `MemoryMax=3G`, and `TasksMax=256`.

---

### Task 1: Bound request memory

**Files:**
- Modify: `task_server/config.py`
- Modify: `task_server/response.py`
- Modify: `task_server/app.py`
- Modify: `task_server/router.py`
- Test: `tests/test_runtime_resource_guards.py`

- [x] Write failing tests for the 64 MiB JSON limit, streamed report upload, and request-slot rejection.
- [x] Run the focused tests and confirm they fail for missing behavior.
- [x] Implement the smallest request and streaming changes.
- [x] Run the focused tests and confirm they pass.

### Task 2: Bound process caches and expose runtime metrics

**Files:**
- Modify: `task_server/services/sonic_service.py`
- Create: `task_server/runtime_metrics.py`
- Modify: `task_server/router.py`
- Test: `tests/test_runtime_resource_guards.py`

- [x] Write failing cache-eviction and runtime-metric tests.
- [x] Run the focused tests and confirm they fail for missing behavior.
- [x] Add bounded eviction and health metrics without external dependencies.
- [x] Run the focused tests and confirm they pass.

### Task 3: Contain systemd and recover Sonic independently

**Files:**
- Modify: `deploy/install-server.sh`
- Modify: `deploy/midscene.env.example`
- Create: `deploy/configure-sonic-restart.sh`
- Modify: `deploy/README.md`
- Test: `tests/test_runtime_resource_guards.py`

- [x] Write failing assertions for the generated resource override and opt-in Sonic recovery behavior.
- [x] Run the focused tests and confirm they fail for missing behavior.
- [x] Generate the systemd resource override and implement the standalone Sonic script.
- [x] Run focused tests and Bash syntax checks.

### Task 4: Bound persisted background jobs

**Files:**
- Create: `task_server/background_jobs.py`
- Modify: `task_server/config.py`
- Modify: `task_server/app.py`
- Modify: `task_server/router.py`
- Modify: `deploy/install-server.sh`
- Modify: `deploy/midscene.env.example`
- Test: `tests/test_runtime_resource_guards.py`

- [x] Reproduce authentication being replaced by outer resource-limit HTTP 503.
- [x] Write a failing test for fixed workers, bounded waiting, and queue rejection.
- [x] Persist Figma and repair requests and queue only job IDs in memory.
- [x] Route generation, mind-map, Figma, repair, regeneration, and retry through the dispatcher.
- [x] Make legacy synchronous Figma/case/YAML routes share the same heavy-work slots.
- [x] Restore pending jobs and explicitly fail jobs interrupted by a service restart.
- [x] Scan all active persisted jobs on restart and preserve cancellation as the terminal state.
- [x] Add queue metrics to `/api/health` and keep API-testing authentication first.

### Task 5: Final verification and handoff

**Files:**
- Modify: `CODEX_STATE.md`

- [x] Run backend static checks and focused tests.
- [x] Run required Python compilation and shell syntax checks.
- [x] Run `git diff --check` and review the complete diff.
- [x] Update `CODEX_STATE.md` with the latest verified scope.
- [x] Commit the verified change set to `main`.
- [x] Push `main` and hand off deployment verification.

### Task 6: Stop API workbench startup timeout chains

**Files:**
- Modify: `api-testing-ui/src/views/WorkbenchView.vue`
- Modify: `api-testing-ui/src/stores/assets.ts`
- Modify: `api-testing-ui/src/stores/cases.ts`
- Modify: `api-testing-ui/src/stores/tasks.ts`
- Modify: `api-testing-ui/src/styles/app.css`
- Test: `api-testing-ui/src/views/WorkbenchView.spec.ts`

- [x] Reproduce the loading screen continuing after an initial Store timeout.
- [x] Add a failing regression for stopping downstream restoration and showing retry.
- [x] Use an 8-second startup read client without changing write-operation timeouts.
- [x] Stop after the first critical failure and keep prior AI job restoration off the first-paint path.
- [x] Ignore stale AI restoration responses after the user changes source versions.
- [x] Preserve unsaved edits from delayed AI version responses and fail routed version restoration atomically.
- [x] Complete independent review with no remaining critical or important findings.
- [x] Run focused tests, production build, and the complete API testing gate.
- [x] Amend the current commit and push `main`.
