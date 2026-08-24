# AgileTC Read-Only Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only Token and TOTP-backed AgileTC authentication while preserving the existing report metadata contract.

**Architecture:** A focused `CasePlatformClient` owns authentication, cookies, retries, and JSON transport. `case_platform_service.py` keeps search and normalization responsibilities and delegates requests to the client.

**Tech Stack:** Python standard library (`urllib`, `http.cookiejar`, `hmac`, `hashlib`, `base64`, `threading`), pytest.

## Global Constraints

- Never commit or log access tokens, passwords, TOTP seeds, or generated codes.
- Do not read or persist AgileTC `caseContent`.
- Preserve anonymous mode unless `CASE_PLATFORM_AUTH_REQUIRED=true`.
- Retry an expired credential session at most once.
- Run `python3 tests/backend_static_checks.py` and `git diff --check`.

---

### Task 1: Authenticated AgileTC HTTP Client

**Files:**
- Create: `task_server/services/case_platform_auth.py`
- Test: `tests/test_case_platform_auth.py`

**Interfaces:**
- Produces: `CasePlatformClient(base_url: str, timeout: int)` and `request_json(path, params=None)`.
- Produces: `generate_totp(secret: str, timestamp: float | None = None) -> str`.
- Produces: `reset_case_platform_client_cache() -> None` for deterministic tests/config reloads.

- [ ] **Step 1: Write failing TOTP and required-auth tests**

```python
def test_generate_totp_uses_rfc6238_sha1_vector():
    assert generate_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", timestamp=59, digits=8) == "94287082"

def test_required_auth_rejects_missing_credentials(monkeypatch):
    monkeypatch.setenv("CASE_PLATFORM_AUTH_REQUIRED", "true")
    with pytest.raises(CasePlatformAuthError, match="未配置"):
        CasePlatformClient("http://agiletc.test", 5)
```

- [ ] **Step 2: Run tests and confirm the missing module/API failure**

Run: `.venv/bin/python -m pytest tests/test_case_platform_auth.py -q`

Expected: FAIL because `case_platform_auth` does not exist.

- [ ] **Step 3: Implement environment config and standard-library TOTP**

Implement strict Base32 decoding, RFC 6238 HMAC-SHA1 code generation, boolean
environment parsing, and sanitized configuration validation.

- [ ] **Step 4: Add failing Token attachment and TOTP login-cookie tests**

Use a fake opener that records `Request` headers/bodies and returns JSON plus a
fake login cookie state. Assert credentials are present only in the login body
and never in raised errors.

- [ ] **Step 5: Implement Token and service-account request modes**

Implement one cookie-aware opener per client, lazy login under an `RLock`, and
JSON GET/POST helpers with explicit `CasePlatformAuthError` and
`CasePlatformRequestError` exceptions.

- [ ] **Step 6: Add failing session-expiry retry test**

Return AgileTC code `100011` from the first metadata request, a successful login,
then a successful metadata response. Assert one login and two metadata calls.

- [ ] **Step 7: Implement exactly one credential refresh retry**

Invalidate only the authenticated flag and cookie jar, log in again, and retry
once. Token mode and the second failure must return a sanitized error.

- [ ] **Step 8: Run focused auth tests**

Run: `.venv/bin/python -m pytest tests/test_case_platform_auth.py -q`

Expected: all tests pass.

### Task 2: Existing Metadata Search Integration

**Files:**
- Modify: `task_server/services/case_platform_service.py`
- Modify: `tests/test_case_platform_integration_service.py`

**Interfaces:**
- Consumes: `get_case_platform_client(base_url: str, timeout: int) -> CasePlatformClient`.
- Preserves: `search_case_platform_cases(...) -> dict` response schema.

- [ ] **Step 1: Write failing delegation tests**

Patch `get_case_platform_client`, return list/detail payloads, and assert both
requests go through the same client while normalized report fields stay equal.

- [ ] **Step 2: Run focused integration tests and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_case_platform_integration_service.py -q`

Expected: FAIL because the service still calls `urllib.request.urlopen`.

- [ ] **Step 3: Delegate `_json_get` to the authenticated client**

Remove transport-specific exception handling from the metadata service, map
client exceptions to `CasePlatformError`, and preserve detail fallback.

- [ ] **Step 4: Run auth and integration tests**

Run: `.venv/bin/python -m pytest tests/test_case_platform_auth.py tests/test_case_platform_integration_service.py -q`

Expected: all tests pass.

### Task 3: Deployment Contract and Project State

**Files:**
- Modify: `deploy/README.md`
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Documents: server-only secret configuration in `/opt/midscene.env`.

- [ ] **Step 1: Document both authentication modes**

Add Token mode, TOTP mode, minimum AgileTC permissions, secret rotation, and a
restart requirement without inserting example secret values.

- [ ] **Step 2: Record implementation and verification in `CODEX_STATE.md`**

State that report metadata is unchanged and live production login still depends
on server credentials and AgileTC permission assignment.

- [ ] **Step 3: Run mandatory verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_case_platform_auth.py tests/test_case_platform_integration_service.py tests/test_mindmap_test_report_service.py -q
python3 tests/backend_static_checks.py
git diff --check
```

Expected: all tests/checks pass.

- [ ] **Step 4: Review secrets and commit**

Run `git diff --cached` and `rg` against changed files to verify no supplied
credential appears, then commit the implementation and push `main`.
