# HTTP 路由表

## 目录

- [1. 概述](#1-概述)
- [2. Task Server 路由](#2-task-server-路由)
  - [2.1 健康检查](#21-健康检查)
  - [2.2 认证](#22-认证)
  - [2.3 用例管理](#23-用例管理)
  - [2.4 YAML 操作](#24-yaml-操作)
  - [2.5 Job 管理](#25-job-管理)
  - [2.6 Runner 管理](#26-runner-管理)
  - [2.7 Sonic 操作](#27-sonic-操作)
  - [2.8 Agent 运行管理](#28-agent-运行管理)
  - [2.9 报告](#29-报告)
  - [2.10 修复草稿](#210-修复草稿)
  - [2.11 知识库](#211-知识库)
  - [2.12 飞书集成](#212-飞书集成)
  - [2.13 平台健康](#213-平台健康)
  - [2.14 AI 生成任务](#214-ai-生成任务)
  - [2.15 Figma 集成](#215-figma-集成)
  - [2.16 基线引用](#216-基线引用)
  - [2.17 其他](#217-其他)
- [3. AI Gateway 路由](#3-ai-gateway-路由)
  - [3.1 健康检查](#31-健康检查)
  - [3.2 模型管理](#32-模型管理)
  - [3.3 AI 能力](#33-ai-能力)
  - [3.4 Agent 操作](#34-agent-操作)
  - [3.5 YAML 校验](#35-yaml-校验)

---

## 1. 概述

- **Task Server** 监听 `:8091`，所有业务路由以 `/api/` 开头
- **AI Gateway** 监听 `:8090`，路由直接挂载在根路径
- **Nginx** 在 `:8088` 统一入口：`/api/*` → Task Server，`/ai-gateway/*` → AI Gateway
- 认证方式：Session Token（`Authorization: Bearer <token>`）或 Runner Token（`x-token` 头）
- 标注说明：
  - **认证** = `Session`（需要登录 Token）/ `Runner`（需要 Runner Token）/ `Sonic`（需要 Sonic 回调 Token）/ `无`（公开接口）

---

## 2. Task Server 路由

### 2.1 健康检查

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/health` | 内联 | 无 | 平台健康检查，返回存储路径状态、模型配置、依赖信息 |

### 2.2 认证

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| POST | `/api/auth/login` | `issue_session_token` | 无 | 管理员登录，返回 Session Token |
| POST | `/api/auth/logout` | 内联 | Session | 注销 Token（加入黑名单） |
| GET | `/api/auth/me` | `verify_session_token` | Session | 获取当前登录用户信息 |

### 2.3 用例管理

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/modules` | `yaml_service.list_modules` | 无 | 用例模块列表（`{模块名: [文件名]}`） |
| GET | `/api/yaml` | `yaml_service.read_yaml` | 无 | 读取 YAML 内容（`?module=X&file=Y`） |
| GET | `/api/yaml-stats` | `yaml_service.yaml_priority_stats` | 无 | YAML 优先级统计（`?module=X`） |
| POST | `/api/convert-cases-json` | `yaml_service.cases_to_midscene_yaml` | Session | 将用例 JSON 转换为 Midscene YAML |
| POST | `/api/generate-yaml` | `yaml_service.cases_to_midscene_yaml` | Session | 同上（别名） |
| GET | `/api/cases/summary` | 内联 | 无 | 获取生成汇总（`?case_set_id=X`） |
| GET | `/api/cases/mindmaps` | 内联 | 无 | 脑图列表 |
| GET | `/api/cases/mindmap` | 内联 | 无 | 下载脑图（`?case_set_id=X`） |
| POST | `/api/cases/mindmap` | `write_generation_mindmap` | Session | 刷新脑图 |
| POST | `/api/cases/mindmap-only-async` | `run_mindmap_only_job` | Session | 异步生成脑图 |
| GET | `/api/cases/ui-designs` | 内联 | 无 | 获取 UI 设计稿列表 |
| POST | `/api/cases/ui-designs` | `save_case_ui_design_files` | Session | 上传 UI 设计稿 |
| GET | `/api/cases/ui-design-image` | 内联 | 无 | 获取 UI 设计稿图片 |
| POST | `/api/cases/ui-design-exclusion` | `restore_excluded_figma_node` | Session | 恢复排除的 Figma 节点 |
| POST | `/api/cases/generate` | `call_dashscope_cases` | Session | AI 生成测试用例 |
| GET | `/api/cases/{case_set_id}` | 内联 | 无 | 获取用例集 JSON |
| POST | `/api/cases/{case_set_id}` | `normalize_cases_payload` | Session | 更新用例集 |

### 2.4 YAML 操作

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/file` | 内联 | 无 | 读取原始文件（`?module=X&file=Y`） |
| GET | `/api/file/history` | `list_file_versions` | 无 | 文件版本历史 |
| GET | `/api/file/version` | `read_file_version` | 无 | 读取指定版本 |
| POST | `/api/file/repair-latest-async` | `run_repair_job` | Session | 异步修复整个文件 |
| POST | `/api/file/repair-task-latest-async` | `run_repair_job` | Session | 异步修复单个 Task |
| GET | `/api/repair/result` | 内联 | 无 | 读取修复结果 |

### 2.5 Job 管理

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/jobs` | `job_service.load_jobs` | Session | Job 列表（含活跃+最近100条+后台任务） |
| POST | `/api/run-request` | `create_pending_job` | Session | 创建执行 Job |
| GET | `/api/jobs/{id}` | 内联 | Session | 获取 Job 详情 |
| POST | `/api/jobs/{id}/cancel` | 内联 | Session | 取消 Job |
| POST | `/api/jobs/{id}/retry` | 内联 | Session | 重试 Job |
| POST | `/api/jobs/{id}/review` | 内联 | Session | 更新 Job 评审信息 |
| POST | `/api/jobs/{id}/repair` | `run_repair_job` | Session | 对 Job 触发修复 |

### 2.6 Runner 管理

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/runners` | `runner_service.list_runners` | Session | Runner 列表与在线设备 |
| POST | `/api/runner/heartbeat` | `runner_service.save_runners` | Runner | Runner 心跳上报 |
| GET | `/api/runner/jobs/next` | 内联 | Runner | Runner 轮询下一个任务 |
| POST | `/api/runner/jobs/{id}/progress` | 内联 | Runner | Runner 上报执行进度 |
| POST | `/api/runner/jobs/{id}/report-ready` | 内联 | Runner | Runner 上报报告就绪 |
| POST | `/api/runner/jobs/{id}/result` | 内联 | Runner | Runner 上报执行结果 |

### 2.7 Sonic 操作

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/sonic/config` | `sonic_service.sonic_base_url` | 无 | Sonic 连接配置（不暴露 Token） |
| GET | `/api/sonic/runtime-env` | 内联 | Session | Sonic 运行时环境详情 |
| GET | `/api/sonic/projects` | `sonic_service.list_projects` | Session | Sonic 项目列表 |
| GET | `/api/sonic/suites` | `sonic_service.list_suites` | Session | Sonic 测试套列表（`?projectId=X`） |
| GET | `/api/sonic/cases` | `list_task_case_assets` | 无 | 用例资产与同步状态 |
| GET | `/api/sonic/status` | `sonic_live_case_status` | 无 | 用例同步状态汇总 |
| GET | `/api/sonic/case` | `find_task_case_asset` | 无 | 单条用例详情（`?case_id=X`） |
| GET | `/api/sonic/case-yaml` | `task_case_yaml` | 无 | 单条用例 YAML 内容 |
| GET | `/api/sonic/bridge-groovy` | 内联 | Session | 获取 Sonic Bridge Groovy 脚本 |
| GET | `/api/sonic/suite-results` | 内联 | 无 | Sonic 测试套结果列表 |
| POST | `/api/sonic/publish` | `publish_yaml` | Session | 同步单条用例到 Sonic |
| POST | `/api/sonic/publish-batch` | `publish_batch` | Session | 批量同步用例到 Sonic |
| POST | `/api/sonic/result` | 内联 | Session | Sonic 执行结果回传 |
| POST | `/api/sonic/report-ready` | `attach_sonic_background_report` | Session | Sonic 报告就绪通知 |
| POST | `/api/sonic/suite-complete` | `register_sonic_suite_completion` | Sonic | Sonic 测试套完成回调 |
| POST | `/api/sonic/suite-report` | `register_sonic_suite_completion` | Sonic | Sonic 测试套报告回调 |
| POST | `/api/sonic/custom-robot` | `register_sonic_suite_completion` | Sonic | Sonic 自定义机器人回调 |

### 2.8 Agent 运行管理

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/agent-runs` | `agent_service.list_agent_runs` | Session | Agent Run 列表（`?limit=N`） |
| GET | `/api/agent-runs/{id}` | `agent_service.get_agent_run` | Session | 获取 Agent Run 详情 |
| POST | `/api/agent-runs/start` | `agent_service.create_agent_run` | Session | 启动新 Agent Run |
| POST | `/api/agent-runs/{id}/confirm` | `agent_service.confirm_agent_step` | Session | 确认 Agent 待确认步骤 |
| POST | `/api/agent-runs/{id}/cancel` | `agent_service.cancel_agent_run` | Session | 取消 Agent Run |
| GET | `/api/agent-tools` | `agent_service.list_agent_tools` | Session | 工具白名单（分类列表） |

### 2.9 报告

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/reports` | `report_service.list_reports` | Session | 报告列表（`?limit=N&status=X`） |
| POST | `/api/reports/rebuild-index` | `report_service.rebuild_index` | Session | 重建报告索引 |
| GET | `/api/reports/cleanup` | `cleanup_midscene_reports` | Session | 报告清理预览（`?dry_run=1&days=N`） |
| POST | `/api/reports/cleanup` | `cleanup_midscene_reports` | Session | 执行报告清理 |
| POST | `/report` | 内联 | Runner | Runner 上传报告（完整） |
| POST | `/api/report/chunk` | 内联 | Runner | Runner 分片上传报告 |
| POST | `/api/report/chunk-finish` | 内联 | Runner | Runner 分片上传完成 |

### 2.10 修复草稿

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/repair-drafts` | `repair_service.load_repair_drafts` | Session | 修复草稿列表（`?job_id=X`） |
| POST | `/api/repair-drafts` | `repair_service.upsert_repair_draft` | Session | 创建/更新修复草稿 |
| POST | `/api/repair-drafts/reject` | 内联 | Session | 拒绝修复草稿 |
| POST | `/api/repair-drafts/apply` | 内联 | Session | 应用修复草稿（需 `confirmApply=true`） |

### 2.11 知识库

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/knowledge/apps` | `knowledge_service.list_knowledge_app_details` | 无 | 知识库应用列表 |
| GET | `/api/knowledge/pages` | `knowledge_service.list_knowledge_pages` | 无 | 页面列表（`?app_package=X&tier=Y`） |
| GET | `/api/knowledge/screenshot` | 内联 | 无 | 页面截图（`?app_package=X&page_id=Y`） |
| POST | `/api/knowledge/page` | `knowledge_service.save_knowledge_page` | Session | 保存知识库页面 |
| POST | `/api/knowledge/analyze` | `knowledge_service.analyze_knowledge_screenshot` | Session | AI 分析知识库截图 |

### 2.12 飞书集成

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/feishu/drafts` | `feishu_service.list_feishu_drafts` | Session | 飞书缺陷草稿列表 |
| POST | `/api/feishu/drafts` | `feishu_service.create_feishu_draft` | Session | 创建飞书缺陷草稿 |
| GET | `/api/feishu/drafts/{id}` | `feishu_service.get_feishu_draft` | Session | 获取飞书缺陷草稿详情 |

### 2.13 平台健康

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/platform/status` | `platform_service.get_platform_status` | 无 | 平台各子系统健康状态 |
| GET | `/api/preflight/dashboard` | `platform_preflight_dashboard` | Session | 预检面板（含 Sonic 扫描） |
| GET | `/api/task-meta` | `load_task_meta` | Session | 用例元信息 |
| GET | `/api/task-apps` | `sonic_notify_known_apps` | Session | 已注册应用列表 |

### 2.14 AI 生成任务

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| POST | `/api/ui/generate-yaml` | `generate_ui_yaml_from_request` | Session | 同步生成 YAML |
| POST | `/api/ui/generate-yaml-async` | `run_generate_job` | Session | 异步生成 YAML |
| POST | `/api/ui/regenerate-yaml-async` | `run_generate_job` | Session | 异步重新生成 YAML |
| GET | `/api/ui/generate-status` | 内联 | 无 | 生成任务状态查询（`?job_id=X`） |
| POST | `/api/ui/generate-jobs/{id}/retry` | 内联 | Session | 重试生成任务 |
| POST | `/api/ui/generate-jobs/{id}/cancel` | 内联 | Session | 取消生成任务 |
| POST | `/api/assets/upload` | `save_asset_files` | Session | 上传需求资产文件 |
| GET | `/api/assets/{case_set_id}` | 内联 | 无 | 获取资产元信息 |

### 2.15 Figma 集成

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| POST | `/api/figma/parse` | `parse_figma_design` | Session | 同步解析 Figma 设计 |
| POST | `/api/figma/parse-async` | `run_figma_parse_job` | Session | 异步解析 Figma 设计 |
| POST | `/api/figma/import` | `import_figma_design` | Session | 导入 Figma 设计 |

### 2.16 基线引用

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/api/baseline/page-refs` | 内联 | 无 | 获取基线页面引用（`?module=X&file=Y`） |
| POST | `/api/baseline/page-refs` | `set_baseline_ref_page_ids` | Session | 设置基线页面引用 |

### 2.17 其他

| 方法 | 路径 | Service 函数 | 认证 | 说明 |
|------|------|-------------|------|------|
| GET | `/` | 内联 | 无 | 返回前端页面 `task-manager.html` |
| GET | `/assets/*` | 内联 | 无 | 静态资源（品牌图片等，缓存1小时） |

---

## 3. AI Gateway 路由

### 3.1 健康检查

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/health` | AI Gateway 健康检查，返回当前 Provider 与模型信息 | 无 |

### 3.2 模型管理

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| GET | `/ai/providers` | 获取模型 Provider 列表；非千问 OpenAI 兼容通道读取上游 `/models`，并返回目录来源、缓存和降级状态 | 无 |
| POST | `/ai/providers/test` | 测试指定 Provider 连通性（`{providerId}`） | 无 |
| GET | `/ai/model-router` | 获取 Action → Provider 路由策略 | 无 |
| POST | `/ai/model-router` | 更新 Action → Provider 路由策略 | 无 |

### 3.3 AI 能力

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/ai/generate-yaml` | 生成 Midscene YAML（`{appName, platform, testCase}`） | 无 |
| POST | `/ai/generate-case` | 生成测试用例（`{moduleName, requirement}`） | 无 |
| POST | `/ai/analyze-failure` | 分析失败原因（`{taskName, yaml, log, screenshotDesc}`） | 无 |
| POST | `/ai/optimize-yaml` | 优化/修复 YAML（`{yaml, failureAnalysis, requirement}`） | 无 |
| POST | `/ai/generate-bug` | 生成缺陷草稿（`{taskName, envInfo, failureAnalysis}`） | 无 |
| POST | `/ai/chat` | AI 对话（`{message, context}`） | 无 |

### 3.4 Agent 操作

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/agent/run` | 启动 Agent 执行（含策略评估、状态机推进） | 无 |
| GET | `/agent/runs` | Agent Run 列表（`?limit=N`） | 无 |
| GET | `/agent/runs/:runId` | 获取 Agent Run 详情 | 无 |
| POST | `/agent/runs/:runId/confirm` | 确认 Agent 待确认步骤（`{decision: approve/reject}`） | 无 |
| POST | `/agent/runs/:runId/cancel` | 取消 Agent Run | 无 |

### 3.5 YAML 校验

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/ai/validate-yaml` | 校验 Midscene YAML 结构（`{yaml}`） | 无 |
