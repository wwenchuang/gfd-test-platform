# Video stage schema classification implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve closed grounding schema rejections as schema errors so the existing bounded trajectory correction path can handle them.

**Architecture:** Introduce a typed validation error with a schema-owned path, translate it only at the skill output-validation boundary into the existing SkillSchemaError, and pass its safe feedback to the existing correction loop. Keep the rejected raw receipt immutable and all ownership validation active.

**Tech Stack:** Python, pytest, existing ModelJobs and closed Qwen transport.

## Evidence and design

Verified normal original v14 on release44c4c83d failed4/10,12 calls/12 receipts. Receipt24 claims trigger_frame frame-163 in packet005 whose owned frames are129..160 and attached context123..166. Reconstructed narrowed schema SHA dec91f90540f39fd3d5900918eca60158db7494824297d14ee3148dd72210473 exactly matches its provider attestation. Current validate_stage_output raises plain ValueError at $.events[2].trigger_frame, which run_ai_skill wraps as a generic failure, ModelJobs classifies failed, and the trajectory correction loop cannot catch. One correction was used earlier; one remains. Root retained authentic fixture material at .superpowers/sdd/v14-trigger-owner-replay.json and verified the source/receipt/ZIP hashes.

Chosen fix is error classification plus safe targeted feedback. Increasing capacity or accepting context-owned actions would not address this cause. A repeated whole-source run is insufficient regression coverage; exercise the actual skill/child/correction chain offline first. User has explicitly authorized necessary fixes, deployment and validation without another confirmation.

## Global constraints

- All AI models remain Qwen; grounding/audit128 and all other stage caps remain unchanged.
- Do not alter prompts, output schema contents, packet cuts, owned frame enums, actions, inputs, image bytes or model answers.
- Preserve120s model deadline,1800/1200s runtime deadlines,24 trajectory calls,2 corrections,4 revisits,64 new frames and no repair/fallback/provider retries.
- Wrong trigger ownership must still be rejected; never clamp/change frame IDs, move actions, clear questions or auto-confirm steps.
- Only schema-validation failures from the closed validator get the schema classification. Transport/deadline/options/configuration errors retain their categories.
- Retain raw receipts, proof, source/version ownership/CAS and cleanup. Legacy/manual behavior and all normal static/score78/trial gates stay unchanged.
- Protect dirty CODEX_STATE.md and the primary checkout's249 existing dirty files. Work only in this isolated checkout, scoped commits. Root owns push/integration/deployment/native UI/original/phone.

## Task1: Typed closed schema rejection and bounded correction feedback

**Files:** Modify task_server/services/ui_video_stage_policy.py, task_server/services/ai_skill_service.py, task_server/services/ui_video_trajectory.py. Create tests/test_ui_video_stage_schema_classification.py and tests/fixtures/ui_video_v14_trigger_owner.json. Reuse existing relevant test helpers without broad refactoring.

**Interfaces:** validate_stage_output(value,schema,path='$') still returns the original valid value and raises ValueError-compatible errors on invalid values. New VideoStageOutputError(ValueError) retains only schema_path and a fixed message; no rejected value. run_ai_skill translates that specific error to SkillSchemaError at the semantic/grounding output validation call. Existing ModelJobs serializes/reconstructs SkillSchemaError, and observe_trajectory.ask spends the existing correction budget.

- [ ] Step1: Copy the authentic fixture from .superpowers/sdd/v14-trigger-owner-replay.json with its provenance intact. Test reconstruction of the exact attested schema hash and current wrong trigger refusal. Test the actual run_ai_skill path receiving the captured raw reply raises SkillSchemaError with $.events[2].trigger_frame, rather than RuntimeError. Do not substitute a fake validation function.
- [ ] Step2: Run the new tests and retain RED output under .superpowers/sdd/schema-classification-logs/. Add a real ModelJobs child/loopback test proving proof+receipt persistence, typed parent error path, terminal cleanup and no automatic retry. Exercise the trajectory's actual existing correction loop with this real skill validation error, bounded corrected synthetic subsequent reply, and always-invalid exhaustion. A corrected synthetic reply is regression data, never acceptance of the original source.
- [ ] Step3: Implement the minimal typed error and boundary translation:

```python
class VideoStageOutputError(ValueError):
    def __init__(self, path):
        super().__init__(f'video_semantic_output_invalid:{path}')
        self.schema_path = path
```

Replace only validator-produced output mismatch raises with this class; anyOf catches this class. At the existing semantic/grounding call:

```python
try:
    validate_stage_output(result, output_schema)
except VideoStageOutputError as exc:
    raise SkillSchemaError(exc.schema_path, '输出不符合当前阶段schema') from None
```

In ask's already existing SkillSchemaError branch, use error.model_feedback plus a fixed reminder that trigger_frame belongs to owned_frame_ids and context may only supply evidence. Do not put rejected model text or values into feedback. Other error feedback stays unchanged.

- [ ] Step4: Run new tests plus affected tests/test_ui_video_stage_wire.py, test_ui_video_model_jobs.py, test_ui_video_trajectory.py, test_ui_video_trajectory_transport.py, test_ui_video_runtime.py, test_ui_video_business_runtime.py, test_ui_video_image_cap_128.py. Run one heavy suite at a time. Required main-chain+changed py_compile, backend_static_checks.py, git diff --check. No Gateway/frontend change planned; if touched, run its required checks and justify scope.
- [ ] Step5: Self-review, append CODEX_STATE.md without altering its prefix, write .superpowers/sdd/schema-classification-report.md with RED/GREEN/limits and commit only the named code/tests/fixture. Root performs independent task review and whole-release review before integration/deployment. Then one same-source normal run with unchanged video/goal/answers; inspect content and normal gates before phone execution.
