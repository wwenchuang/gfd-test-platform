# Video business-first Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the enrolled unchanged-video path using direct Qwen business understanding plus original-frame grounding and existing execution gates.
**Architecture:** Two additive closed Gateway stages, a source-only server flag, one owned runtime and existing trajectory/tail. Bank runtime remains default.
**Tech Stack:** Python existing AI skills/ModelJobs, Node Gateway, JSON schema, pytest/node:test.

## Global Constraints

- All platform AI remains Qwen; new timeline and grounding use qwen3.8-max, temperature0.1/thinking1024/maxTokens10000/120seconds/retries0/no fallback/no repair.
- Existing stages/models/options remain unchanged; existing canonical observer scaffold is the grounding schema.
- Source authorization/owner/app/version/status CAS, cancellation, immutable private receipts/proofs and cleanup remain mandatory. Preserve existing dirty CODEX_STATE.md prefix and do not include it in focused commits.
- Existing outer1800s, trajectory1200s/24asks/two corrections/four revisits/64new frames, JPEG60/10MiB, IPC16MiB and record limits remain unchanged. No fabricated no_new_frames or confirmed evidence.
- Only existing QA source enrollment plus server UI_VIDEO_RUNTIME_BUSINESS_FIRST=1 activates new mode. No public Runner/action/route bypass and no direct worker provider keys.
- Preserve all original/revisit IDs, real PTS, owned/context roles, original video and questions. Native approximate times and descriptions are unverified context, never frame IDs or executable actions.
- Grounding sees original JPEGs and unverified timeline, no perception-bank prose. Independent audit sees original pixels and existing claims, without native timeline/observer narrative. Keep all normal tail and execution gates.
- No hardcoded recording values/counts/coordinates or manual baseline in model input. Phoneecbfd645/Runner mac-android-adouceshi/printer9888E0094F2A only; no unrelated cancellation or final physical print.

### Task 1: Closed native timeline and visual grounding transport

Files: task_server/services/ui_video_stage_policy.py, ui_video_stage_receipts.py, ai_skill_service.py; ai-gateway/server.js; new ui_video_native.py; ai_skills/prompts/ui_video_timeline.v1.md and schemas/ui_video_timeline.schema.json; focused tests/test_ui_video_business_transport.py and tests/ai_gateway_business_video.test.mjs, adjacent stage/wire/records/job tests as affected.

Interfaces consumed: existing run_ai_skill image_assets and ModelJobs.run; stage proof/receipt envelope. Produced: STAGES timeline/grounding exact tuples in design; `validate_native_asset(payload, assets)` validates timeline image_assets and metadata; `load_native_video(source_path, duration_ms)` returns `(metadata, assets)` using original bytes/hash, fixed options. Metadata keys are source_sha256/source_bytes/duration_ms/fps/max_pixels/total_pixels, passed at payload['video']; native asset keys name/mime/base64/timestamp_ms (normal Gateway adds empty dataUrl). Root timeline payload phase whole-timeline, video metadata only. Root output exact `scenes` and `questions`; scene exact kind/start_ms/end_ms/description, kinds input/navigation/interaction/wait. Schema max60 scenes/max40 questions, description max2000, timestamps integers validated within source duration by runtime helper. No frame IDs/values/executable actions. Timeline prompt covers full business order, input rounds, distinct objects/returns, asynchronous responses, only material questions, no known source answers.

- [ ] Write failing tests invoking real stage validators/renderer/normal Gateway and comparing actual provider options to Python reconstruction. Include actual-size source base64 near16MiB boundary, malicious asset mismatch/hash/dataURL, wrong MIME/model/phase/null schema, legacy rejection, zero images versus video_count1, valid groundingJPEGs with narrowed schema, unsupported >12MiB and global IPC failure before dispatch.
- [ ] Run scoped tests and preserve expected RED output under .superpowers/sdd/business-transport-red.txt.
- [ ] Implement minimal native asset helper, additive stages, image envelope forwarding, native video_url dispatch and independent proof reconstruction. Grounding branches alongside semantic for schema handling but alongside visual stages for exact JPEG validation; existing semantic remains zero-image. Existing legacy requests cannot acquire native capability. Add schema and prompt; raw source never in prompt/log/receipt.
- [ ] Run new Python/Node tests plus affected existing stage/wire/records/ModelJobs tests; main compile/backend63/Gateway static/diff. Preserve commands/output in report. Self-review actual wire/options/cleanup; no model/network/deploy.
- [ ] Commit only scoped source/tests/skill files. Report exact commit, files, test commands/results and limitations at .superpowers/sdd/business-transport-report.md.

### Task 2: Source-scoped business runtime and budget-aware original-frame trajectory

Files: new task_server/services/ui_video_business_first.py; ui_video_runtime.py; task_server/workers/ui_video_worker.py; ui_video_trajectory.py; observer/audit prompts or render helper; new tests/test_ui_video_business_runtime.py and existing trajectory/worker tests as affected.

Interfaces consumed: Task1 load_native_video, timeline/grounding stages, existing RuntimeBudget/_semantic_schema, RecordStore/ModelJobs, observe_trajectory, canonical revisit loader and unchanged tail. Produced: `runtime_mode()` freezes existing bank mode or video-business-first-v1 based on exact server flag; `run_runtime(..., *, budget=None, mode=None)` routes to business path or unchanged bank path. Worker pins mode in source selection and passes it plus RuntimeBudget(0) for business or original producer count for bank. `run_business_runtime(document, workspace, source_path, check, publish, *, budget)` runs one native timeline then current trajectory through local runner, original tail and finally record publication. Timeline validated scene times0<=start<=end<=duration, ordered starts, <=60 scenes; kept in private result as unverified context with proof/receipt refs. No accepted action or native input value enters execution.

- [ ] Write true failing injected-runtime tests: no38 per-frame producer jobs; timeline precedes original-frame overview/grounding/audit/tail; grounding has exact JPEGs/schema/no bank; independent audit omits timeline; loader uses original source/owned range and publishes added media before refs; same shared accounting/deadlines/proofs/finally; timeline invalid/out-of-duration fails; stale access/source version stops before model/after receipt; legacy flag off unchanged.
- [ ] Add trajectory tests asserting actual remaining revisits/acquired-frame allowance visible before observe/audit; budget is not renewed, refused requests remain fail-closed, questions/provenance preserved. Test actual source fixture multi-packet flow, input continuation, separate confirmation and keyboard boundary, positive-trigger locks.
- [ ] Preserve RED, implement minimal routing and runtime using existing original functions. Provide unverified business timeline to overview/observe only through an optional explicit observe_trajectory argument (defaultNone preserves legacy). Add closed budget context on calls; prompt preserves real steps and asks only business-material evidence, no keyword classifier or cleared questions. Leave consolidation/input recovery/action compiling/normal execution unchanged.
- [ ] Run new runtime/worker/trajectory suites and relevant existing bank/audit/boundary/input/normal-gate tests, mandatory main compile/backend and diff; keep log/report. Independent content fixture checks do not claim live acceptance.
- [ ] Commit scoped changes only, report .superpowers/sdd/business-runtime-report.md, no network/deploy/device.

### Task 3: Reviewed deployment and unchanged original qualification (root)

- [ ] Review each task diff against design; resolve spec/quality findings. Broad final branch review e77ccf8e..HEAD including additive stages, actual payload/schema/proof, mode source freeze and all unaffected normal gates.
- [ ] Preserve primary249 dirtyfile manifest (STATE prefix only), cherry-pick reviewed commits to primary codex branch, verify committed tree equals isolatedHEAD, mandatory checks/push. Build exact committed offline bundle; add only explicit server UI_VIDEO_RUNTIME_BUSINESS_FIRST=1 while retaining source selection and unrelated env lines/permissions.
- [ ] Native Chrome QA SFTP upload, actual server checksum equals package hash, unpack/deploy, full health SHA and service/Gateway checks. Then normal UI unchanged original regenerate once; no retries during active run.
- [ ] Terminal export fresh run12, native download/CRC/manifest/canonical hashes; verify actual all-Qwen route, exact input values, two Next actions with separate confirmations, old/new cancellation identities, distinct keyboard hide and each return, no final print. No manual answer injection or gate bypass.
- [ ] Normal import/review/static/score>=78 and normal designated-phone trial; current permitted old task absent -> new flow only, branchunexercised. Record source/content/gates/phone outcomes separately; failures trigger precise diagnosis, not a false success claim.
