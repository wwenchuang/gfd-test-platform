# MeterSphere 3.6.5 Real Adapter Design

## Objective

把当前 MeterSphere 的“可配置通用路径”升级为对 `v3.6.5-lts` 官方合同的真实适配：平台将已确认且未过期的结构化 API case 幂等写入 MeterSphere，组成一个真实 API 场景，触发远端执行，并以远端 report ID 轮询和回收报告。

本阶段只闭合 API 自动化链路，不迁移 UI Agent、Runner、Sonic 或现有报告所有权。

## Verified Runtime

- 目标版本：`v3.6.5-lts-f043cdd2`，对应官方 `v3.6.5-lts` tag。
- 目标项目：由配置中的 `project_id` 动态选择，不写死 `3D业务` 名称。
- 目标环境：由 `/api/test/env-list/{project_id}` 动态读取并按配置选择。
- 认证：MeterSphere 3.6.5 Access Key 合同，header 仅为 `accessKey` 与 `signature`；`signature` 是以 Secret Key 为 AES key、Access Key 为 IV，对 `accessKey|nonce|timestamp` 做 AES-CBC/PKCS7 后的 Base64。
- 已确认读取合同：版本、项目、环境、API definition、API case、API scenario、scenario module 和 scenario report。
- 写入合同必须同时满足：精确版本支持、认证通过、项目/环境实时有效、官方源码合同匹配以及 QA 写入回归通过。

## Architecture

新增独立 `MeterSphereV365Adapter`，由现有 `metersphere_service.py` 负责配置、异步本地 execution 和浏览器 API；adapter 只负责远端协议和对象映射。

```text
confirmed API plan
        |
        v
MeterSphereV365Adapter.probe
        |
        +-- exact version / project / environment / permissions
        |
        v
definition exact match (method + normalized path)
        |
        v
managed API case upsert
        |
        v
managed API scenario upsert
        |
        v
scenario run -> real reportId -> poll -> normalized report
```

`metersphere_service.py` 保留旧通用路径作为非 3.6.5 兼容回退，但 3.6.5 一旦被识别就不能再依赖用户手填的 case/run/report path。

## Ownership And Idempotency

- 每个远端 case 使用稳定标记 `[MTP:<identity_hash>]`，identity 来源为 `plan_id + case_id`。
- 每个远端 scenario 使用稳定标记 `[MTP:<plan_hash>]`，identity 来源为 `plan_id`。
- 本地 binding 只保存 provider identity、remote IDs、内容 hash、版本和时间，不保存请求值、环境变量或凭据。
- 更新前必须验证远端对象仍属于当前项目、当前 definition 且名称带相同稳定标记；否则阻断，不接管用户对象。
- binding 丢失时允许按稳定标记精确找回；出现多个候选时阻断，不猜测。
- 相同内容重复推送不更新；内容变化只更新平台拥有的远端对象。

## Case Mapping

- definition 必须以 HTTP method 和规范化 path 唯一匹配；零个或多个匹配都阻断对应 case。
- path/query/header/body 分别映射到 MeterSphere `rest/query/headers/body`。
- `auth_ref=environment_default` 不复制任何 token，只由所选 MeterSphere 环境注入。
- `Authorization / Cookie / Token / API Key / Access Key / Secret / Signature / Password / Credential` 等敏感鉴权 Header 在远端写入前直接阻断，不能依赖日志脱敏掩盖不安全合同。
- 显式鉴权移除用例只有在 adapter 能证明环境鉴权可被覆盖时才可执行；否则报告为 adapter incompatibility。
- status `in` 断言映射为单值 `EQUALS` 或多值 `REGEX`。
- `schema_ref=response:2xx` 映射为基于 OpenAPI response schema 的结构断言。报告必须标明实际覆盖级别（required fields/type 或 root-only），不能宣称完整 JSON Schema 校验。
- adapter 不认识的请求、变量、依赖或断言类型会阻断推送，不能静默丢弃。

## Scenario And Execution

- 使用 API scenario，不要求项目开启 Test Plan 模块。
- 场景步骤按 `executable_api_cases()` 的依赖顺序引用远端 API case，`stepType=API_CASE`、`refType=REF`。
- 场景绑定所选 environment；失败策略保留 MeterSphere 的可审计默认值。
- MeterSphere 3.6.5 官方前端在执行前生成 UUID `reportId`，并通过 `POST /api/scenario/run` 随完整 `ApiScenarioDebugRequest` 请求体传入。adapter 使用同一合同，把本地稳定 step ID 写回每个运行步骤的 `uniqueId`；只有远端响应 `taskItem.reportId` 非空时才接受为真实 run/report ID，不生成失败后的替代 ID。
- 轮询 `/api/report/scenario/get/{report_id}`。远端明确 `COMPLETED / STOPPED` 时按其状态结束；读取失败保留 running 并记录有限事件。
- 如果所有请求步骤都已有 `SUCCESS / ERROR / FAKE_ERROR` 终态，但主报告超过 5 分钟仍未回写终态，adapter 以 `provider_terminal_state_missing` 失败收口并继续同步真实步骤报告。该分支绝不推断成功，也不让本地 execution 白等到通用 1800 秒上限。
- 报告回收时保留 remote IDs、远端终态和 case 结果摘要，原始响应先递归脱敏再持久化。
- 归一化 case 结果只接收 binding 中的稳定步骤，或 MeterSphere 明确标记为请求类型的步骤；分组、控制流等容器节点不得被计为独立用例。

## Security

- `cryptography` 是服务端依赖；缺失时 Access Key 认证 fail closed。
- Access Key、Secret Key、Authorization、Cookie、环境变量值、签名及嵌套 key/value 形式的敏感 header 都不得进入缓存、binding、事件、报告或浏览器响应。
- adapter 不读取或返回环境详情中的变量值；只使用环境 ID。
- 网络错误中远端 body 也必须经过脱敏。

## Capability Model

capability 不再由“路径非空”推导。3.6.5 capability 由 probe 返回：

- `can_read_assets`：版本、项目、环境、definition 和 case 查询均成功。
- `can_push`：精确支持版本、认证、项目/环境和写合同均已验证。
- `can_run`：scenario/module/report 合同可读且写合同已验证。
- `can_query_run` / `can_pull_report`：scenario report 合同可用。
- 任一实时元数据使用过期缓存时，页面可以展示但 `ready=false`，禁止执行。

## Compatibility And Failure Semantics

- 非 3.6.5 或版本不可读时不自动写入，继续显示明确的不支持原因。
- legacy token/custom-path 模式保留，现有测试和已有部署不被强制迁移。
- push 部分成功后失败时保留已建立 binding；重试从 binding 幂等续跑。
- 场景触发失败、远端执行失败和报告同步失败分别落在现有四阶段状态中，不能合并成模糊失败。远端执行失败后仍尝试同步报告；报告可用时 `metersphere_run=failed / sync_report=succeeded / overall=failed`，保留真实失败步骤。
- 报告步骤只能通过运行请求中的稳定 `uniqueId` 回映本地 `case_id`，不能按名称猜测。远端场景步骤与本地 binding 不一致时禁止触发。

## Acceptance

- AES 签名使用固定向量测试，并通过真实 `/user/api/key/validate`。
- 嵌套 `{"key":"Authorization","value":"..."}` 等形式被脱敏测试覆盖。
- definition 唯一匹配、case/scenario create/update/no-op/recovery/ambiguity 均有失败优先测试。
- 真实 QA 至少完成一个 executable case 的首次写入、重复推送无重复、场景触发、远端终态和报告回收；若环境只返回步骤结果而不回写主终态，必须保留 `provider_terminal_state_missing` 失败证据，不能把该环境验收写成成功。
- 全量 `npm test` 通过，受保护用户文件不进入提交。
