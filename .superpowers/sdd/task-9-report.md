# Task 9 Report: Vue Application Shell and Saved Workspace Context

## RED

- Created `api-testing-ui/src/stores/context.spec.ts` before the application package existed.
- `npm --prefix api-testing-ui test -- --run src/stores/context.spec.ts` failed with `ENOENT` for `api-testing-ui/package.json`, as expected by the task brief.
- Added the static navigation assertions before the link. `python3 tests/frontend_static_checks.py` failed with `Sidebar must include a same-tab API testing link`.

## GREEN

- `context.spec.ts` now passes. It verifies `GET /api/api-testing/v1/workspace`, reads the real `{data:{workspace:{project_id,source_revision_id,environment_revision_id}}}` response, and does not start a source refresh.
- The isolated Vue 3 + TypeScript + Vite + Pinia + Router application has workbench, assets, runs, reports, and settings routes. Its production base is `/api-test/` and its build output is committed in `api-test/`.
- The typed API client reads only `sessionStorage.sessionToken`, sends only a Bearer authorization header, and clears session auth values before a same-tab redirect to `/task-manager.html?return_to=%2Fapi-test%2F` on missing or rejected authentication. It does not store business tokens.
- The context store restores and saves only the three workspace IDs through the Task 8 workspace endpoint.
- `task-manager.html` has one same-tab API testing link. Static checks reject restored legacy API workflows, letter-box navigation, and an API test link with any `target` attribute.

## Verification

```text
npm --prefix api-testing-ui install --registry=https://registry.npmmirror.com --no-audit --no-fund --progress=false
up to date in 841ms

npm --prefix api-testing-ui test -- --run
1 test passed

npm --prefix api-testing-ui run build
vite v5.4.21: 1589 modules transformed; api-test/ emitted successfully

python3 tests/frontend_static_checks.py
{'ok': True, 'checks': 72}

git diff --check
passed
```

## Dependency Note

The default npm registry could not resolve in this environment (`ENOTFOUND`). Installation was completed with the user-specified `https://registry.npmmirror.com` command; no registry setting was written to global or project configuration.
