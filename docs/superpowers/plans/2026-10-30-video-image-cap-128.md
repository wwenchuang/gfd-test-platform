# User-authorized visual image cap adjustment

User explicitly requested 上限调整一下 after normal v12 failed request_image_capacity at packet004. This steering chooses a bounded cap adjustment; previously discussed audit-first/deferred-review workflow is not part of this change. Use subagent-driven-development, TDD and independent review; no extra user approval needed.

## Design and evidence

Exact v12 original/source/release/private model evidence under external R/current-platform/normal-run12/run12 and normal-run12-diagnosis.json. Both ZIP CRC, remote innerSHA aa599effc8eef942f8648d62237c4cff5ff40821a17aace2f021e9b0db4aa666,2manifestfiles/31canonicalentries verified. All10Qwen calls admitted/receipt/cleanup, no provider timeout/schema rejection. Packet004 originally44images,2revisits added16,60images, next2requests refusedcapacity0 beforedispatch (receipt20).

Official https://help.aliyun.com/zh/model-studio/vision-model and https://help.aliyun.com/zh/model-studio/qwen3-8-max checked2026-10-30: Base64 multi-image cap250, Max1M context. Platform128 stays below provider cap. Actual original text/image token usage at60images58069prompt, no context refusal. Documented capability does not prove fresh provider/source acceptance.

## Task 1: Closed128-image transport and business acquisition

Global binding requirements:
- Raise image count only for closed grounding and audit stages to128; all other closed/legacy/nonstage image caps remain as before (perception12,overview/consolidate/outcome/inputs60,semanticzero,nativetimeline1). Default bank trajectory stays60. Business trajectory explicit context uses128 in actual packet_image_slots and expand capacity. No public caller override or new mode.
- All platform AI remains Qwen. Fixed models/temperature0.1/thinking1024/maxTokens/120s/no retry/fallback/repair unchanged. Existing10MiB asset bound,16MiB IPC, nativeMP4 bounds,1800s outer,1200s trajectory,24calls,2corrections,4revisits,64newframes, record limits unchanged. User requested image count, not bypassing evidence or execution gates.
- No silent slicing/truncation: every admitted image reaches actual provider messages in chronological order with exact original/revisit ID/PTS/ownership, proof and receipt.128accepted/129reject before dispatch. Canonical observer schema/enum bounds remain valid at128; per-event reference/state limits are independent and unchanged.
- Python/Gateway must agree on per-stage cap. Prefer a small named policy helper per language used by validation and message normalization; no magic payload limit or wholesale60searchreplace. Retain native1 and semantic0 behavior, legacy60 rejection and perception12. Direct legacy fallback unchanged.
- Preserve owner/app/version/status CAS, cancellation/sharedModelJobs/RecordStore/cleanup, JPEG10MiB and IPC16MiB/positive triggers/questions/immutable raw refs/normal tail/import/review/static/score>=78/target trial gates. Phoneecbfd645 Runner mac-android-adouceshi printer9888E0094F2A only; no unrelated cancel/finalprint.
- Protect dirty CODEX_STATE.md prefix; no include in focused commit. Do not touch249primarydirtyfiles or untracked prototypes. No network/browser/provider/device/push/deploy by implementer.

Scope: ui_video_stage_policy.py,ai_skill_service.py,ui_video_trajectory.py,ai-gateway/server.js,focusedexisting/newstage/transport/runtime tests. Determine other exact count validators from named call chain; no unrelated prompt/schema/runtime behavior change.

- TDD first: actual closed Python/Gateway128-image message/proof path and129refusal; audit andgrounding; existing legacy60/perception12/native1/semantic0 unchanged. Test image61/128 actually forwarded and attested, 10MiB and16MiB unchanged.
- Reproduce captured v12 44+16=60/pending2 capacity0 with real trajectory expansion, prove business mode now has68slots and continues actual decoder within same counters; defaultbank60stillrejects. Use captured content only as fixture, never inject source answers into prompts. Include128schema normalization and no caller mutation.
- Run scoped/new+adjacent stage/wire/jobs/runtime/trajectory tests (one suite at a time to avoid loopback timing noise), NodeGateway tests, mandatorycompile/backend/Gateway/diff. Retain RED/GREEN/report .superpowers/sdd/image-cap-128-report.md.
- Commit focused source/tests only; returnstatus/head/testsummary/concerns.

## Task 2 root

Taskreview + broad whole delta review since business baselinec4e11b14 (isolatedb9e816ac) as appropriate; integrate exact tree into primary preserving249dirt, mandatorychecks/push/buildnewpackage/nativechecksumdeploy/fullhealth. Sameoriginal normalv13once, export/verify/content/import/review/gates/phone. No claim adjustment guarantees modelconvergence. No audit-first/deferredreview newbehavior in this cap-only task.
