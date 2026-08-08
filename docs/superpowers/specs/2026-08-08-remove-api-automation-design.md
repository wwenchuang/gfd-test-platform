# Remove Existing API Automation Design

## Goal

Remove the current API automation product and runtime completely so a future API testing experience can be designed from a clean boundary.

This change removes executable API automation code. It does not design or scaffold the replacement.

## Decisions

- Remove the existing API automation UI, backend routes, services, AI skill integration, deployment dependency, and API-specific tests.
- Preserve existing SQLite files and server-side API history as read-only recovery data. The running application must no longer open, migrate, or write those files.
- Preserve UI Agent, Midscene YAML, Runner, Sonic, reports, and all non-API workflows.
- Preserve historical design documents and screenshots as records. They are not loaded by production code.
- Do not add a compatibility redirect, feature flag, placeholder page, or replacement API module in this change.

## Runtime Removal Scope

### Frontend

- Remove the `接口自动化` navigation group and all API workflow keys.
- Stop loading `js/api.js`, `js/api-testing.js`, and `js/api-test-lab.js`.
- Delete those three API-only scripts.
- Remove API-only navigation and toolbar branches from shared scripts.
- Remove API-only CSS from `css/round5.css` while preserving shared layout and Agent styles.
- Old browser state pointing to an API workflow must fall back to the normal platform default view without throwing a JavaScript error.

### Backend

- Remove every `/api/api-testing/*` and `/api/test-lab/*` route.
- Delete the API-only and Apifox-only services:
  - `api_asset_service.py`
  - `api_case_contract_service.py`
  - `api_execution_service.py`
  - `api_module_service.py`
  - `api_plan_generation_service.py`
  - `api_report_service.py`
  - `api_schema_diff_service.py`
  - `api_source_service.py`
  - `api_sync_service.py`
  - `api_task_service.py`
  - `api_test_plan_service.py`
  - `api_workbench_service.py`
  - `api_workspace_service.py`
  - `apifox_discovery_service.py`
  - `apifox_service.py`
  - `test_lab_service.py`
- Remove API-only imports, startup hooks, and summary fields from shared server modules.
- Requests to old API automation routes return the platform's normal `404 Not Found` response.

### AI Gateway and Skills

- Delete `api_test_designer.v1.md` and its schema.
- Remove the `api_test_designer -> generate_case` Gateway routing entry.
- Remove API designer checks and eval registration without changing other AI skills.

### Deployment

- Remove Apifox CLI installation and version checks from server deployment.
- Keep the rest of the deployment process unchanged.

### Tests

- Delete API- and Apifox-specific test modules.
- Remove API-only assertions from shared frontend, backend, Gateway, and visual checks.
- Keep historical screenshots and specifications; they are not production dependencies.

## Preserved Data

The deletion must not remove or mutate:

- `LEARNING_DIR/test-lab/test_lab.sqlite3`
- any `TEST_LAB_DIR` or `TEST_LAB_DB_PATH` target
- prior API execution reports or uploaded artifacts outside tracked runtime code
- UI YAML files and their source directories
- unrelated dirty worktree files

The old SQLite schema is intentionally not migrated. A future design may import selected records through a separate, explicit migration.

## Failure Handling

- A stale local browser workflow key must not leave the page blank; it falls back to the default task view.
- Old API URLs return 404 rather than a misleading success or compatibility response.
- Server startup must not depend on the Apifox CLI, API database, or API environment variables.

## Verification

The removal is complete when:

1. The platform starts and both health endpoints remain healthy.
2. No API automation group appears in the sidebar.
3. Old API automation routes return 404.
4. Runtime code contains no imports of removed services and no API automation script tags.
5. Deployment no longer installs or validates Apifox CLI.
6. Frontend, backend, and AI Gateway static checks pass after their obsolete API assertions are removed.
7. UI Agent, YAML, Runner, and Sonic focused checks still pass.
8. The existing SQLite and historical API data files remain untouched.

## Out of Scope

- Designing the replacement API testing product.
- Choosing a new database schema.
- Migrating old API records.
- Adding new API routes, pages, services, or execution behavior.
