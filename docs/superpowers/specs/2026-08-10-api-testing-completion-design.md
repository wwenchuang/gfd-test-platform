# API Testing Completion Design

## Goal

Complete the three gaps left after API Testing Phase 1 without expanding the main navigation:

1. manually refresh saved Apifox projects and environments without requiring a JSON export;
2. persist one lightweight API test task across AI design, debugging, baseline execution, and reporting;
3. verify the real "My Favorites" workflow after deployment.

## Product Flow

The primary workflow remains one workbench:

```text
Select saved project and environment
  -> choose endpoints
  -> AI generates editable drafts
  -> debug drafts
  -> adopt passing versions as baselines
  -> run the current task
  -> inspect live logs and the final report
```

The API asset page is the only source-management surface. It offers:

- `Check Apifox updates` for a saved Apifox connection;
- a three-step connection flow: save token, select project/branch/environment, preview changes;
- explicit confirmation before a new immutable source revision and editable environment revision become active;
- JSON upload under an `Advanced import` disclosure as an offline fallback.

Opening the workspace never contacts Apifox. Refresh is manual only.

## Apifox Boundary

Use the official Apifox OpenAPI export endpoint for the OpenAPI document. Use the installed Apifox CLI only for read-only discovery of projects, branches, environments, service URLs, and variables. This avoids depending on an unsupported CLI export command while retaining the richer environment metadata already proven by the previous platform implementation.

The access token is encrypted with the existing API testing secret key. Public responses expose only `configured`, `fingerprint`, and update time. Connection metadata stores project, branch, environment, and provider display names but never the token.

Refresh creates an expiring source diff. Confirmation activates the immutable source revision and writes a new local editable environment revision. Sensitive environment values discovered from Apifox are represented as configured placeholders and are never copied into logs, reports, or AI prompts. Local environment edits never write back to Apifox.

## Test Task Boundary

Add a durable `ApiTestTask` record with:

- project, source revision, and environment revision;
- selected endpoint IDs;
- current state and display name;
- latest AI job and execution references;
- a small summary for restoration.

The workbench automatically restores the latest non-terminal task for the signed-in user. Starting AI generation creates or updates the task. Debugging and execution advance its state. A terminal execution attaches the final report summary. The task is not a new menu item and does not duplicate execution records.

## Error Handling

- Apifox authentication, permission, timeout, and malformed-output errors use stable Chinese messages and always keep JSON upload available.
- A failed refresh cannot replace the active source or environment revision.
- A task may reference only endpoints, source revisions, and environments from the same project and owner scope.
- AI or execution failure preserves the task and offers retry or resume instead of silently creating a duplicate task.
- All execution statuses retain the existing `PASSED`, `FAILED`, and `BROKEN` semantics.

## Verification

Automated verification must cover:

- encrypted Apifox credentials and token redaction;
- project/context discovery, OpenAPI refresh preview, environment persistence, and manual confirmation;
- JSON fallback remaining available;
- task creation, restoration, scope validation, AI association, debug association, and final execution summary;
- Vue connection flow and restored task state;
- the existing Python, Vue, static, visual, and browser suites.

After deployment, run the three real "My Favorites" endpoints with the configured production environment and business token. Verify AI generation, editing, debug, baseline adoption, task execution, SSE logs, truthful report counts, failure analysis, and absence of secret leakage.

## Explicit Non-Goals

This completion does not add scheduling, Mock management, email notifications, distributed worker leases, trends, performance/security testing, or a general test-suite DAG. Those remain separate later phases.
