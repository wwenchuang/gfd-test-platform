# API 日常测试工作流 V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 Apifox → AI 用例 → MeterSphere 执行能力收敛为项目/业务线驱动、自动同步、环境级共享鉴权、按接口审阅的日常测试工作流。

**Architecture:** 保留 `source_id`、不可变 revision、异步 generation、MeterSphere adapter 和执行报告合同。新增一个不含秘密的环境级 auth profile read model，并在现有四个 API 页面之上增加统一流程条、业务线筛选和分组审阅；所有运行状态继续来自服务端事实。

**Tech Stack:** Python 3 标准库服务与 JSON 存储、MeterSphere `v3.6.5-lts` adapter、vanilla JavaScript/HTML/CSS、`unittest` 静态合同、Playwright 桌面/移动端视觉回归。

## Global Constraints

- `source_id` 继续作为 API 项目工作区边界；业务线由 revision 模块路径第一段派生。
- 单次 generation 包含 1-60 个 endpoint，每个顺序 AI batch 最多 12 个 endpoint。
- 同一 MeterSphere connection/project/environment 只配置一个公共 auth profile。
- 鉴权明文只写入 MeterSphere 环境变量；本地文件、浏览器、日志、计划和报告只保存引用元数据。
- 默认 UI 不显示 source/revision/auth/trace 内部 ID；技术详情必须按需展开并保持滚动位置。
- 服务端状态是流程完成度和下一步操作的唯一事实源。
- 不修改 UI Agent、Midscene YAML、Runner、Sonic、scorer、历史任务 YAML 或用户本地 Runner 文件。
- 不启动 Phase D/E/F，不改变千问模型策略。
- Codex 只本地提交，不 push；用户负责 push 和部署。

---

### Task 1: 固化工作流和环境共享鉴权合同

**Files:**
- Modify: `tests/api_project_workspace_checks.py`
- Modify: `tests/frontend_static_checks.py`
- Modify: `tests/visual_smoke_check.js`

**Interfaces:**
- Produces: environment auth profile behavior tests.
- Produces: workflow stepper, business-line filter and endpoint-grouped review selectors.
- Consumes: existing source-specific binding/auth routes and visual API fixtures.

- [ ] **Step 1: 写环境级复用的失败测试**

在 `ApiWorkspaceBindingChecks` 新增：

```python
def test_sources_in_same_environment_reuse_one_auth_profile(self):
    self._create_sources(2)
    api_workspace_service.save_api_workspace_binding(
        "api_source_a", "ms_project_a", "ms_env_shared",
    )
    api_workspace_service.save_api_workspace_binding(
        "api_source_b", "ms_project_a", "ms_env_shared",
    )
    first = api_workspace_service.save_api_auth_binding_metadata(
        "api_source_a",
        auth_type="bearer",
        header_name="Authorization",
        variable_name="MTP_API_AUTH_SHARED",
    )
    second = api_workspace_service.get_api_auth_binding("api_source_b")
    self.assertEqual(first["auth_ref"], second["auth_ref"])
    self.assertEqual(second["scope"], "environment")
    self.assertTrue(second["reused"])
```

再覆盖不同 environment 不复用、旧来源 metadata 可迁移、Profile 文件不含明文、清除
Profile 后所有关联来源均失效。

- [ ] **Step 2: 写 MeterSphere 远端写入次数失败测试**

为 `save_api_auth_binding()` 添加两个来源共享环境的测试，假 adapter 记录
`upsert_environment_variable()`：

```python
self.assertEqual(upserts[0]["variable_name"], upserts[1]["variable_name"])
self.assertEqual(first["auth_ref"], second["auth_ref"])
self.assertNotIn("runtime-secret", json.dumps(local_files, ensure_ascii=False))
```

更新同一 Profile 允许再次 upsert；仅切换来源但未更新密钥不产生远端写操作。

- [ ] **Step 3: 写前端可读性失败检查**

`frontend_static_checks.py` 要求下列函数/文案存在：

```python
for marker in (
    "renderApiWorkflowStepper",
    "apiBusinessLineOptions",
    "renderApiPlanEndpointGroups",
    "apiPlanReviewFilter",
    "环境公共鉴权",
    "生成 AI 用例",
):
    require(marker in api_testing_js, f"missing API daily workflow marker: {marker}")
```

同时阻止默认计划详情继续使用 `.api-case-table`。

- [ ] **Step 4: 写 Playwright 失败场景**

视觉 fixture 提供两个来源绑定同一 `project_id/environment_id`，并让 auth context 返回
同一 `auth_ref`。断言：

```javascript
await page.waitForSelector('.api-workflow-stepper');
if (await page.locator('.api-plan-selection-panel .api-endpoint-table').count()) {
  throw new Error('Plan page must not render the full endpoint table');
}
if (!/环境公共鉴权已复用/.test(await visibleText(page, '.api-auth-summary'))) {
  throw new Error('Shared environment auth is not visible');
}
```

增加 390px 无横向溢出、endpoint 分组展开、筛选保持和技术日志滚动保持检查。

- [ ] **Step 5: 运行并确认 RED**

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/frontend_static_checks.py
node tests/visual_smoke_check.js
```

Expected: auth profile、流程条或 endpoint 分组功能缺失导致预期失败。

---

### Task 2: 实现环境级公共鉴权 Profile

**Files:**
- Modify: `task_server/services/api_workspace_service.py`
- Modify: `task_server/services/metersphere_service.py`
- Modify: `task_server/services/api_test_plan_service.py`
- Modify: `tests/api_project_workspace_checks.py`

**Interfaces:**
- Produces: `get_environment_auth_profile(project_id, environment_id, connection_identity="") -> dict`
- Produces: `save_environment_auth_profile_metadata(source_id, *, auth_type, header_name, auth_ref="", variable_name="") -> dict`
- Produces: `clear_environment_auth_profile_metadata(source_id) -> dict`
- Keeps: `get_api_auth_binding(source_id)`, `save_api_auth_binding_metadata()` and existing routes as compatibility facades.

- [ ] **Step 1: 添加非秘密 Profile 路径和身份**

在 `api_workspace_service.py` 增加：

```python
def _auth_profile_identity(project_id: str, environment_id: str) -> str:
    return f"metersphere:{project_id}:{environment_id}"

def _auth_profile_path(project_id: str, environment_id: str) -> str:
    digest = _stable_hash(_auth_profile_identity(project_id, environment_id), 24)
    return safe_join(API_TESTING_DIR, "auth-profiles", f"{digest}.json")
```

Profile ID、auth ref 和变量名全部由 project/environment 身份确定，不包含 source ID。

- [ ] **Step 2: 实现 Profile 读写和公共响应**

公共响应固定包含：

```python
{
    "auth_ref": "...",
    "auth_type": "bearer",
    "header_name": "Authorization",
    "variable_name": "...",
    "project_id": "...",
    "environment_id": "...",
    "configured": True,
    "scope": "environment",
    "reused": False,
    "usage_count": 1,
}
```

`usage_count` 通过 workspace bindings 中相同 project/environment 的非秘密引用计算。
Profile 文件权限保持 `0600`，但仍禁止出现密钥字段和值。

- [ ] **Step 3: 兼容来源级 metadata**

`get_api_auth_binding(source_id)` 按以下顺序：

1. 读取来源当前 workspace binding。
2. 查找环境 Profile。
3. 若不存在且旧 binding 有有效 `auth_binding`，只迁移非秘密 metadata。
4. 返回 Profile，并根据关联来源数设置 `reused`。

保存 workspace binding 时不再复制完整来源 auth metadata，只保留可选 `auth_ref` 指针。

- [ ] **Step 4: 切换 MeterSphere 远端变量身份**

把：

```python
_api_auth_identity(source_id, environment_id)
```

改为接收 `project_id, environment_id`。`save_api_auth_binding()` 继续由 source route
进入，但从 workspace binding 解析 project/environment，向共享变量 upsert 后保存
Profile metadata。

- [ ] **Step 5: 安全清除共享 Profile**

`clear_api_auth_binding(source_id)` 删除共享远端环境变量，远端成功后再删除 Profile。
返回 `scope=environment` 和 `usage_count`，供 UI 明确提示影响范围。失败时 Profile 保留。

- [ ] **Step 6: 保持计划 drift 合同**

`api_test_plan_service._plan_auth_binding()` 接受新增
`project_id/scope/profile_fingerprint` 字段。drift 比较仍核对
`auth_ref/auth_type/header_name/variable_name/environment_id`，不把 `reused` 和
`usage_count` 作为计划身份，避免来源数量变化导致计划无意义过期。

- [ ] **Step 7: 运行 GREEN**

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/api_case_contract_checks.py
python3 tests/metersphere_v365_adapter_checks.py
python3 -m py_compile task_server/services/api_workspace_service.py task_server/services/metersphere_service.py task_server/services/api_test_plan_service.py
git diff --check
```

Expected: 环境共享、跨环境隔离、旧 metadata 迁移、清除和密钥不落盘全部通过。

- [ ] **Step 8: 提交后端合同**

```bash
git add task_server/services/api_workspace_service.py task_server/services/metersphere_service.py task_server/services/api_test_plan_service.py tests/api_project_workspace_checks.py
git commit -m "Share API authentication by MeterSphere environment"
```

---

### Task 3: 增加业务线和自动同步的用户状态

**Files:**
- Modify: `task_server/services/api_source_service.py`
- Modify: `task_server/services/api_module_service.py`
- Modify: `task_server/services/api_sync_service.py`
- Modify: `task_server/router.py`
- Modify: `tests/api_project_workspace_checks.py`
- Modify: `tests/api_asset_sync_checks.py`

**Interfaces:**
- Produces: `business_line_summary(endpoints) -> list[dict]`
- Produces: public source fields `sync_schedule`, `last_change_summary`.
- Consumes: existing module catalog, source scheduler and async sync records.

- [ ] **Step 1: 写业务线和调度失败测试**

测试 module paths 第一段派生、`未分组` 兜底、中文稳定排序，并验证 public source：

```python
self.assertEqual(source["sync_schedule"]["mode"], "automatic")
self.assertEqual(source["sync_schedule"]["interval_minutes"], 60)
self.assertTrue(source["sync_schedule"]["next_check_at"])
```

来源首次配置或 sync fingerprint 变化后应创建一个 queued sync；仅修改显示名称不重复
同步。

- [ ] **Step 2: 确认 RED**

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/api_asset_sync_checks.py -v
```

- [ ] **Step 3: 实现业务线 read model**

在 `api_module_service.py` 增加：

```python
def business_line_for_module(module_path: str) -> str:
    normalized = normalize_module_path(module_path)
    return normalized.split("/", 1)[0] if normalized else "未分组"
```

`business_line_summary()` 返回 name、module_count、endpoint_count，不改变 endpoint
所有权或持久化身份。

- [ ] **Step 4: 计算可验证的自动同步状态**

`_public_source()` 返回：

```python
"sync_schedule": {
    "mode": "automatic" if sync_enabled else "manual",
    "interval_minutes": sync_interval_minutes,
    "last_success_at": last_success_at,
    "next_check_at": computed_next_check,
    "status": last_sync_status,
}
```

时间只根据持久化 `last_attempt_at/last_success_at` 和周期计算，不由前端猜测。

- [ ] **Step 5: 配置变化立即排队**

来源保存 route 比较保存前后 `source_config_fingerprint`。新来源或 fingerprint 变化且
`sync_enabled/configured` 时调用现有异步 sync enqueue，一次保存只创建一个任务。
响应返回 `sync` 摘要；同步失败不回滚来源配置，也不替换活动 revision。

- [ ] **Step 6: 路由返回业务线摘要**

`/api/api-testing/assets` 在既有响应上追加：

```json
{
  "business_lines": [
    {"name": "家用业务", "module_count": 12, "endpoint_count": 268}
  ]
}
```

- [ ] **Step 7: 运行 GREEN 并提交**

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/api_asset_sync_checks.py -v
python3 -m py_compile task_server/services/api_source_service.py task_server/services/api_module_service.py task_server/services/api_sync_service.py task_server/router.py
git diff --check
git add task_server/services/api_source_service.py task_server/services/api_module_service.py task_server/services/api_sync_service.py task_server/router.py tests/api_project_workspace_checks.py tests/api_asset_sync_checks.py
git commit -m "Expose API business lines and automatic sync status"
```

---

### Task 4: 实现统一流程条和人性化资产页

**Files:**
- Modify: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `task-manager.html`
- Modify: `tests/frontend_static_checks.py`
- Modify: `tests/visual_smoke_check.js`

**Interfaces:**
- Produces: `renderApiWorkflowStepper(context) -> string`
- Produces: `apiWorkflowNextAction(context) -> object`
- Produces: `apiBusinessLineOptions(endpoints) -> list`
- Consumes: public source sync schedule, asset business lines, generation/plan/execution state.

- [ ] **Step 1: 实现纯前端流程状态函数**

`apiWorkflowNextAction()` 严格按设计规格中的十级优先级返回：

```javascript
{step: 'assets', label: '同步接口', handler: 'showApiAssetsPage()'}
```

不得读取按钮本地状态判断完成度。

- [ ] **Step 2: 渲染五步流程条**

每个 `showApi*Page()` 的页面根节点首项调用同一
`renderApiWorkflowStepper()`。默认文本只显示项目、业务线、版本时间和中文状态；
内部 ID 放入 `<details class="api-workflow-tech-detail">`。

- [ ] **Step 3: 重排资产页**

资产页默认顺序：

1. 流程条。
2. 项目和业务线选择。
3. 自动同步摘要与唯一主操作。
4. 业务线下模块树和接口。
5. 备用上传与技术日志。

配置继续使用现有设置区域，但改为抽屉式按需打开。主操作根据状态显示“同步接口”、
“正在同步”或“重试同步”；普通刷新和设置使用图标按钮及 `aria-label`。

- [ ] **Step 4: 限制大范围选择**

`toggleApiEndpointSelection()` 和“选择当前模块”先计算筛选结果。超过 60 时不修改
selection，展示：

```text
当前范围有 971 个接口，单次最多生成 60 个。请继续选择子模块或搜索接口。
```

不自动选择前 60 个。计划入口显示当前已选数量和预计批次数。

- [ ] **Step 5: CSS 响应式**

新增 `.api-workflow-stepper`、`.api-scope-bar`、`.api-sync-summary` 和
`.api-business-line-switcher`。卡片圆角不超过 6px；不使用装饰渐变或嵌套卡片。
390px 下步骤条折叠为当前步骤摘要，模块和接口容器独立滚动。

- [ ] **Step 6: 更新静态版本串**

`task-manager.html` 中 `round5.css` 和 `api-testing.js` 的 query version 同步更新，
避免部署后浏览器继续使用旧 UI。

- [ ] **Step 7: 运行 GREEN 并提交**

```bash
python3 tests/frontend_static_checks.py
node tests/visual_smoke_check.js
git diff --check
git add js/api-testing.js css/round5.css task-manager.html tests/frontend_static_checks.py tests/visual_smoke_check.js
git commit -m "Clarify the API asset workflow"
```

---

### Task 5: 重构用例生成和按接口审阅

**Files:**
- Modify: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `tests/frontend_static_checks.py`
- Modify: `tests/visual_smoke_check.js`

**Interfaces:**
- Produces: `renderApiPlanScopeSummary()`
- Produces: `apiPlanReviewFilter`
- Produces: `groupApiPlanCasesByEndpoint(cases) -> list`
- Produces: `renderApiPlanEndpointGroups(plan) -> string`
- Consumes: existing plan `cases`, `execution_readiness`, `revision_state`, `binding_drift`.

- [ ] **Step 1: 将计划页改为范围摘要**

移除计划页默认 `renderApiAssetTable(apiTestingEndpoints)`。显示业务线、模块、选中接口
数、预计 batch 数和生成规则摘要。没有选择时，主操作为“去选择接口”；1-60 条时为
“生成 AI 用例”。

- [ ] **Step 2: 简化生成进度**

默认只显示四个用户阶段和整体进度。已有 batch 列表、generation ID、provider/model
和日志移动到 `<details class="api-plan-tech-detail">`，继续复用已有展开和滚动状态。

- [ ] **Step 3: 聚合待补数据**

新增纯函数按 missing path 前缀聚合：

```javascript
{
  request: {label: '请求数据', count: 18},
  assertions: {label: '断言', count: 4},
  auth: {label: '鉴权', count: 1},
}
```

点击聚合项设置 review filter，不一次铺开全部字段 chip。

- [ ] **Step 4: 按 endpoint 分组用例**

用 request method/path 或 endpoint stable label 分组。分组 header 展示 method、path、
接口名、总用例、可执行和待补数量；展开后使用纵向 case row，展示中文类型、请求摘要、
断言、鉴权和缺失原因。

默认每页 20 个 endpoint，支持搜索和“全部/可执行/待补数据/已变化”筛选。筛选、分页
和展开状态保存在当前 plan ID 的页面状态中。

- [ ] **Step 5: 收敛计划历史卡片**

历史列表默认只显示计划名称、业务线/模块、接口数、用例数、状态和时间。AI trace、
binding ID、revision ID 和 auth variable 移入技术详情。生成完成后自动打开最新草稿。

- [ ] **Step 6: 唯一主操作**

`renderApiPlanDetail()` 根据状态只渲染一个 primary：

```javascript
draft + ready       -> 确认计划
draft + missing     -> 查看待补数据
confirmed + fresh  -> 去执行
stale              -> 按最新接口重新生成
```

辅助操作不与主操作竞争视觉层级。

- [ ] **Step 7: 运行 GREEN 并提交**

```bash
python3 tests/frontend_static_checks.py
node tests/visual_smoke_check.js
git diff --check
git add js/api-testing.js css/round5.css tests/frontend_static_checks.py tests/visual_smoke_check.js
git commit -m "Make API plan generation reviewable"
```

---

### Task 6: 收敛执行页的公共鉴权和下一步

**Files:**
- Modify: `js/api-testing.js`
- Modify: `css/round5.css`
- Modify: `tests/frontend_static_checks.py`
- Modify: `tests/visual_smoke_check.js`

**Interfaces:**
- Produces: compact `.api-auth-summary`.
- Consumes: environment auth profile `scope/reused/usage_count`, current project/environment and executable plans.

- [ ] **Step 1: 默认显示公共鉴权摘要**

已配置时只显示：

```text
APP 测试环境 · Bearer 鉴权已就绪 · 环境公共配置
```

`reused=true` 时补充“已自动复用”。Token 表单只在未配置或用户主动点“更新鉴权”时
出现。

- [ ] **Step 2: 明确共享清除影响**

清除按钮文案改为“清除该环境鉴权”，确认提示包含 `usage_count`。用户取消时不发
DELETE。更新只替换远端变量，不要求每个来源重新配置。

- [ ] **Step 3: 执行计划按当前范围展示**

计划列表只突出名称、业务线、用例数、最近运行和一个“推送并执行”主操作。不可执行
计划显示具体原因和“去处理”入口；内部 binding/auth ID 进入技术详情。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
python3 tests/frontend_static_checks.py
node tests/visual_smoke_check.js
git diff --check
git add js/api-testing.js css/round5.css tests/frontend_static_checks.py tests/visual_smoke_check.js
git commit -m "Reuse environment authentication in API execution"
```

---

### Task 7: 全量验证、视觉复核和交接

**Files:**
- Modify: `CODEX_STATE.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified deployment handoff without push.

- [ ] **Step 1: 聚焦后端回归**

```bash
python3 tests/api_project_workspace_checks.py -v
python3 tests/api_asset_sync_checks.py -v
python3 tests/api_case_contract_checks.py
python3 tests/api_runtime_recovery_checks.py -v
python3 tests/metersphere_v365_adapter_checks.py
```

- [ ] **Step 2: 静态与全量回归**

```bash
python3 tests/frontend_static_checks.py
npm test
git diff --check
```

- [ ] **Step 3: 人工检查视觉产物**

检查 Playwright 生成的桌面和 390px 截图：

- 业务线、自动同步、下一步操作首屏可见。
- 计划页没有 977 行接口和 1590 行用例。
- 用例名称、method/path 不单字竖排。
- 公共鉴权不重复显示输入框。
- 页面级无横向溢出。
- 技术日志展开和滚动在轮询后保持。

- [ ] **Step 4: 更新交接状态**

`CODEX_STATE.md` 记录实现范围、测试结果、未完成的真实 QA 验收和以下边界：

- Codex 未 push。
- 未修改用户历史 YAML、Sonic、scorer、Runner 本地脚本和草稿目录。
- 部署后需在真实 3D 项目验证自动同步、共享环境鉴权、小批生成、确认、执行和报告。

- [ ] **Step 5: 最终本地提交**

```bash
git add CODEX_STATE.md
git commit -m "Document API daily workflow verification"
git status --short --branch
```

不得暂存或提交用户已有改动。

