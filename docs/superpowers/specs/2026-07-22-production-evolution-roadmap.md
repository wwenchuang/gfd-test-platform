# AI Test Engineering Platform Production Evolution Roadmap

## Objective

在不推翻现有 UI Agent、Midscene YAML、Runner、Sonic、DAG、Replay、Shadow、AI Skill、Apifox 和 MeterSphere 模块的前提下，把已经存在的能力按生产依赖串成可验证闭环。

本路线图基于 2026-07-22 当前仓库与真实线上状态，而不是附件中的历史判断。每个阶段必须产生可独立部署、可回归、可回滚的结果；后续阶段不得用伪路径、伪状态或猜测的第三方合同绕过前置门槛。

## Current Reality

| 能力 | 当前状态 | 结论 |
| --- | --- | --- |
| `ExecutionFacade` / `ExecutionAdapter` | 已有 Router 调试入口、local Runner、DAG、parallel、shadow | 保留；暂不迁移 Agent 主链 |
| Apifox source / 增量同步 | 已完成并在线同步 971 个接口 | Phase A 完成 |
| 不可变 API revision / schema diff / impact | 已完成 | 不重复建设 |
| AI Skill / Gateway 路由与运行 trace | UI Agent 主链已接入；`api_test_designer` 已使用显式 `generate_case` action 与规范化 trace | 不重复建设，后续只补跨执行引擎 trace |
| YAML scorer / validator / Top3 基线 / smoke gate | 已在真实 Agent 多轮回归中运行 | 持续回归，不做全量 Prompt 重写 |
| 失败分类 | 已有设备、ADB、模型、脚本、产品等分类规则 | 不新增重复 `failure_classifier.py` |
| MeterSphere 读取 | 3.6.5 精确版本、真实项目、环境、definitions、cases、scenarios 和 reports 已接通 | Phase C 读取合同完成 |
| MeterSphere 写入/执行 | 3.6.5 case/scenario 幂等写入和真实 `POST /api/scenario/run` 已接通；QA 主报告终态回写异常 | Phase C 代码完成，真实终态验收阻断 |
| API 用例计划 | 已升级为结构化请求、断言、依赖、readiness 和 revision 门禁 | Phase B 完成 |
| API 报告 | 有独立归一化和存储 | 需在真实执行后收敛为 canonical envelope |
| Feishu | service 已有，业务仍可直接调用 | 待 Event Center 阶段收敛 |
| UI/API 统一资产 | 两套资产均真实存在，尚无统一索引合同 | 后置，不迁移底层存储 |
| DAG / observability / replay / shadow | 基础存在，未成为所有生产任务主链 | 先通过 shadow 证据再推广 |

## Source Plan Crosswalk

用户提供的 Production Evolution Plan 是本路线图的需求来源，但执行顺序遵循用户随后确认的“API 闭环优先”，并结合仓库已存在能力去重：

| 原文能力方向 | 仓库/交付状态 | 本路线图处理 |
| --- | --- | --- |
| `ExecutionFacade / ExecutionAdapter` 收敛 Sonic 与 MeterSphere | 抽象、DAG、parallel、shadow 已存在 | Phase D 只注册 MeterSphere API execution/report；UI Agent 先 shadow，不重建 executor |
| Apifox 增量同步、API Asset、schema diff/impact | 已真实同步 971 个接口，revision/diff/impact 已完成 | Phase A 完成 |
| AI API Analyzer、用例计划、Decision Trace、模型 route | 结构化 `api_case_contract/v1`、readiness、stale 门禁、`generate_case` route/trace 已完成 | Phase B 完成 |
| YAML 可执行性、Top3 基线、smoke 状态和失败修复 | 已在 UI Agent 真实回归中运行，设备/脚本/产品失败分类已存在 | 保持主链回归；不新增重复 classifier，不做全量 Prompt 重写 |
| MeterSphere 项目、环境、用例、场景、执行与报告 | 精确 3.6.5 adapter 已实现；QA 主报告终态回写阻断 | Phase C 当前阶段 |
| UI/API 统一执行记录、统一报告 envelope | 尚未统一，现有报告继续由各领域持有 | Phase D，先兼容和 shadow 再迁移 |
| Event Center、Feishu 解耦、补偿、RBAC、审计 | Feishu service 已有，持久化 outbox 与角色门禁未实现 | Phase E |
| Test Asset Center、Dashboard、DAG/Observability 晋级 | 底层资产与观测工具已有，统一 read model 未实现 | Phase F，不迁移底层存储 |
| 千问模型升级 | 当前主链仍使用已验证模型 | 读取在线目录、验证结构化输出/Midscene family，并在固定 OPPO shadow 通过后再切换 |

## Delivery Order

### Phase A: API Source and Asset Foundation

状态：完成。

验收证据：Apifox `3D` 项目 `5904970` 首次同步 `added=971`，第二次同步 `no_change / unchanged=971`，活动 revision 仅 1 个。

### Phase B: Executable API Case Contract

状态：完成。

交付：

- 每条 API case 必须包含结构化 `request / assertions / variables / dependencies / readiness`。
- 平台从 OpenAPI 确定性生成 method、path、参数位置、成功/错误状态与 schema 断言。
- 缺少必填测试数据时标记 `needs_review`，不能靠占位文案变为 executable。
- 计划保存 `executable_case_count / needs_review_case_count / execution_readiness`。
- 资产 revision 改变且命中计划 endpoint 时，计划动态标记 `stale` 并阻止确认、推送和执行。
- MeterSphere payload 只接收 executable cases。
- `api_test_designer` 使用显式 `generate_case` 路由并输出可审计 trace。

退出门槛：合同单元测试、API route 测试、前端状态测试、全量 `npm test` 全部通过；旧 plan 仍可读取但默认不能被误判为新合同 executable。

完成证据：23 个合同/计划/MeterSphere 聚焦测试、27 个 Apifox 增量资产测试、后端 61 项、前端 69 项、Gateway 46 项、4 个 Skill fixture、动态模型目录检查和桌面/移动端 Playwright 全部通过。Phase C 仍需验证 MeterSphere `3.6.5-lts` 的真实写接口、环境变量绑定和远端对象映射；本阶段没有用通用路径配置冒充该能力。

### Phase C: MeterSphere 3.6.5 Real Adapter

状态：适配器实现完成，QA 主报告终态回写阻断；聚焦规格见 `2026-07-23-metersphere-v365-real-adapter-design.md`。

交付：

- `MeterSphereV365Adapter.probe()` 识别版本、模块权限和真实执行策略。
- 读取并匹配 API definitions，按 method/path 和 provider identity 保存映射。
- 对已验证能力实现幂等 case/scenario upsert、execution binding、trigger、poll 和 report。
- 不再以“填写了路径”代表 capability ready；未经 QA 捕获验证的写接口保持阻断。
- 在 `3D业务` 项目完成至少一个 executable case 的真实推送、执行与报告回收。

当前证据：

- QA 精确版本 `v3.6.5-lts-f043cdd2`、动态项目/环境和全部读写 capability 已通过；同一受控 case/scenario 连续两次同步均为 `unchanged=1`，没有重复远端对象。
- 官方 `POST /api/scenario/run` 返回真实 report ID；报告步骤通过稳定 `uniqueId` 正确回映 `API-LIVE-001`，真实 HTTP 结果为 `200 / 47ms / 289B`。
- QA 默认资源池在 6 分钟后仍将主报告保留为 `PENDING / status=- / endTime=null`。平台按 `provider_terminal_state_missing` 失败收口并同步步骤报告，没有把步骤成功冒充整次执行成功。
- 26 个 3.6.5 adapter 聚焦回归覆盖精确认证、递归脱敏、HTTP 错误 body 脱敏、版本 capability、case/scenario 幂等、真实触发、稳定报告映射、敏感鉴权 Header 阻断、报告容器过滤和缺失主终态失败收口。
- 在 MeterSphere 资源池或回调链恢复真实主报告终态前，不启动 Phase D 的生产迁移。

退出门槛：真实 remote IDs、终态、报告和失败证据均可回查；重复请求不创建重复远端对象。

### Phase D: Canonical Execution and Report Contracts

状态：待 Phase C。

交付：

- API execution request/handle 注册到 `ExecutionFacade` 的 MeterSphere adapter。
- 保持 Agent/Runner 当前入口，先用 shadow 对比，再按证据逐步迁移。
- API 和 UI 报告通过共享 envelope/index 查询，底层原始报告仍保留各自所有权。
- 失败 taxonomy 统一字段，不强行合并现有领域分析器。

退出门槛：兼容旧 routes、jobs、reports；shadow 无阻断差异后才能切换生产入口。

### Phase E: Event Center, Feishu and RBAC

状态：待 Phase D。

交付：

- 增加持久化事件 outbox；业务发布领域事件，Feishu 只作为 subscriber。
- 支持任务开始、完成、失败、smoke 阻断、API 失败、AI 生成失败和基线变化事件。
- 增加幂等投递、重试、脱敏和审计。
- 从现有单管理员认证演进到最小角色权限：查看、执行、配置、凭据管理。

退出门槛：业务服务不直接依赖 Feishu；权限门禁有 route 级回归。

### Phase F: Unified Asset Index and Workflow Promotion

状态：最后实施。

交付：

- 在不移动 UI baseline/API revision 存储的前提下增加统一 asset index/read model。
- Dashboard 基于统一索引展示来源、revision、最近状态、成功率与受影响任务。
- DAG 只负责依赖表达，平台 scheduler 负责执行；AI 不能直接决定跳过确定性门禁。
- Replay、Shadow、Observability 成为每次迁移的验收工具，而不是先行重构理由。

## Global Constraints

- 不修改或覆盖用户历史 YAML、`sonic_service.py`、`yaml_executable_scorer.py`、本地 Windows Runner 脚本和草稿目录。
- 不猜测 MeterSphere 写接口，不以配置路径存在代替能力验证。
- 不让 AI 决定 schema diff、case readiness、smoke 是否扩展、执行终态或报告通过率。
- 不新增 Midscene 1.7.10 不支持的 action/字段。
- 不一次迁移 UI Agent 主链；跨执行引擎收敛必须先有 shadow 证据。
- 所有凭据只保存在服务端，日志、计划、报告和浏览器响应不得包含明文。
- Qwen 升级以在线模型目录、结构化输出能力和固定设备 shadow 证据为门槛，不把“最新”型号字符串直接替换进已验证的 Midscene 主链。
- 每个 Phase 都更新 `CODEX_STATE.md`，由用户 push 和部署，Codex 不 push。
