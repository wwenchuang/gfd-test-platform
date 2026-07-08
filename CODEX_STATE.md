# CODEX_STATE.md

本文件记录当前 Codex 交接状态，目的是减少长对话上下文依赖。每次完成一轮重要修改后更新本文件。

## 当前项目状态

平台已有完整的 Agent 生成、YAML 校验、Runner 执行、Sonic 同步、报告和失败修复链路。当前主要目标不是重构架构，而是提高 AI 生成 Midscene YAML 的可执行性、速度和生产稳定性。

## 当前重点问题

1. Agent 生成 YAML 时偶尔会把“入口展示 / 布局 / 同级校验”误生成成“点击入口进入第三方流程”。
2. 生成 YAML 有时缺少目标模块路径，例如没有先进入文档打印页就校验百度网盘入口。
3. 过泛的 `aiTap` / `aiWaitFor` / `aiAssert` 会导致 Runner 反复重规划或定位失败。
4. 设备 / ADB / AI 模型服务异常需要和 YAML 脚本问题分开归因。
5. 旧任务和新任务状态展示、重跑、修复范围需要持续保持透明。
6. Windows Runner 需要作为服务稳定运行，并上报能力、设备、App 版本和 last_seen。

## 已有能力

- `yaml_executable_scorer.py`：YAML 可执行性评分。
- `yaml_static_validator.py`：YAML 静态校验。
- `yaml_baseline_cache.py`：基线缓存。
- `yaml_pattern_service.py` / `yaml_template_matcher.py`：基线写法和模板匹配。
- Agent smoke gate：首批冒烟控制。
- `/api/cases/rerun-smoke`：人工修改冒烟后重跑入口。
- Runner `yaml_dry_run` 能力：Windows Runner 已支持上报。
- Windows Runner 服务脚本：使用 NSSM 安装为服务。

## 最近完成的关键修复

### 2026-07-08 Agent YAML 可执行性收敛

已修改：

- `task_server/services/yaml_executable_scorer.py`
- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `server-tasks/AI_Agent_草稿/基础打印新增百度网盘入口-可执行冒烟.yaml`

修复点：

- 入口展示 / 位置 / 同级类百度网盘用例不能点击百度网盘或等待第三方页面。
- 文档打印 / 扫描复印 / 照片打印 / 证件照类百度网盘用例必须先进入正确业务页。
- 埋点 / 统计 / eleTitle 类不应自动下发 Runner。
- 生成 YAML 默认不使用最近任务多次滑动清理。
- 普通入口 / 文案 / 布局等待压缩到 12-15 秒。
- 上传 / 导入 / 模型生成 / 切片等长任务才允许 120-180 秒。
- Agent 校验阶段会把“aiTap 写成检查/断言”的错误修成 `aiWaitFor` / `aiAssert`。

已验证：

```bash
python3 tests/backend_static_checks.py
python3 -m py_compile task_server/services/yaml_executable_scorer.py task_server/services/yaml_service.py task_server/services/agent_service.py tests/backend_static_checks.py
git diff --check
```

参考 YAML 校验结果：

```text
executionLevel=executable
score=100
dry_ok=True
```

## 当前未提交/需注意改动

工作区可能存在用户或历史改动，不要默认回滚：

- `server-tasks-all/3D打印基线/十二生肖印章打印.yaml`
- `server-tasks/3D打印基线/十二生肖印章打印.yaml`
- `task_server/services/sonic_service.py`
- `deploy/install-windows-runner-service.local.ps1`

提交时不要直接 `git add .`，按任务文件精确添加。

## 下一步优先级

1. 用真实需求 + Figma + 现有基线验证 Agent 新生成 YAML 是否贴合需求。
2. 对失败 Runner 报告继续做归因分类：YAML 问题、页面状态问题、设备问题、AI 服务问题。
3. 优化 Agent 生成结果展示：完整用例、可执行 YAML、需确认项、人工项、失败原因要分层清楚。
4. 持续沉淀成功执行的 YAML 片段到基线缓存，不把失败样例当成功模板。

## 常用部署流程

本地提交：

```bash
git status --short
git add <本次任务相关文件>
git commit -m "<提交说明>"
git pull --rebase
git push
```

服务端部署：

```bash
cd /opt/midscene-task-platform-src
git pull --ff-only
bash deploy/install-server.sh
systemctl restart midscene-task
curl http://127.0.0.1:8091/api/health
curl http://127.0.0.1:8088/api/health
```

## 新对话推荐开头

```text
请先阅读 AGENTS.md 和 CODEX_STATE.md，然后只处理本次任务。

本次任务：
<写一个明确的小任务>

要求：
1. 先阅读相关文件并列修改计划。
2. 不要重构 router.py。
3. 不要新增执行模式。
4. 不要修改历史 YAML。
5. 不要改本任务无关文件。
6. 修改后跑相关检查，并更新 CODEX_STATE.md。
```

