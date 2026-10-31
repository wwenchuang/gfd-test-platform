# Visible Target Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement the task and obtain fresh task and whole-change reviews.

**Goal:** Guide Qwen to distinguish evidence-backed region interactions and independent business advances from unproven residual feedback or ongoing asynchronous responses.

**Architecture:** Add the same concise generic evidence distinctions to current observer and independent auditor prompts. Retain existing validation and original evidence domains; no automatic event creation or gate relaxation. Root owns bounded real-model proof and fresh normal original-video acceptance.

**Tech Stack:** Existing prompt templates, Python prompt rendering/contract tests, unchanged Qwen stage policy.

## Global Constraints

The authoritative binding constraints are the Global constraints section of `docs/superpowers/specs/2026-10-31-video-visible-target-boundaries.md`, copied verbatim into each task/review brief. No production changes outside the two named prompts.

### Task 1: Clarify visible target regions and evidence-backed causal boundaries

**Files:**
- Modify: `ai_skills/prompts/ui_video_trajectory.observe.md`
- Modify: `ai_skills/prompts/ui_video_trajectory.audit.md`
- Create: `tests/test_ui_video_visible_target_boundaries.py`

**Interfaces:**
- Consume unchanged current prompt payloads, rendering API, action and audit reference contracts.
- Produce unchanged raw schemas and action/domain contracts; only guidance text changes.

- [ ] Read the spec, current prompts and relevant existing prompt rendering tests. Before implementation write RED tests rendering the actual observer and v1/v2 auditor templates and requiring all three generic distinctions: visible unlabelled semantic region requires own trigger+local response; ongoing prior response cannot manufacture a forward-button click; residual feedback requires comparing target/page/trajectory and before/onset evidence without physical count inference. Tests must also preserve unverified-question, positive-lock and ownership contracts and forbid source answer contamination. Run these tests on current prompts and capture expected RED.
- [ ] Add concise generic paragraphs to both current prompts, explicitly defining how existing “visible control/target” wording includes a supported visible region. Keep all existing safety/ownership/question rules; resolve wording ambiguity without deleting substantive requirements. No source target labels, frame IDs, time values, exact action counts or input values. Do not edit runtime/schema/budget/model/Runner code.
- [ ] Run the focused rendered-contract tests GREEN. Run relevant existing observer/auditor prompt, action-preservation, ownership and runtime tests selected from actual dependencies once at final code; list exact files/results and isolate unrelated preexisting failures instead of changing them.
- [ ] Run `python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py`, `python3 tests/backend_static_checks.py`, and `git diff --check`.
- [ ] Self-review for conflicting instructions, accidental baseline leakage and broadened click authority. Commit only the two prompts and new tests. Do not stage/change dirty CODEX_STATE.md or other uncommitted files.
- [ ] Write full RED/GREEN/commands/output/commit/self-review report `.superpowers/sdd/visible-target-boundaries-task-report.md`; return compact status, commit, tests, concerns and report path.

Root will prepare and independently review the frozen actual-Qwen experiment after implementation. No implementation test or mock constitutes AI recognition acceptance. Source30 and its reviewed phone-passed draft/history remain unchanged throughout.
