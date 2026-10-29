# Qwen strict-text semantic routing implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Max as the serial text-semantic bottleneck while keeping Max on original pixels and independent audits.

**Architecture:** Change the existing closed semantic model entry in Python and Gateway to official qwen3.8-flash. Runtime already reads this table. Preserve all remaining policy and independent qualification gates.

**Tech Stack:** Existing Python video stage policy/runtime, Node Gateway policy, pytest and Node loopback/wire tests.

## Global Constraints

- Semantic alone is qwen3.8-flash; skill/version/phase ui_video_trajectory/observe/whole-trajectory, cap10000, thinking1024, strict schema and no images stay.
- Perception Max8192/nonthinking, overview Flash10000, audit Max10000, consolidate Max10000, outcome Max4000, inputs Max3000 stay exact.
- Provider official qwen_plus only; fallback absent; SDK retry0; finish/schema/admission/proof/raw/order unchanged.
- Per-call120, outer1800, engine1200/24, shared80/lowercap, corrections2/revisits4/new64, source/image/record/gates stay.
- Legacy without selected QA source/video_stage unchanged. No source/frame/value/question/candidate/prompt/compiler/scorer/Runner/UI edit.
- Implementer has no external model/API/browser/device/deploy/push scope; root owns actual normal full-source and phone acceptance.

### Task 1: Closed semantic policy and exact wire/runtime coverage

**Files:** task_server/services/ui_video_stage_policy.py, ai-gateway/server.js, affected focused stage/wire/runtime tests. Exact task brief .superpowers/sdd/normal-video-semantic-flash-brief.md contains all binding values and verification contract.

- [ ] Add failing actual-request/options/attestation/hash/admission tests: semanticFlash accepted, semanticMax rejected, perception/auditFlash rejected, unchanged remaining stages and legacy route.
- [ ] Retain expected RED evidence then implement the minimal two policy edits.
- [ ] Verify runtime semantic remains text-only and selects exact new stage model, retaining schema/thinking/caps/deadlines/proof/raw checks.
- [ ] Run affected Python/Node coverage, mandatory compile/backend/Gateway/node syntax/diff checks; no unrelated reruns.
- [ ] Write full report with commands/results/limits, append STATE preserving dirt, commit scoped code/tests only.
- [ ] Independent SPEC and QUALITY review; fix and re-review concrete findings before release.

### Root actual acceptance

- [ ] Integrate reviewed scope preserving existing dirt, push, build and verify incremental bundle.
- [ ] Native QA deploy and verify exact revision; no old-code retry.
- [ ] Retry same original once as normal v10, preserve terminal proof/raw/budget/error evidence and verify every export hash.
- [ ] Audit full original business sequence and input recovery; normal import/review/static/scoring/execution checks then designated phone. No hardcoded source answers, content-gate relaxation, deleted actions or physical final print.
