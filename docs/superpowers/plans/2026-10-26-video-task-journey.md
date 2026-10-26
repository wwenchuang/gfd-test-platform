# Video Task Journey Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement the UI task, then independent review and native browser acceptance.

**Goal:** 按用户批准的任务列表＋详情方案优化视频生成与核对流程。

**Architecture:** 复用mountUiVideoSource和UiCaseDraft，列表/上传/详情为互斥视图；保持服务器与执行协议权威。新增前端状态只表示导航/筛选，不授权执行。

**Tech Stack:** 原生JavaScript、现有CSS、Node test/JSDOM、Python静态检查、原生Chrome CUA。

## Global Constraints
- 工作树 /Users/adouceshi/.codex/worktrees/recorder-touch-target-fix/midscene-task-platform；不回滚或批量提交现有用户改动。
- 唯一打印机9888E0094F2A；手机ecbfd645；本批页面验收不得下发手机任务。
- 不改生成/执行门禁，不手工补视频输入，不伪造执行状态；生成完成不自动导入或跳页。
- 既有请求隔离、轮询上限、媒体回收、编辑恢复、幂等试跑必须保留。

### Task 1: Implement approved task and review journey

**Files:** js/ui-simple-entry.js, js/ui-case-draft.js, css/ui-case-draft.css, task-manager.html, tests/ui_simple_entry_check.js, tests/ui_case_draft_check.js.
**Requirements:** Read docs/superpowers/specs/2026-10-26-video-task-journey-design.md; it is the approved scope.
**Interfaces:** mountUiVideoSource(node,{current,openDraft,initialSource}) and UiCaseDraft.mount remain compatible; selectors may be retained for lifecycle tests. Use existing API responses, no new backend API or source history synthesis.

- [ ] Add behavior tests before production edits: exclusive list/upload/detail views; query/status filtering plus10/load more; draft_id displayed as draft only; return from active task keeps source and ready result never auto-imports; older/other-app responses ignored; review summary targets correct row; gate blocker links target valid step only; original step evidence preserves provenance.
- [ ] Run `node --test tests/ui_simple_entry_check.js tests/ui_case_draft_check.js` and confirm new behavior failures.
- [ ] Implement view switching with `data-video-view`; preserve upload controls under hidden upload section, shared app choice and async guards. One main CTA per source. Update stage progress with actual completed values.
- [ ] Replace button wall with semantic rows, escaped text and title tooltips, state filter/search/load more; existing source retries stay the same ID. Do not infer trial states from draft existence.
- [ ] Improve review summary, per-step evidence and execution scope copy without changing try authorization or dirty-state guards. Full original source remains accessible.
- [ ] Bump touched asset versions in task-manager.html. Run scoped Node tests, `python3 tests/frontend_static_checks.py`, required main3 py_compile/backend static, `git diff --check`. Self-review for keyboard access, narrow layout and stale async results. Report exact files/tests; do not commit other files.

### Task 2: Independent review, integration and real-page acceptance (root)
- [ ] Review task diff and fix important findings before commit.
- [ ] Run required checks once after final edits, update only own CODEX_STATE prefix; selective commit and push both existing branches.
- [ ] Verify current video worker is settled; bundle deploy through native Chrome bastion and confirm terminal completion plus /api/health exact revision.
- [ ] Reload real Chrome page and verify task listing/filtering/detail/back/upload and Chinese review page. No Runner dispatch for page testing.
- [ ] Save evidence and report UI acceptance separately from outstanding original-video/phone acceptance.
