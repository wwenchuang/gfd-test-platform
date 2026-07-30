# Native API Workbench Redesign

## Goal

Make API testing feel like one local workbench: Apifox provides assets and environments, the platform stores snapshots and runs tests, and AI helps design/edit/debug cases without exposing internal ids as the main workflow.

## References

- Postman: collection runs separate collection, environment, runner, and report.
- Apifox/Apidog: endpoint assets and automated test scenarios are separate concepts.
- Bruno/Hoppscotch: local-first API work is fast when requests, environments, and history stay close together.
- AI API testing practice: generate a coverage matrix and executable drafts from OpenAPI, then validate data and assertions before treating cases as regression baselines.

## Product Shape

The primary API surface is `API 工作台`. `报告历史` remains separate. Advanced asset management can remain reachable, but it should not be the normal path.

The workbench has four sections:

1. Current context: source, environment, base URL, token profile, snapshot version, endpoint count, and sync status.
2. Scope selection: module cards and module tree use backend snapshot counts. Large modules must show scope cost before starting AI generation.
3. AI draft and debug: AI generation is batch-based, visible, retryable, and never stuck forever in `running`. Draft cases can be reviewed, edited, and single-case debugged before baseline adoption.
4. Baseline and report: confirmed baselines run on the native executor. Reports separate pass, fail, skipped, environment/auth failures, and assertion failures.

## First Phase

This phase fixes the current confusing behavior without a broad rewrite:

- Module cards use backend `endpoint_count` and backend-selected endpoint ids, not a truncated frontend endpoint sample.
- Large module generation requires a short confirmation that states endpoint count, selected cap, batch count, and suggested child modules.
- Generation polling can convert stale running batches into recoverable failed batches. Successful plans remain linked and retry only reruns failed work.
- Generation UI shows timeout/retry state plainly and keeps generated batches reviewable.

## Non-Goals

- Do not reintroduce MeterSphere as the normal execution path.
- Do not rewrite Apifox parsing; current Figma/Apifox parsing behavior must remain intact.
- Do not change Agent UI automation, Midscene YAML, Runner, Sonic, or historical YAML.
