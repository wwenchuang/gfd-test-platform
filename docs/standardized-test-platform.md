# 标准化测试平台落地边界

本文记录本轮综合所有方案后的取舍，避免继续把系统做成难维护的抽象堆叠。

## 已落地能力

- 输入标准化：Agent 使用 `AgentContext` 统一承接文本、Figma、截图和文件来源。
- 工具调用隔离：Agent 工具通过 `ToolRegistry` 访问，保留现有工具白名单。
- 执行适配：`ExecutionAdapter` 默认走 Windows/Mac Runner，Sonic 单条临时套件不再作为默认执行方式。
- DAG 外壳：`DAGWrapper` 和 `SimpleDAG` 提供可选顺序 DAG 调试能力，不替换现有主链路。
- 并行 DAG：`ParallelDAGRunner` 可用于安全调试，默认仍按调用方给定批次执行。
- 可观测性：`Span`、`Tracer` 记录节点级执行信息。
- Trace Debugger：`TraceExporter` 从真实 Agent Run、Runner Job、内存 DAG Span 导出统一 Trace。
- Replay/Diff：`SnapshotStore` 持久化执行快照，`ReplayEngine` 默认 dry-run，`DiffEngine` 对比两次执行差异。
- 前端入口：执行中心新增“Trace 回放”，独立页面 `trace-viewer.html` 可查看真实链路。

## 暂不落地的能力

- 系统自重构、自改代码、多 Agent 自编程不进入生产链路。
- 自进化策略不自动修改 DAG、prompt、model，只作为后续人工评审资料。
- Replay 默认不直接创建执行任务，防止误触发真机；需要显式关闭 dry-run。

## 验收重点

- 原 YAML 编辑、Runner 单条调试、Sonic 同步、Agent 执行接口不被替换。
- 新增 Debug API 必须基于真实存储数据，不返回演示数据。
- API Key、Runner Token、Session Secret 不进入 Trace 输入输出。
- 打包、安装、Docker 静态同步必须包含 `trace-viewer.html`。
