# Prompt Center Business Context Guide

Prompt Center 是功夫豆测试平台的统一业务上下文入口。它不替代具体 skill，
而是把用户目标、需求资料、Figma/截图、页面知识、风险动作和业务主链整理成
可复用结构，再交给各 skill 执行专业判断。

## 输入优先级

当 `payload.businessContext` 或 `payload.promptCenter.businessContext` 存在时，skill
必须优先参考这些字段：

- `target`: 当前测试目标。
- `intent`: `ui_test` / `regression` / `repair` / `api_test` 等意图。
- `business_flow` / `business_flow_text`: 当前业务主链。
- `risk_level` / `risk_hits`: 高风险动作，例如支付、删除、确认打印、开始打印、覆盖基线。
- `ui_context`: Figma、截图、页面知识和已筛选 UI 页面。
- `requirement_text`: 需求正文或用户补充说明。
- `source_summary`: 输入来源摘要。

## Skill 使用规则

1. `business_flow` 是强约束，不要脱离它扩大成整套回归、控件遍历或相邻功能，除非用户明确要求。
2. Figma/截图只用于校准当前业务链的入口、文案、状态和断言，不得混入无关页面。
3. 若业务主链与视觉资料冲突，保留需求点，并把冲突写入风险、待确认或人工用例。
4. 高风险动作必须进入待确认或人工用例，不允许自动执行。
5. 生成用例时必须覆盖业务主链节点；每个业务节点最多扩展 2 条自动化用例，异常/边界总量按需求复杂度控制。
6. 修复脚本时只能围绕失败点附近最小修改，不能改写业务主链。
7. 若主链节点缺少入口、数据或可见结果，输出待确认/人工项，不得跳过节点或用猜测路径替代。

## 输出要求

skill 仍然必须遵守自身 schema。Prompt Center 只影响判断依据，不改变输出结构。
