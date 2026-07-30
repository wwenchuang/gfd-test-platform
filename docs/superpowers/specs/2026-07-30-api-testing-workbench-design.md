# API Testing Workbench Design

## Goal

Make API testing feel like one local platform workflow instead of a chain of integration setup screens. Apifox is only the upstream interface asset source. After the platform pulls an Apifox snapshot, test design, debugging, baseline storage, execution, and reports all happen inside this platform.

## User Workflow

The primary page is a single API testing workbench:

1. Connect or select an Apifox project.
2. Pull and freeze a local interface snapshot.
3. Select modules or individual endpoints.
4. Generate API test cases with AI.
5. Edit and debug cases directly.
6. Save stable cases as API baselines.
7. Run baselines and inspect live logs, results, and failure analysis.

The user should not need to understand `source_id`, `snapshot_id`, `revision_id`, `plan_id`, binding fingerprints, or sync jobs during normal use.

## Information Architecture

The workbench has four visible sections.

### 1. Interface Snapshot

Shows only the current Apifox project, environment, base URL, snapshot time, endpoint count, and update status.

Primary actions:

- `Update Apifox Snapshot`
- `Change Project`
- `View Snapshot History`

Behavior:

- Automatic periodic sync is no longer the main model. The default is a frozen local snapshot.
- Updating Apifox creates a new local snapshot and keeps old snapshots available for comparison.
- If the current baseline was generated from an older snapshot, show a concise stale marker and a button to review changes.

### 2. Endpoint Scope

A compact two-column selector:

- Left: module tree with endpoint counts.
- Right: endpoint table with method, path, Chinese name or summary, required data status, and selected state.

Primary actions:

- Search endpoints.
- Select module.
- Select individual endpoints.
- Continue with current selection.

The UI should not expose asset/revision terminology here.

### 3. AI Cases

Generated cases appear as editable cards, not raw JSON by default.

Each card shows:

- Case name.
- Endpoint and method.
- Request data summary.
- Dependencies or extracted variables.
- Assertions.
- Readiness: executable, needs data, or needs review.

Primary actions:

- `Generate AI Cases`
- `Regenerate Selected`
- `Debug This Case`
- `Save as Baseline`

The JSON contract remains available in a folded technical detail editor for troubleshooting, but the normal editor is field-based.

Important behavior:

- Users can debug draft cases before saving them as baselines.
- Baseline adoption is for stable reuse, not a prerequisite for trial execution.
- AI may propose cases and data, but platform validation decides executable readiness.

### 4. Execution and Report

The bottom/right execution area shows live progress:

- Current run state.
- Log stream.
- Passed, failed, skipped, and blocked counts.
- Current case.
- Latest report summary.

Reports show:

- Environment and base URL.
- Summary cards.
- Case-by-case result table.
- Request/response details.
- Assertion failures.
- Deterministic failure classification.
- AI failure analysis entry point for deeper diagnosis.

## Data Model Direction

Keep existing backend concepts where useful, but wrap them in user-facing terms:

- `api_source` becomes Apifox connection.
- `api_asset` / `api_revision` become local snapshot.
- `api_plan` becomes API case set or baseline.
- `api_execution` remains execution record.
- `api_report` remains report.

No destructive migration is required for the first pass. Existing records should still load. New UI labels and helper APIs can present a simpler workbench contract over the current storage.

## Backend API Direction

Add a workbench-oriented facade instead of making the frontend assemble every internal object:

- `GET /api/api-testing/workbench`
  Returns selected source, active snapshot, endpoint scope, latest case sets, active runs, recent reports, and readiness summary.

- `POST /api/api-testing/snapshots/update`
  Pulls Apifox and creates a frozen local snapshot.

- `POST /api/api-testing/cases/generate`
  Starts AI case generation from source, snapshot, and selected endpoints/modules.

- `POST /api/api-testing/cases/debug`
  Runs one draft or baseline case through the native executor.

- `POST /api/api-testing/baselines`
  Saves selected draft cases as reusable baselines.

Existing lower-level routes can stay for compatibility and tests, but the main page should depend on the facade.

## Frontend Design

The primary screen should be dense and readable, closer to an execution console than a settings wizard.

Layout:

- Top status strip: project, environment, snapshot, endpoint count, readiness.
- Left column: module and endpoint selection.
- Center: AI case cards and editor.
- Right/bottom panel: execution log and latest report.

Hidden by default:

- Source IDs.
- Snapshot IDs.
- Binding fingerprints.
- Raw JSON.
- Sync logs.
- Internal generation IDs.

Visible when useful:

- Snapshot age.
- Base URL.
- Missing token or missing base URL.
- API case readiness.
- Execution result and failure reason.

## Error Handling

Errors should tell the user the next useful action:

- Apifox token invalid: ask to replace token.
- Snapshot missing base URL: ask to re-read the selected Apifox environment.
- Case needs data: open the field editor and highlight missing fields.
- Auth missing: open the business token panel.
- Execution failure: show request, response, assertion, and classification.

Do not label local execution failures as Apifox sync failures.

## Testing

Focused checks:

- Workbench facade returns a simple aggregate payload without secrets.
- Snapshot update freezes Apifox data locally and keeps prior snapshots.
- Draft API cases can be debugged without baseline adoption.
- Baseline adoption stores selected cases and preserves source snapshot metadata.
- Frontend static checks ensure the main UI no longer exposes MeterSphere or sync-first wording.
- Native execution tests continue to cover auth, execution, report, and failure classification.

## Out of Scope

- Reintroducing MeterSphere.
- Deleting existing historical API records.
- Full data migration of old source/asset/plan IDs.
- Making AI results bypass platform readiness validation.
- Building a separate multi-step wizard as the primary API flow.
