# AGENTS.md

本文件是 Codex 进入本仓库后的固定工作规则。每次开始新任务时，先阅读本文件和 `CODEX_STATE.md`，再阅读本次任务相关代码。

## 项目定位

Midscene Task Platform 是一个 AI 自动化测试平台，核心链路是：

需求 / Figma / 截图 / 基线库
→ AI 分析与规划
→ Midscene YAML 生成
→ YAML 静态校验 / 可执行性评分 / dry-run
→ 冒烟执行
→ Runner / Sonic
→ 报告 / 失败归因 / 修复草稿 / 重跑。

## 核心文件

- `task_server/services/agent_service.py`
- `task_server/services/yaml_service.py`
- `task_server/services/ai_skill_service.py`
- `task_server/services/case_service.py`
- `task_server/services/yaml_baseline_cache.py`
- `task_server/services/yaml_executable_scorer.py`
- `task_server/services/yaml_static_validator.py`
- `task_server/services/sonic_service.py`
- `ai-gateway/server.js`
- `ai-gateway/config/model-router.json`
- `ai_skills/prompts/*.v1.md`
- `windows-midscene-runner.py`

## 已确定生产策略

- Midscene 版本按 1.7.10 兼容处理。
- 小需求生成 3 条，中需求生成 5 条，大需求最多 8 条。
- 首批冒烟最多 3 条。
- 冒烟通过率大于等于 50% 才继续执行剩余用例。
- 冒烟未达标时暂停，允许人工修改冒烟 YAML 后重跑。
- 生成 YAML 必须优先参考 Top3 相似成功基线写法。
- 不要把测试设计草稿、人工验证项、埋点项直接下发 Runner。
- 设备 / ADB / AI 服务异常要和 YAML 脚本问题分开归因。
- 普通入口、文案、布局、同级展示校验应使用短等待，长等待只给上传、导入、模型生成、切片等长任务。

## AI 参与边界

AI 应参与：

- 需求理解和业务主链抽取。
- 页面状态、入口和关键控件识别。
- 相似成功基线重排。
- 用例计划生成。
- Midscene YAML 仿写。
- dry-run / Runner 失败后的定向修复建议。

平台必须负责：

- 数量上限。
- Midscene action 白名单。
- YAML 静态校验。
- 可执行性评分。
- 冒烟门禁。
- 继续执行阈值。
- Runner 能力检测。
- 设备 / 环境 / AI 服务错误分类。

## 禁止事项

- 不要大规模重构 `router.py`。
- 不要删除 `dag_safe` / `parallel_dag`。
- 不要新增 `runner_fast` 或其他不存在的执行模式。
- 不要新增 Midscene 1.7.10 不支持的 YAML action 或字段。
- 不要生成 `waitAfter.fast` / `fastProbe` 等非官方字段。
- 不要批量修改历史基线 YAML，除非用户明确要求。
- 不要用全局基线 profile 替代 Top3 相似成功基线。
- 不要让 AI 绕过平台门禁直接决定是否继续执行 remaining。
- 不要把设备离线、网络超时、AI 服务超时误判成 YAML 逻辑错误。
- 不要用固定坐标或最近任务多次滑动作为默认启动守卫。

## 必跑检查

按改动范围选择检查，涉及生成 / Agent / Runner 主链路时至少跑：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py
python3 tests/backend_static_checks.py
git diff --check
```

涉及前端时增加：

```bash
python3 tests/frontend_static_checks.py
```

涉及 AI Gateway 时增加：

```bash
python3 tests/ai_gateway_static_checks.py
```

涉及 Runner 时增加对应 runner 脚本语法检查和服务启动说明。

## 标准工作方式

每个 Codex 对话只处理一个明确任务。

推荐流程：

1. 先阅读 `AGENTS.md` 和 `CODEX_STATE.md`。
2. 再阅读本次任务相关代码。
3. 给出修改计划。
4. 执行小范围修改。
5. 跑检查。
6. 更新 `CODEX_STATE.md`。
7. 最后给出提交文件和部署命令。

如果工作区已有用户改动，必须保留，不能回滚。

