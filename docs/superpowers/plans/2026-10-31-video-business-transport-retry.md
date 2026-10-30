# Business transport retry and evidence guidance implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Survive one bounded transient mandatory-call failure without skipping evidence, and clarify same-page interaction evidence for Qwen.
**Architecture:** Business-only facade owns independent sequential ModelJobs scopes sharing one clock/store/budget; generic prompt clarification has no source-specific answers.
**Tech Stack:** Python/pytest/actualowned subprocess controls, existing Qwen stageprotocol/Markdownprompts.

## Global Constraints
Exact binding requirements in docs/superpowers/specs/2026-10-31-video-business-transport-retry.md, all sections. No deployment/source retry/phone in implementertasks. DirtySTATEappendexcluded fromcommit. No credentials.

### Task1: Bounded mandatorycall transport retry
Files: new smallfocused task_server/services/ui_video_business_jobs.py (or equivalent), minimal ui_video_runtime.py integration; focusednew tests plus necessaryexistingruntime tests. ModelJobs internals/legacyunchanged.
- [ ] Inspectspec/AGENTS/STATE/currentruntime/nativefallback/modeljobs. Write failedcontrols first, run RED.
- [ ] Implement smallbusinessfacade/eligibility/ownerprovenance/finaltracerefs exactlyspec, no newpayloadanswers.
- [ ] Run GREEN meaningful realownedjob/negative/budget controls, affected runtime/trajectory/business/modeljobs/optionalnative tests; mandatorycompile/backend/diff.
- [ ] Selfreview/STATEappendexcluded/commitonlyownfiles. Report .superpowers/sdd/business-transport-retry-task1-report.md with RED/GREENcommands/results/files/concerns. Return compactstatus/commit/testsummary.
Root taskreview (spec+quality) fromrecordedbase3080ce2a, resolve findings before Task2.

### Task2: Generic same-page response prompt guidance
Files: ai_skills/prompts/ui_video_trajectory.observe.md and .audit.md only, meaningful existingpromptcontractchecks ifneeded (no string-mirrortests). 
- [ ] InspectTask2spec andexistingpromptcontracts; preserve allpreviousinstructions.
- [ ] Minimal generic samepage/controlfeedback guidance, bothobserve/audit, no per-videoanswers.
- [ ] Existingprompt/schema/staticchecks; ownsemanticpositive/negative review; rootlivecontrolledQwen probe remainsdistinctrequiredverification.
- [ ] Commitownfiles/report .superpowers/sdd/business-transport-retry-task2-report.md. Root fresh taskreview and whole3080..HEAD release before integration.

Root: original25diagnosis savedcurrent-platform/normal-run25; prepare controlledQwen probe separately while Task1 executes. Afterreviews/controls exactintegration/push/deploy original26; whole baseline/normaldraft/actualSDK/phone not optional.
