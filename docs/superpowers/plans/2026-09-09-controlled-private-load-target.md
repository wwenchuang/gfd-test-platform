# Controlled Private Load Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a page-configured, per-service authorization for controlled private load-test targets without disabling SSRF protections globally.

**Architecture:** Store the authorization in the existing immutable environment-service metadata. The HTTP executor reads only the selected service metadata and passes a scoped boolean to the pinned-address host policy. The UI edits and renders the flag alongside the service URL.

**Tech Stack:** Python 3, pytest, Vue 3, TypeScript, Vitest.

## Global Constraints

- Default behavior remains public-address only.
- Private authorization is scoped to one service in one immutable environment revision.
- Loopback, link-local, unspecified, multicast, and reserved destinations remain blocked.
- DNS pinning, configured-service binding, Host validation, and redirect boundaries remain enforced.

---

### Task 1: Scoped backend destination policy

**Files:**
- Modify: `tests/api_testing/test_executor.py`
- Modify: `task_server/api_testing/executor.py`

**Interfaces:**
- Consumes: `ResolvedEnvironment.service_metadata` and the selected request service name.
- Produces: `HostPolicy.resolve(url, allow_private_network=False)`.

- [x] **Step 1: Write failing tests** for authorized RFC1918 access, default denial, loopback denial, and service scoping.
- [x] **Step 2: Run the focused pytest cases** and verify failures are caused by the missing scoped option.
- [x] **Step 3: Add the minimum policy and executor wiring** while retaining all existing destination checks.
- [x] **Step 4: Run executor and environment tests** and verify all pass.

### Task 2: Environment page control

**Files:**
- Modify: `api-testing-ui/src/views/SettingsView.spec.ts`
- Modify: `api-testing-ui/src/views/SettingsView.vue`
- Modify: `api-testing-ui/src/styles/app.css`

**Interfaces:**
- Consumes: `EnvironmentServiceSnapshot.metadata.allow_private_network`.
- Produces: `services[*].metadata.allow_private_network` in `EnvironmentPayload`.

- [x] **Step 1: Write a failing component test** that opens environment editing, enables the private-network checkbox, saves, and asserts the metadata payload.
- [x] **Step 2: Run the focused Vitest file** and verify the missing control causes failure.
- [x] **Step 3: Add the compact checkbox, warning copy, load/save mapping, and layout styles.**
- [x] **Step 4: Run the focused frontend test and static checks.**

### Task 3: Integration verification and delivery

**Files:**
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: the completed backend and UI behavior.
- Produces: deployable main-branch commit and recorded live acceptance evidence.

- [x] **Step 1: Run required Python compilation, backend static checks, frontend static checks, production build, and `git diff --check`.**
- [ ] **Step 2: Commit and push the implementation to `main`.**
- [ ] **Step 3: After deployment, create a new environment version with the service flag enabled and rerun connectivity, single-user preflight, load execution, monitoring, and report checks in Safari.**
