# Forward Context Questions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route only genuinely attached future-context audit questions to their frozen owner without losing blockers or bypassing any current evidence gate.

**Architecture:** A pure ownership/partition helper separates unverified forward findings from current issues. Business runtime explicitly enables it, builds matching narrowed provider metadata, and the trajectory engine retains a derived queue while leaving raw audit responses and normal current-owned repair unchanged.

**Tech Stack:** Python existing runtime/trajectory, existing Markdown prompt, pytest, unchanged Qwen/Gateway.

## Global Constraints

Read the authoritative spec docs/superpowers/specs/2026-10-31-video-forward-context-questions.md in full. All its exact bounds, unchanged gates and evidence requirements bind this task. BUSINESS-only explicit boolean; default/legacy strict. Past context invalid; original owned cuts immutable. Forward findings always unverified/global needs_review, never actions/positive checks or automatic resolution. No provider/device/browser/deploy/source mutation by implementer. Do not stage CODEX_STATE.md or private exports. Preserve other dirty files.

---

### Task 1: Business-only forward context audit question routing

**Files:**
- Create: task_server/services/ui_video_context_questions.py (pure frozen ownership and partition/projection, no IO/provider).
- Modify: task_server/services/ui_video_trajectory.py (explicit capability, queue, delivery, local-only repair, derived diagnostics/ledger).
- Modify: task_server/services/ui_video_runtime.py (business activation, dynamically narrowed audit schema/reference metadata).
- Modify: ai_skills/prompts/ui_video_trajectory.audit.md (conditional v2 meaning; v1 stays strict).
- Test: tests/test_ui_video_forward_context_questions.py.
- Test affected existing runtime/prompt contract files only when new trusted v2 behavior requires it; retain every legacy negative control.

**Interfaces:**
- observe_trajectory(..., forward_context_issues=False) accepts only bool; enabling requires business_timeline dict.
- _audit_schema(payload, *, forward_context_issues=False) retains old direct behavior; true derives eligible futurecontext from actual attached role and current half-open cut, not model-provided allowlists.
- Pure helper functions use copied existing packet metadata and frozen ownership, return current issues plus structured forward findings. Exact function names are implementer-owned; report them and keep every caller consistent. Do not pass arbitrary provider values across error/IPC boundaries.
- No JSON schema root scaffold fields are added. Dynamic reference_contract v2 separates current and future owner IDs. New prompt rule is conditional on v2 so existing exact legacy semantics stay covered.

- [ ] Step1: Write generic tests showing a current audit with one valid future unresolved_evidence and one current issue is rejected under old behavior but safely partitioned under enabled mode; current checks untouched, future exact body/provenance retained. Add default strict/past/offimage/missing-owner/forged mapping negative controls before production edits.
- [ ] Step2: Run `python3 -m pytest -q tests/test_ui_video_forward_context_questions.py` and record expected RED failures from missing capability/partition support, not unrelated imports.
- [ ] Step3: Implement pure metadata validation and current/future partition, then runtime schema/reference v2 and explicit trusted business activation. Preserve canonical source schema shape and legacy v1 defaults. Re-run focused tests.
- [ ] Step4: Implement trajectory derived queue only after whole audit validation; leave raw unchanged, deliver to actual target observer/audit, retain all questions globally and ledger/proof refs even across acquisition/re-observation. Filter both current missing-action decisions and repair-feedback issues to current-owned findings. Add RED/GREEN controls for future missing_action/current repair/positive locks/later silence/empty claims/shards/failed later packet and unchanged final question cap.
- [ ] Step5: Run focused tests plus related trajectory/runtime/audit structured schema/packet contract/temporal/review-groups suites. Run mandatory `python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py`, `python3 tests/backend_static_checks.py`, `git diff --check`.
- [ ] Step6: Self-review against spec, commit ONLY named production/tests (no STATE/private outputs), and write .superpowers/sdd/forward-context-questions-task-report.md with RED/GREEN evidence, actual commands/counts, interfaces, files/commit, preservation and unresolved risks. No actual Qwen or devices from this task.

### Controller-only release acceptance

- [ ] Replay authentic run28 raw receipts without rewriting exports; verify exact schemas/partition/provenance and no future question becomes a confirmed/current action. Label this historical replay.
- [ ] Generate task review package; fresh independent spec+quality review then whole-release review, resolve findings.
- [ ] Fresh scoped mandatory checks; snapshot all protected primary paths and whole STATE, integrate only reviewed commits, verify tree/push/package/serverSHA/revision/health.
- [ ] One unchanged original regeneration, actual terminal export and all record hashes; full fixed baseline including both keyboard hides/cancel identities/no thirdNext/home. Normal draft review/static/SDK gates and designated real-phone trial remain required; no generated-case/device success claim from unit tests or deployment alone.
