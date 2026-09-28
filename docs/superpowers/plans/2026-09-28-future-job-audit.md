# Future job audit implementation plan

> Execute the bounded audit-only change in the existing isolated worktree. Historical records are out of scope per the user's latest instruction.

**Goal:** Record new job creation from the authenticated actor context and hash the exact YAML prepared for Runner dispatch.

**Architecture:** Extend the existing operation store; job metadata never grants permissions. Link dispatch to a unique creation event in the protected audit database, never to `created_by`, `initiator`, or provenance fields in job JSON. Preserve existing dispatch content and access policies.

**Constraints:** No old-record migration, backfill, deletion, or legacy trust promotion. No execution snapshots or ACL changes. A dispatch event means content prepared, not device execution success. Missing/ambiguous creation evidence leaves initiator unknown. Async workers without request context remain unknown until a separate context propagation change. Performance benchmarking is paused; avoid extra scans on idle Runner polls.

## Implementation and validation

- [x] Add failing tests in `tests/test_job_audit.py`: verified creator vs spoofed JSON, both creation paths, duplicate IDs, history untouched, unknown actors, dispatch exact transformed hash, no YAML secrets stored, empty poll, failed preparation, audit outage and concurrent creation.
- [x] Add `services/job_audit.py` for best-effort durable audit calls and indexed lookup in `operation_attribution.py`; record new creation inside the existing creation lock after successful persistence, reject duplicate IDs, append dispatch after YAML transformation. Do not alter job authorization or dispatch response.
- [x] Run the new tests, operation/identity/access/job regressions and required backend/static/compile/diff checks. Use an isolated operation database for all test runs and remove test data.
- [x] Update `CODEX_STATE.md` and evidence with results and uncovered async/device acceptance. Commit the bounded change. Deployment is pending restoration of the expired bastion session; authenticated production verification also requires platform login; never call local tests real Runner acceptance.
