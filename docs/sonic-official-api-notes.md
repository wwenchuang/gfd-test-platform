# Sonic 官方接口核验记录

本文件记录 Task 平台涉及 Sonic 执行、同步、回调时必须遵守的官方源码结论。

## 核验来源

- 官网入口：https://soniccloudorg.github.io/
- Gitee 组织：https://gitee.com/sonic-cloud
- GitHub 官方镜像：https://github.com/SonicCloudOrg/sonic-server
- 本次核验源码提交：`90d333c`

## 执行入口结论

Sonic 后端执行入口在测试套，不在单个测试用例。

- `sonic-server-controller/src/main/java/org/cloud/sonic/controller/controller/TestSuitesController.java`
  - `@RequestMapping("/testSuites")`
  - `@GetMapping("/runSuite")`
  - 调用 `testSuitesService.runSuite(id, strike)`
  - `@PutMapping` 用于保存测试套
  - `@GetMapping` 用于按 `id` 查询测试套详情
- `sonic-server-controller/src/main/java/org/cloud/sonic/controller/controller/TestCasesController.java`
  - 提供查询、保存、删除、复制等用例管理接口
  - 未提供 `runCase` / `runTestCase` 之类单用例执行 REST
- `sonic-server-controller/src/main/java/org/cloud/sonic/controller/services/impl/TestSuitesServiceImpl.java`
  - `runSuite` 会读取测试套内 `testCases` 与 `devices`
  - 测试套为空或设备为空时不能执行
- `sonic-server-controller/src/main/java/org/cloud/sonic/controller/models/interfaces/CoverType.java`
  - `CASE = 1`
  - `DEVICE = 2`

## Task 平台实现规则

1. “Runner 单条调试”走 Task 平台 Runner：`/api/run-request` + `target_task_name`，用于快速调试一条或几条 Midscene YAML task。
2. 不再提供 “Sonic 临时套执行”。虽然可以技术上创建只包含当前 Sonic case 的临时测试套再调用 `/testSuites/runSuite`，但这会污染 Sonic 的测试套和执行结果列表，容易和正式基线回归混淆。
3. 不允许把绑定的完整回归测试套当成单条调试入口，否则会误跑整套。
4. Sonic 批量回归仍使用应用绑定的正式测试套。
5. 同步到 Sonic 前必须确保 YAML 状态是 `active` 或 `baseline`，草稿状态只允许人工强制同步。
6. 桥接脚本必须包含 `/api/sonic/bridge-groovy` 和 `setRequestProperty("x-token", runnerToken)`，否则会出现 401 或无法回调 Task 平台。

## 排障提示

- Sonic 平台已有用例但同步失败，优先检查 Task 平台读取的 `module/file/taskName` 是否指向正式 `TASK_DIR`，以及 `task-meta` 状态是否为 `active/baseline`。
- “旧模板需要人工确认”不代表 Sonic 没有用例；它表示 Sonic 里的 Groovy 步骤不是当前托管桥接模板，平台无法安全自动替换。
- “单条执行却跑整套”说明使用了正式回归测试套执行；应改用 Runner 单条调试。
