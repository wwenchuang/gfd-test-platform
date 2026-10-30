# Business source deadline implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Stop verified original24 source-relative engine cutoff while retaining finite cancellation and evidence gates.
**Architecture:** Business mode chooses4500s sourceclock once; engine uses sameclock−480.5s; observer accepts business-only4020s maximum. Legacy unchanged.
**Tech Stack:** Python/pytest, existing owned subprocess jobs.

## Global Constraints
Read the exact binding spec docs/superpowers/specs/2026-10-30-video-business-source-deadline.md. No other codepaths/caps/prompt/gate changes. Preserve dirty CODEX_STATE.md, neverstage it; no credentials. Do not deploy orstartsource25 inimplementationtask.

### Task 1: Business-only bounded source clock
Files: task_server/services/ui_video_runtime.py, task_server/services/ui_video_trajectory.py, focused tests (existing runtime evidence reuse/business modules and a new deadline test if clearer).
- [ ] Read spec and AGENTS, inspect current runtime/observer and affectedtests.
- [ ] Write failing clock/control tests covering exact requirements. Run RED before implementation.
- [ ] Minimal implementation source4500 onlybusiness,480.5tail onlybusiness, max4020 onlyexplicitbusiness observer; legacy oldvalues. Existing custombudget neverextended. Defaultdirectobserver1200 unchanged.
- [ ] Run focused affected tests, mandatory compile/backend/diff; inspect code/selfreview; updateSTATE appendexcluded.
- [ ] Commit only ownfiles; write .superpowers/sdd/business-source-deadline-task-report.md with RED/GREEN commands/results/changedfiles/concerns. Return status/commits/testsummary only.

Root: task review and whole release review from base12bbbee6 after report, disjointchecks as needed, primaryprotectedintegration/push/package/deploy then normal25 baseline/draft/SDK/phone chain. Fullacceptance is not a localtest result.
