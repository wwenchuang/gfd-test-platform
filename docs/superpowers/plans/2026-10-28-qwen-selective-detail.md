# Qwen Selective Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this task.

**Goal:** Reduce optional trajectory image processing cost while preserving every image and its evidence identity.

**Architecture:** Extend the already opt-in fine-detail transport with an optional bounded selection of original frame IDs. Only selected images receive the existing fixed sibling min_pixels. Selection is metadata, never permission to omit images, confirm a touch, or change ownership. Defaults remain unchanged.

**Tech Stack:** Existing Python skill/direct DashScope transport and Node Gateway; pytest and Node VM tests.

## Global Constraints

- Work only in /Users/adouceshi/.codex/worktrees/recorder-touch-target-fix/midscene-task-platform, branch codex/ui-platform-simple-workflows. Root owns state/evidence/plan; preserve all unrelated dirty files.
- No worker, prompt, general model-router/config, Runner, UI, schema-output or execution-gate changes. No actual API/device/deployment calls by implementer. Owner clarification after review: a server-enforced per-call `disableFallback` option for exact `ui_video_trajectory` / `whole-trajectory` / `fine-detail` `/ai/skill` requests is authorized to satisfy the literal no-fallback requirement; callers cannot override it. Default and unrelated calls retain existing routing.
- Exact scope is skill ui_video_trajectory and phase whole-trajectory. Other skills/phases ignore this new payload property and retain current behavior.
- Optional payload property perception_frame_ids requires perception_mode exactly fine-detail. If present, it must be a list of 1..12 unique strings, each an actually provided image asset name. Reject null/bool/scalar/empty/duplicates/unknown IDs/too many with video_perception_selection_invalid. Original image validation runs first, so preexisting image failures retain their classification.
- Absent selection preserves current all-image fine-detail behavior. Absent mode and selection preserves current default behavior. Scope-matched selection without exact mode is invalid.
- Fine detail still requires exact qwen3.8-max on official HTTPS dashscope.aliyuncs.com, dashscope-intl.aliyuncs.com or dashscope-us.aliyuncs.com, with no fallback. Fixed min_pixels=1658880; never arbitrary pixels or source-specific values.
- Keep every supplied JPEG byte, MIME, ID, timestamp, order, chronological/context label and existing 2..60/10MiB guards unchanged. Do not mutate assets/payload. Preserve timeout/retry/max_tokens/reasoning and 1024 thinking independently.

## Task 1: Select fixed detail frames across both transports

**Files:** Modify task_server/services/ai_skill_service.py and ai-gateway/server.js; extend tests/test_ui_video_trajectory_transport.py and tests/ai_gateway_trajectory_transport.test.mjs.

**Interfaces:** build_dashscope_chat_body and dashscope_chat_content gain trailing trajectory_detail_frame_ids=None. The direct helper validates supplied non-None selection against the actual image slice and requires bounded_trajectory_detail is True; None preserves current behavior. Explicit payload null is rejected before this internal default. run_ai_skill forwards the new argument only in exact scope with a present selection. Gateway validates exact-scoped selection after original asset validation and applies options by corresponding asset name.

- [ ] Write failing positive tests for mixed owned/context frame selection: selected images alone have min_pixels; every URL/label/other option equals baseline; original dictionaries unchanged. Assert selection propagation through run_ai_skill and dashscope_chat_content to actual request body.
- [ ] Write failing guards for invalid values above, selection without mode, IDs outside image_limit slice, and exact-scope behavior. Existing absent-selection all-fine and no-mode defaults must remain valid. Exercise both actual Gateway completion function and Python direct helper, including official route restrictions and reasoning independence.
- [ ] Run focused tests and record meaningful RED before implementation.
- [ ] Implement only bounded selection, with a small validator reused where practical. Direct selection predicate: isinstance(value,list), 1<=len(value)<=12, every item type str, unique, subset of actual sent names. Preserve original validation ordering in Gateway and skill entry.
- [ ] Run focused Python/Node transport and related Qwen tests; required py_compile, backend_static_checks.py, ai_gateway_static_checks.py, node --check ai-gateway/server.js and git diff --check.
- [ ] Self-review and commit only owned four code/test files. Report exact red/green commands, output counts and hash to .superpowers/sdd/selective-detail/task-1-report.md. Independent spec/quality review is required before root uses the component.

Wire verification must compare a deep copy of mixed-image response after removing min_pixels from selected indices against baseline, and verify unselected images never gained it. This task provides a capability only; root must measure real usage and source semantics before making a performance or acceptance claim.
