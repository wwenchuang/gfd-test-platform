# Video Repair Copy Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Teach Qwen the existing repair constraints and supply safe indexed feedback without changing acceptance.

**Architecture:** Existing observer prompt plus exact Counter-derived diagnostic metadata in the existing lock validator/correction path. No new provider protocol.

**Tech Stack:** Python, Markdown skills, pytest, existing Qwen model jobs.

## Global Constraints

Read `docs/superpowers/specs/2026-10-31-video-repair-copy-contract.md` in full; all its Global constraints apply verbatim. No agent provider/source/device/deploy/push actions. Dirty STATE/private exports remain unstaged.

### Task 1: Existing copy/ownership contract and safe missing-lock feedback

**Files:** Modify `ai_skills/prompts/ui_video_trajectory.observe.md`, `task_server/services/ui_video_trajectory.py`; create `tests/test_ui_video_repair_copy_contract.py`; extend existing contract tests only if necessary.

**Interface:** `_validate_positive_trigger_locks(locks,history,packet)` retains its exact pass/reject behavior, raises existing reason with bounded safe model_feedback. Missing identities use their first original lock index and Counter missing count. Correction text references audit_feedback.positive_trigger_locks and six literal fields. Do not match candidates semantically.

- [ ] Write failing synthetic tests: exact target adjective mutation rejected with safe index/count; each other protected field mutated; duplicate missing occurrence, reordered locks/history and regenerated IDs; rejected string absent from feedback; rendered prompt contract and all-event ownership; real correction loop retains questions/constraints. Record RED before production edits.
- [ ] Add minimal Counter-derived missing-index metadata and static Chinese feedback; explain exact fields/ownership in existing prompt. Keep underlying rejection and budgets unchanged. Preserve existing parent feedback. Record GREEN.
- [ ] Verify unchanged dynamic schema rejects future aiWaitFor/aiAssert; correct context response/state remains governed by existing rules. Run affected business episode/schema/runtime/temporal/prompt tests, mandatory mainchain py_compile, backend_static_checks.py and git diff --check.
- [ ] Self-review, commit only task files, report `.superpowers/sdd/repair-copy-contract-task-report.md` with RED/GREEN commands, counts, commit/files, preservation and limitations. No actual Qwen or deployment.

### Controller acceptance

- [ ] Independent task review and whole release review; preserve protected integration paths.
- [ ] Independently reviewed bounded diagnostic as specified, all actual raw and gates retained. Adjudicate real returned evidence before deployment/full30.
- [ ] Only after diagnostic supports hypothesis: integrate/push/exact package/serverSHA/deploy health; unchanged whole original; normal draft/SDK/review and designated phone acceptance. A source/draft/device verdict must be reported separately.
