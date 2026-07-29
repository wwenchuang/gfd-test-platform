# API Baseline Workflow Design

## Goal

Expose confirmed AI-generated API plans as a separate, versioned baseline
workspace without adding a second copy of the plan or a competing lifecycle.

## Existing Model

The platform already has the required baseline semantics:

- AI generation creates a persisted `draft` plan.
- Platform contract checks mark each case `executable` or `needs_review`.
- Confirmation changes the plan to `confirmed`.
- The plan remains bound to an immutable OpenAPI revision and selected endpoint
  keys.
- Schema diffs mark a confirmed plan `stale` and identify affected cases.
- MeterSphere executes one confirmed plan at a time and keeps run history.

Adding a separate `baseline` status or copying cases to another store would
create invalid combinations such as `confirmed + candidate` and duplicate
revision-impact logic. The UI should instead treat `confirmed` as the adopted
baseline projection.

## Reference Patterns

- Apifox AI generates candidate cases, supports trial runs, and requires
  accepting selected cases into the maintained case set.
- Postman keeps specification-derived collections synchronized with OpenAPI,
  while collections and folders remain independently runnable test suites.
- Postman Collection Runner and CLI run one collection or folder and retain run
  history.
- ReadyAPI separates reusable test steps from test data so stable suites do not
  need to be regenerated for each run.

## User Flow

1. Sync an immutable Apifox/OpenAPI revision.
2. Select business modules and endpoints.
3. Let AI create candidate plans.
4. Review platform readiness results and adopt an executable plan as baseline.
5. View adopted plans in a dedicated API baseline workspace.
6. Run one baseline through the existing MeterSphere execution workflow.
7. When the OpenAPI revision changes, keep the baseline visible but mark it as
   needing regeneration; never silently rewrite it.

## UI Changes

- Add `API 基线` under the API testing navigation.
- The AI plan page lists candidate drafts only.
- Rename the confirmation action to `采纳为基线`.
- The baseline page shows:
  - project/source name;
  - module scope;
  - endpoint and executable case counts;
  - adopted time;
  - fresh/stale revision state;
  - actions to inspect cases, regenerate stale plans, or enter execution.
- Rename confirmed-plan execution copy to baseline execution copy.

## Non-Goals

- No new execution mode.
- No duplicated case files or plan records.
- No automatic remote write to Apifox.
- No silent baseline mutation after an API change.
- No changes to UI Agent, Sonic, Runner, Figma, or historical YAML.

## Acceptance

- AI candidates and adopted baselines are visibly separate.
- Every baseline is still a normal confirmed plan and remains executable by the
  existing MeterSphere service.
- Stale baselines remain inspectable and show the existing affected-case state.
- Direct navigation works with no prior API page visit.
- Desktop and mobile views have no horizontal overflow.
