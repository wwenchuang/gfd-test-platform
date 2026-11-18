# 测试失败自主调查 Agent Implementation Plan

> **For agentic workers:** Use subagent-driven-development with independent file ownership; implement and verify each task without further approval pauses.

**Goal:** 从现有 API / UI 执行详情启动千问自主取证，输出可追溯的事实、假设、缺口和下一步，降低人工翻日志成本。

**Architecture:** 复用 Python HTTP 服务、现有账号权限、持久后台调度器和 DashScope 配置。模型通过三个只读取证工具动态选择下一步，平台限制执行范围、预算和证据引用。既有测试执行流程不改。

**Tech Stack:** Python stdlib HTTP/SQLite + 现有 SQLAlchemy API 仓库、原生 JS、Vue 3、现有千问 OpenAI-compatible endpoint。

## Global Constraints

- 用户已选择服务器直接接千问，无百炼应用 ID；不新建云端应用。
- 只读工具 get_failed_steps / get_request_trace / compare_last_pass；source、run_id、actor 服务端绑定。没有调度测试、改用例、发通知工具。
- 每次取证及结果读取重新检查当前权限；跨运行子记录拒绝。证据内容是数据，不是指令。
- 最多 6 轮、12 次工具调用、180 秒调查时间；累计 token 预算 18000，单轮输出最多 2000 tokens。相同参数重复最多两次。
- UI 没有同次运行网络记录时明确缺失；不得按同分钟或不同账号/环境关联。API 历史限定同用例版本、项目、环境修订、接口及来源修订，且早于本次。
- 深色薄荷绿，报告详情入口，刷新后可读历史；禁止模型 HTML 和任意外部证据链接。
- 不包含巡检调度、自动提单、实际重跑、升级或打印。本期自主性为调查路径、取证选择、停止判断与缺口建议。

## Shared interfaces

```python
EvidenceSource(actor, source, run_id, session_factory=None)
source.authorize()
source.get_failed_steps()
source.get_request_trace(case_id)
source.compare_last_pass(case_id)
# Each result: {evidence:[{id,kind,title,href,data}], missing:[str], ...bounded context}
investigate(source, *, model_call=None, progress=None, cancelled=None, timeout_seconds=180, max_rounds=6)
# Returns {status,stop_reason,result,evidence,trace,rounds,usage,elapsed_ms,model}
# progress receives the same accumulated partial payload.
```

Task statuses: queued, running, completed, limited, failed, cancelled, interrupted.
Result schema: `{summary:string, facts:[{text,evidence_ids}], hypotheses:[{text,evidence_ids}], recommendations:[{text,evidence_ids}], missing_evidence:[string]}`. Facts require nonempty valid IDs; hypotheses never presented as confirmed. Evidence IDs must originate from actual tools.

HTTP: POST `/api/failure-analysis/tasks` `{source:'api'|'ui',run_id,request_id:UUID}`; GET same path `?id=UUID` or `?source=ui&run_id=...`; POST `/api/failure-analysis/cancel` `{id}`. All return `{ok:true,task:object|null}`. Task object has `id,source,run_id,status,created_at,updated_at,error` plus investigate payload. Same owner/request_id idempotent; one active analysis per owner; source/run has latest read. Queued timeout 60 seconds; restarted workers become interrupted, preserving evidence.

### Task 1: Real evidence adapters
- Files: `task_server/services/failure_analysis_sources.py`, `tests/test_failure_analysis_sources.py`.
- [x] Write failing tests for run binding, redaction, scope revocation, no network evidence and strict same-environment historical comparison.
- [x] Implement trusted readonly projections of API ExecutionService and UI batch service, with bounded data and same-origin evidence links.
- [x] Run targeted pytest; never trigger a real device or business request.

### Task 2: Autonomous tool loop and model transport
- Files: `task_server/services/failure_analysis_agent.py`, `task_server/services/failure_analysis_model.py`, `tests/test_failure_analysis_agent.py`.
- [x] Test two identical timeout symptoms with differing request evidence causing different model-selected tool paths; test forged tools/arguments/refs, repeated calls, budget, missing usage and cancellation.
- [x] Implement native tool_calls/tool_call_id continuation using configured DashScope endpoint. No fixed tool sequence or alternate provider fallback. Save observable actions, not chain-of-thought.
- [x] Validate final JSON evidence references; invalid/incomplete outputs preserve collected evidence and honest limited/error status.

### Task 3: Durable tasks and authenticated HTTP
- Files: `task_server/services/failure_analysis_tasks.py`, `task_server/failure_analysis_http.py`, minimal route registration in `task_server/router.py`, `tests/test_failure_analysis_tasks.py` and HTTP tests.
- [x] Test with real local HTTP and isolated identity store: unauthenticated/cross-owner/revoked permissions denied; duplicate start reuses task; cancellation and restart preserve evidence.
- [x] Reuse PersistedJobDispatcher and actor_context. Store private SQLite records with owner binding and bounded limits; never persist secrets.

### Task 4: Report entry and actionable results
- Files: new `js/failure-analysis.js`, `css/failure-analysis.css`, `api-testing-ui/src/components/FailureAnalysisPanel.vue`, report mounting points and component tests.
- [x] Add explicit start, stop, retry and persistent result read. Display actual tool calls, elapsed time, usage, facts/hypotheses/missing/next steps and evidence previews.
- [x] Preserve existing report on failure. Account/run change discards stale replies, access revoked clears private results, unmount stops polling.
- [x] Add UI batch deep link without fabricated report revision; embed at API report and execution details.

### Task 5: Verification and handoff
- [x] Backend/frontend static checks, py_compile required Agent files, scoped Python and JS tests, API UI build, git diff --check.
- [x] Exercise the actual report component in Chromium with isolated HTTP route fixtures; exercise the actual HTTP service, queue, ORM and evidence adapters independently with a model protocol fixture. Distinguish these checks from real model/production evidence.
- [x] Attempt configured real Qwen analysis using an authorized existing report if available; record configuration/access blockers truthfully.
- [x] Independent review, update CODEX_STATE.md and deploy instructions; fetch remote before integration, push branch, user deploys.
