# API Execution Interruption Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让因 API Worker 重启或丢失而长期停留在 `RUNNING` 的执行安全收敛，同时保留完成证据且不自动重放业务请求。

**Architecture:** `ExecutionService` 提供候选查询和加锁幂等收敛；Scheduler 按安全静默窗口扫描并投递恢复任务；Celery Worker 执行恢复、刷新关联任务并沿用既有通知策略。数据库结构不变。

**Tech Stack:** Python 3.12、SQLAlchemy、PostgreSQL、Celery、Redis、pytest、systemd。

## Global Constraints

- 不自动重放任何未完成业务请求。
- 已完成用例和历史尝试保持不变。
- 默认静默窗口 300 秒，配置范围 120 至 3600 秒。
- 收敛时必须在事务内重新检查状态和最后进度时间。
- 中断错误不能包含原始异常、请求、响应或凭据。
- 修改直接提交到用户指定的 `main`，保留工作区现有文档。

---

### Task 1: 执行状态幂等收敛

**Files:**
- Modify: `task_server/api_testing/repositories/execution_repository.py`
- Modify: `task_server/api_testing/services/execution_service.py`
- Test: `tests/api_testing/test_execution_service.py`

**Interfaces:**
- Produces: `ExecutionService.stale_running_execution_ids(stale_before, limit=100) -> tuple[str, ...]`
- Produces: `ExecutionService.recover_interrupted(execution_id, stale_before) -> bool`

- [x] **Step 1: Write the failing tests**

  创建包含已通过、运行中和排队用例的执行，断言恢复后仅后两者写入 `BROKEN` 尝试、汇总为终态、事件可读；再覆盖新鲜进度和重复调用不处理。

- [x] **Step 2: Run tests to verify RED**

  Run: `pytest -q tests/api_testing/test_execution_service.py -k 'recover_interrupted or stale_running'`

  Expected: FAIL because the recovery APIs do not exist.

- [x] **Step 3: Implement the minimal recovery transaction**

  Repository 计算执行与子用例的最后进度时间并查询候选。Service 锁定执行后再次检查时间，只转换未完成子用例，调用既有汇总逻辑并追加脱敏事件。

- [x] **Step 4: Run focused tests**

  Run: `pytest -q tests/api_testing/test_execution_service.py -k 'recover_interrupted or stale_running or worker_exception'`

  Expected: all selected tests pass.

### Task 2: Scheduler、Worker 与部署验收

**Files:**
- Modify: `task_server/api_testing/config.py`
- Modify: `task_server/api_testing/scheduler.py`
- Modify: `task_server/api_testing/tasks.py`
- Modify: `tests/api_testing/test_config.py`
- Modify: `tests/api_testing/test_tasks.py`
- Create: `tests/api_testing/test_scheduler.py`
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: `ExecutionService.stale_running_execution_ids(...)`
- Consumes: `ExecutionService.recover_interrupted(...)`
- Produces: Celery task `api_testing.recover_interrupted_execution`

- [x] **Step 1: Write failing scheduler and task tests**

  断言配置边界、Scheduler 只派发候选 ID、Worker 仅在恢复成功后刷新任务并发送既有通知。

- [x] **Step 2: Run tests to verify RED**

  Run: `pytest -q tests/api_testing/test_config.py tests/api_testing/test_tasks.py tests/api_testing/test_scheduler.py`

  Expected: FAIL because scan and task wiring are absent.

- [x] **Step 3: Implement scan and task wiring**

  Scheduler 每轮使用 UTC 截止时间查询并投递；Celery 任务解析截止时间、幂等恢复、刷新任务并复用通知函数。

- [x] **Step 4: Run repository gates**

  Run: `pytest -q tests/api_testing/test_execution_service.py tests/api_testing/test_tasks.py tests/api_testing/test_scheduler.py tests/api_testing/test_config.py`

  Run: `python3 tests/backend_static_checks.py`

  Run: `python3 -m py_compile task_server/api_testing/services/execution_service.py task_server/api_testing/repositories/execution_repository.py task_server/api_testing/tasks.py task_server/api_testing/scheduler.py`

  Run: `git diff --check`

  Expected: all commands exit 0.

- [ ] **Step 5: Commit, push, deploy and verify**

  Commit only the listed code, tests, design/plan and `CODEX_STATE.md` on `main`; push `origin/main`. Deploy platform and restart task, worker and scheduler services. Verify `/api/health` revision, service status, the old 219/288 record's terminal summary, and Safari execution/detail pages.
