# Live Device Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 刷新设备时安全同步真实手机截图和只读使用状态，并在执行手机卡片中准确展示。

**Architecture:** 服务端创建单次采集请求，Windows Runner 从心跳响应领取并通过 ADB 采集，受认证接口保存最新 PNG 和状态。前端发起刷新并有界轮询，卡片继续使用既有单设备选择合同。

**Tech Stack:** Python 标准库、ADB、现有 HTTP Router、原生 JavaScript/CSS、Node JSDOM 测试。

## Global Constraints

- 截图仅由用户显式刷新触发，每台设备只保留最新成功图片。
- 所有截图读写接口都要求现有认证；不输出 Runner token。
- 忙碌只依据平台活动任务或可信 Sonic 状态，不根据亮屏/前台应用猜测。
- 忙设备不可选择；旧 Runner 和失败状态可降级，不影响原卡片和执行合同。

---

### Task 1: 服务端采集请求和安全存储

**Files:** `task_server/services/runner_service.py`, `task_server/router.py`, `tests/test_device_snapshot_sync.py`

**Interfaces:** `request_device_snapshots() -> dict`; `save_device_snapshot(payload) -> dict`; `/api/runners/refresh`; `/api/runner/device-snapshot`; authenticated image GET.

- [ ] 先写失败测试，覆盖请求、归属、PNG、大小、状态合并和活动任务。
- [ ] 运行专项测试，确认因接口缺失失败。
- [ ] 实现最小服务和路由，并让专项测试通过。

### Task 2: Windows Runner 只读采集

**Files:** `windows-midscene-runner.py`, `tests/test_runner_device_snapshot.py`

**Interfaces:** `capture_device_snapshot(adb_bin, device_id) -> dict`; heartbeat response consumes `snapshot_requests`.

- [ ] 先写 ADB 输出解析、PNG 校验、忙时不采集及上传载荷测试并确认红灯。
- [ ] 实现电量、温度、亮屏、前台应用与截图采集；限制大小并按请求上传。
- [ ] 运行专项测试和 Runner 语法检查。

### Task 3: 卡片刷新和呈现

**Files:** `js/app.js`, `css/app.css`, `js/agent-workbench.js`, `tests/agent_device_cards_check.js`

**Interfaces:** `refreshAgentRunnerDevices()`; card consumes `snapshot_url`, `snapshot_captured_at`, `usage_status`, `battery_level`, `battery_temperature_c`, `foreground_package`, `screen_on`, `snapshot_error`.

- [ ] 先写失败 DOM 测试，覆盖真实图片、占用禁选、失败保留和中文状态。
- [ ] 实现有界刷新、缓存破坏 URL、状态卡片及无障碍替代文本。
- [ ] 运行 DOM、前端静态和语法检查。

### Task 4: 整体验证与交接

**Files:** `CODEX_STATE.md`, `task-manager.html`

- [ ] 更新静态资源版本与交接记录。
- [ ] 跑后端静态、前端静态、专项测试、语法检查和 `git diff --check`。
- [ ] 提交并推送 main；部署后以 Safari 真机刷新验收。未完成 Windows Runner 升级或 FRP 恢复时明确记录阻塞，不用模拟数据代替。
