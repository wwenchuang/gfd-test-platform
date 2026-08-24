# Shared API Response Assertions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make deterministic and AI API-case generation share contract-derived response assertion defaults without weakening explicit AI scenarios.

**Architecture:** Add a pure `ResponseAssertionPolicy` service that derives positive assertions from an endpoint operation and merges them into existing candidate assertions. `BasicCaseService` delegates default generation to it; `AiCaseService` invokes it immediately before strict payload parsing and persistence.

**Tech Stack:** Python 3, pytest, OpenAPI operation mappings, existing API case contracts.

## Global Constraints

- Do not add JSONPath assertions to array, binary, streaming, or non-JSON responses.
- Preserve explicit AI negative scenarios and specific business assertions.
- Never accept HTTP status alone as sufficient for a JSON business envelope when the contract supports deterministic business assertions.
- Do not expose or persist runtime secrets.

---

### Task 1: Shared Response Assertion Policy

**Files:**
- Create: `task_server/api_testing/services/response_assertion_policy.py`
- Create: `tests/api_testing/test_response_assertion_policy.py`

**Interfaces:**
- Produces: `ResponseAssertionPolicy.default_positive_assertions(operation) -> list[dict]`
- Produces: `ResponseAssertionPolicy.complete_candidate_assertions(assertions, operation) -> list[dict]`

- [x] **Step 1: Write failing policy tests**

Cover contract-derived `code == 0`, documented `code == 200`, `data exists`, specific AI data assertions, explicit negative status/business assertions, duplicate suppression, and non-JSON responses.

- [x] **Step 2: Run the policy tests and verify RED**

Run: `.venv/bin/python -m pytest tests/api_testing/test_response_assertion_policy.py -q`

Expected: collection fails because `response_assertion_policy` does not exist.

- [x] **Step 3: Implement the pure policy**

Move response-status and response-envelope inference out of `BasicCaseService`. Merge defaults only when the candidate represents a compatible success scenario, and return deep copies so caller payloads are not mutated unexpectedly.

- [x] **Step 4: Run the policy tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/api_testing/test_response_assertion_policy.py -q`

Expected: all policy tests pass.

### Task 2: Connect Both Generation Paths

**Files:**
- Modify: `task_server/api_testing/services/basic_case_service.py`
- Modify: `task_server/api_testing/services/ai_service.py`
- Modify: `tests/api_testing/test_basic_case_service.py`
- Modify: `tests/api_testing/test_ai_service.py`

**Interfaces:**
- Consumes: `ResponseAssertionPolicy.default_positive_assertions(operation)`
- Consumes: `ResponseAssertionPolicy.complete_candidate_assertions(assertions, operation)`

- [x] **Step 1: Write failing AI integration assertions**

Add an AI generation test whose model candidate contains only `status_code == 200`; require the persisted draft to contain the contract-derived `$.code == 0` and `$.data exists` assertions. Add a second test proving an explicit negative business-code candidate remains negative.

- [x] **Step 2: Run focused tests and verify RED**

Run: `TEST_DATABASE_URL='postgresql+psycopg://midscene:midscene@127.0.0.1:5432/midscene_api_testing' TEST_REDIS_URL='redis://127.0.0.1:6379/15' .venv/bin/python -m pytest tests/api_testing/test_ai_service.py -k 'response_assertion_policy' -q`

Expected: the positive draft lacks the derived business assertions before integration.

- [x] **Step 3: Delegate deterministic generation and normalize AI drafts**

Make `BasicCaseService._assertions()` delegate to the shared policy. In `AiCaseService._create_validated_draft()`, merge the candidate assertions after request binding and OpenAPI parameter completion, before secret checking and `parse_case_payload()`.

- [x] **Step 4: Run focused and regression tests**

Run the shared-policy tests, `tests/api_testing/test_basic_case_service.py`, and the focused AI integration tests. Then run the full API AI-service test module against local PostgreSQL/Redis.

Expected: all selected tests pass, including existing safety and validation cases.

### Task 3: Repository Verification and State

**Files:**
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: completed shared assertion behavior and verification evidence.

- [x] **Step 1: Run required checks**

Run Python compilation, `tests/backend_static_checks.py`, and `git diff --check`.

- [x] **Step 2: Record exact behavior and test evidence**

Append a dated `CODEX_STATE.md` section that distinguishes generated assertion consistency from real endpoint execution success.

- [x] **Step 3: Commit the implementation**

Commit only the shared policy, generation-path integrations, tests, design/plan, and state update with a focused message.
