# Qwen Video Fine Detail Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task.

**Goal:** Carry the locally verified Qwen image-detail option through the existing skill transport for explicit trajectory diagnostics.

**Architecture:** The existing whole-trajectory payload may explicitly request `perception_mode="fine-detail"`. Gateway and direct official DashScope Qwen3.8Max add only the fixed sibling `min_pixels=1658880` to each image part, retaining JPEG bytes, frame identity/time and chronological/context labels. Existing callers remain opt-in; this does not enable a new observer, change model routes or qualify source content.

**Tech Stack:** Existing Python AI skill service, Node Gateway, pytest and Node VM transport tests.

## Global Constraints

- Worktree `/Users/adouceshi/.codex/worktrees/recorder-touch-target-fix/midscene-task-platform`, branch `codex/ui-platform-simple-workflows`, base955b8a4f. Preserve unrelated dirty files and historical CODEX_STATE. Root owns plan/state/evidence documents.
- No worker/default/prompt/model-router/Runner/HTTP/UI changes. No real API, phone, printer, deployment or external message actions from this implementation task.
- Mode only applies to exact skill `ui_video_trajectory`, exact phase `whole-trajectory`; other skills/phases retain their existing behavior even if payload carries this property. In exact scope an explicitly supplied mode other than the exact string `fine-detail` is invalid; absence is unchanged.
- Fine detail supports only exact configured model `qwen3.8-max` and exact official HTTPS hosts dashscope.aliyuncs.com, dashscope-intl.aliyuncs.com or dashscope-us.aliyuncs.com (optional legitimate path). Reject a fine-detail request on any other route rather than silently degrading. Do not add a fallback or change routing.
- Keep existing2..60images/10MiB, chronological IDs/PTS, context-role, no trajectory detail-image guard, timeout/retry/reasoning/token limits intact. Fine detail is independent from bounded thinking and cannot enable it by itself.
- Actual controlled local probe is one47.626s Qwen3.8Max call, same8JPEG/prompt/text, only sibling min_pixels changed. Actual image_tokens12976; local marks recovered but qualifiedfalse. Actual resampling dimensions unknown. No claims of stable/full-source success.
- Add no configurable arbitrary pixel count. Fixed tested value is a bounded uniform image-analysis setting, never conditional on filename, source ID, coordinates, input values or a known source frame.

## Task 1: Opt-in bounded Qwen detail across both transports

**Files:**
- Modify `task_server/services/ai_skill_service.py`.
- Modify `ai-gateway/server.js`.
- Extend `tests/test_ui_video_trajectory_transport.py` and/or existing Qwen body tests as needed.
- Extend `tests/ai_gateway_trajectory_transport.test.mjs` or a focused new test that exercises actual completionOptionsForRoute via existing VM approach.

**Interfaces:**
- Existing run_ai_skill accepts the optional mode through its payload. `_video_context_assets` validates the exact-scoped property in addition to existing guards.
- `build_dashscope_chat_body` and `dashscope_chat_content` gain trailing `bounded_trajectory_detail=False`; run_ai_skill passes True only for exact skill/phase/fine-detail. The direct flag never changes ordinary callers.
- Gateway `completionOptionsForRoute` applies the option after normal imagePartsFromBody validation, only with exact scoped mode and supported official route. routeCandidatesForCall image-presence checks and the existing imagePartsFromBody validation remain compatible.

- [x] Write positive failing tests that request fine-detail, assert every image part has sibling min_pixels1658880 and dataURL/label/identity bytes remain equal to base. Prove direct run_ai_skill forwards the explicit capability and Gateway body payload retains it.
- [x] Write failing exact-scope/type tests: absent mode unchanged; wrong skill/phase unchanged; None/bool/numeric/other string invalid in exact scope. Unsupported model/host/protocol/lookalike-host fine requests fail; default ordinary requests remain unchanged. Fine-detail alone does not enable thinking or alter timeout/retry/max tokens.
- [x] Run tests and capture meaningful RED failure before implementation.
- [x] Implement minimal exact predicates and fixed sibling option. For direct body, check explicit boolean flag and official exact model/host before adding image options. Do not mutate original asset dictionaries or body payload; no new high-resolution global flag/max_pixels.
- [x] Re-run scoped transport/Qwen tests, main-chain py_compile, backend_static_checks, ai_gateway_static_checks, Node syntax, git diff --check. Report exact commands/results. No frontend tests needed.
- [x] Self-review, commit only owned code/tests after green; do not stage root docs/state or unrelated files. Write task report with red/green evidence and commit hash.

Positive wire assertion example (adapt to existing fixtures):

```python
parts = body['messages'][1]['content']
images = [part for part in parts if part['type']=='image_url']
assert all(part['min_pixels']==1658880 for part in images)
assert [part['image_url']['url'] for part in images] == base_urls
assert source_assets == original_copy
```

Fine detail must not add enable_thinking when bounded reasoning was not requested; retain default JSON/nonthinking logic and existing separately scoped1024 exception.

Verification commands include:

```bash
python3 -m pytest -q tests/test_ui_video_trajectory_transport.py
node tests/ai_gateway_trajectory_transport.test.mjs
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ai_skill_service.py
python3 tests/backend_static_checks.py
python3 tests/ai_gateway_static_checks.py
node --check ai-gateway/server.js
git diff --check
```

Task review and root full video regression precede handoff. This task's completion is transport availability only; normal worker integration, fresh whole-source qualification, deployment and fixed-device acceptance remain outstanding in the main trajectory plan.

Actual task outcome: code/tests committed2c12aa52199f7b8e5abcb2f2a663b580f44e34a8. Meaningful RED26Python/3Node failures captured; green166 relatedPython/5newNode/6existingQwen checks, independent spec+quality review approved. Root additionally ran1181 video+catalog tests20.17s, backend63/Gateway46/compile/Node syntax/diff green. Default observer/routes remain unchanged; this is an explicit diagnostic transport capability, not source content/Runner qualification.
