# API Testing MVP Design

## Goal

第一阶段先跑通接口测试闭环，不做大平台重构：

```text
Apifox OpenAPI JSON
  -> API 资产导入
  -> AI 生成接口测试计划
  -> 人工确认
  -> 推送 MeterSphere
  -> MeterSphere 执行
  -> 平台回收报告和失败归因
```

Apifox 仍是接口资产源，MeterSphere 仍是执行和测试管理引擎，本平台只做 AI 测试开发中枢和统一报告入口。

## Non-Goals

- 第一阶段不接 Apifox token 自动同步。
- 第一阶段不自研 API Runner，也不在平台内直接执行 HTTP 测试。
- 第一阶段不替换 MeterSphere 的用例库、测试计划和报告能力。
- 第一阶段不改现有 UI 自动化 Sonic/Midscene/Runner 主链路。
- 第一阶段不允许 AI 静默覆盖 MeterSphere 已有用例。

## Sidebar And Pages

新增左侧分组：

```text
接口测试
  API 工作台
  接口资产
  AI 用例计划
  MeterSphere 执行
  API 报告
```

`API 工作台` 展示当前 OpenAPI 导入状态、MeterSphere 连接状态、最近接口变更、最近执行结果和待处理失败。

`接口资产` 展示从 OpenAPI 导入的接口列表，包括模块、method/path、请求 schema、响应 schema、示例、覆盖状态、最近执行状态和 schema hash。

`AI 用例计划` 选择接口范围后生成测试矩阵，包括成功流、必填参数缺失、边界值、鉴权、异常码、响应断言和简单接口链路。生成结果默认是草稿，必须人工确认后才能推送 MeterSphere。

`MeterSphere 执行` 创建或绑定 MeterSphere 测试计划，触发执行并轮询真实执行状态。运行日志展开状态按 `runId + stepId` 本地保持，刷新不能自动收回。

`API 报告` 拉取 MeterSphere 报告并做统一归因：接口缺陷、环境问题、鉴权失效、测试数据问题、断言问题、Apifox 文档与实际不一致。

## Backend Components

新增服务边界：

- `api_asset_service.py`：导入 OpenAPI JSON，生成 API snapshot、endpoint、schema hash 和变更记录。
- `api_test_plan_service.py`：把 API 资产交给 AI 生成接口测试计划，并保存人工确认状态。
- `metersphere_service.py`：封装 MeterSphere 连接、项目/环境查询、用例创建、计划创建、执行触发和报告查询。
- `api_report_service.py`：归并 MeterSphere 执行结果，并调用 AI 做失败分类和修复建议。

第一阶段只需要最小数据落盘：

- API source/snapshot
- API endpoint
- AI generated API case draft
- MeterSphere push mapping
- MeterSphere run/report summary

## Credentials

第一阶段需要 MeterSphere 配置：

- base URL
- token 或 access key/secret
- workspace/project ID
- environment ID
- 创建/更新用例、创建计划、执行计划、读取报告权限

OpenAPI 第一阶段通过文件上传导入，不需要 Apifox token。后续自动同步时再增加 Apifox token、project ID 和环境映射。

所有 token 只保存在服务端配置或加密存储中，前端和技术日志只显示脱敏状态。

## MVP Flow

1. 用户在 `接口资产` 上传 Apifox 导出的 OpenAPI JSON。
2. 平台解析接口并生成 API snapshot。
3. 用户选择接口范围，进入 `AI 用例计划`。
4. AI 生成测试矩阵和断言草稿。
5. 用户确认要推送的用例。
6. 平台通过 MeterSphere API 创建或更新用例。
7. 用户创建/选择测试计划并触发执行。
8. 平台轮询 MeterSphere 执行状态和报告。
9. 平台展示统一报告和 AI 失败归因。

## Acceptance Criteria

- 可以上传 OpenAPI JSON 并看到接口资产列表。
- 可以按接口或模块生成 AI 接口测试计划草稿。
- 推送 MeterSphere 前必须有人工确认。
- 可以配置并检测 MeterSphere 连接。
- 可以创建或绑定 MeterSphere 测试计划并触发执行。
- 可以拉回 MeterSphere 执行结果并在平台展示 API 报告。
- 技术日志展开状态在刷新后保持，不会立即收回。
- 不影响现有 Agent、YAML、Runner、Sonic 页面和执行链路。

## Implementation Order

1. 增加页面入口和空状态，先把信息架构接进去。
2. 增加 OpenAPI 上传解析和 API 资产列表。
3. 增加 MeterSphere 连接配置和健康检查。
4. 增加 AI 用例计划草稿生成。
5. 增加推送 MeterSphere 和执行计划触发。
6. 增加报告拉取、失败归因和日志展开状态保持。
