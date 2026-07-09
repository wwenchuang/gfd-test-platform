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

### 2026-07-09 Runner dry-run 平台根节点修复

本轮定位线上最新任务：

- `agent-1783508474367-2fa0485c`
- 目标：基础打印新增百度网盘入口
- 状态：`FAILED`
- 失败停在：`RUN_SONIC`

问题定位：

- Agent 生成、平台本地 dry-run 和静态校验均通过了部分 YAML。
- Windows Runner 真实 dry-run 全部拒绝选中的 YAML，错误为：`缺少 android 或 ios 平台根节点`。
- 直接原因是服务端生成/拆分链路会产出或保留根级 `tasks:`；平台本地校验允许 `root.tasks`，但 Runner 真实 dry-run 要求顶层必须是 `android.tasks` 或 `ios.tasks`。

已修改：

- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 新增 `ensure_midscene_platform_root`，在 Runner 相关链路把根级 `tasks:` 包装为 `android.tasks`。
- `cases_to_midscene_yaml` / `cases_to_separate_midscene_yamls` 直接生成 `android.tasks`，不再生成 `android: null + root tasks`。
- Agent 确认生成 YAML、拆分多任务文件、确认已有生成文件时都会写入平台根结构。
- Runner 下发前的执行修复会兜底改写旧生成 YAML，并记录到 `yamlExecutionRepairs`。
- AI 修复草稿和修复重跑写入前也会规范化平台根，避免修复链路再次触发 Runner dry-run 结构失败。
- 后端静态检查覆盖根级 `tasks` 包装、生成器输出和 Agent 拆分文件输出。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
```

### 2026-07-08 Skills 链路 app_package 参数漏提交修复

问题定位：

- 线上 Agent 在 `GENERATE_YAML` 阶段进入“兼容生成”。
- 日志显示：`build_cases_payload_from_skills() got an unexpected keyword argument 'app_package'`。
- 直接原因是 `yaml_service.py` 已经向 Skills 用例生成链路传入 `app_package`，但 `ai_skill_service.py` 的签名修复之前未随上次提交一起提交/部署，导致线上调用方和被调用方版本不一致。

已修改：

- `task_server/services/ai_skill_service.py`
- `task_server/services/case_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- `build_cases_payload_from_skills(..., app_package="", app_name="")` 接收 App 上下文。
- `call_skill_automation_filter` 和本地 fallback 自动化筛选继续透传 App 上下文，避免 fallback 步骤写错 App 品牌/首页。
- `case_service.py` 的委托入口同步接收并转发 `app_package` / `app_name`，避免间接调用再触发同类错误。
- 后端静态检查增加签名和透传覆盖，防止后续调用方/被调方参数再次不一致。

### 2026-07-08 上传截图作为 AI 视觉软参考可追踪

本轮修改目标：

- 用户上传的截图/需求补充截图都要作为 AI 判断参考进入视觉校准。
- 上传图不是硬门禁：不能因为没有完全引用截图就阻断生成或 Runner 执行。
- 需要在 Agent 产物里透明展示：识别到哪些上传图、是否要求进入 AI 判断、视觉判断是否完成、和 Figma/需求冲突时如何处理。

已修改：

- `task_server/services/agent_service.py`
- `js/agent-workbench.js`
- `tests/backend_static_checks.py`
- `tests/frontend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 新增 `visualReferenceReport` artifact，记录上传截图、Figma 页面/UI 图、参考来源、软参考规则、冲突处理策略。
- `visualReferenceReport` 明确 `hardGate=false`，同时标记 `aiJudgementRequired` / `sentToAiForJudgement` / `aiJudgementStatus`，用于说明上传图是否参与 AI 视觉判断。
- 质量报告 `qualityReport` 增加“上传截图参考”层和 `uploadedImageCount`，不改变 Runner 准入。
- Agent 工作台质量检查和输入来源详情新增“图片参考”卡片，展示上传截图数量、Figma 图数量、AI 判断状态和硬门禁状态。
- 后端静态检查覆盖：上传截图必须作为 AI 视觉软参考暴露，且 YAML 视觉校准输入仍包含 `figma_images + uploaded_image_assets`。
- 前端静态检查覆盖：UI 必须展示图片参考、上传截图、AI 判断和硬门禁状态。
- 顺手修正两条旧静态检查的 YAML 标量格式依赖：不再要求 `aiAssert` / `aiWaitFor` 必须带双引号，只校验语义和动作类型。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py tests/frontend_static_checks.py
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
```

### 2026-07-08 线上最新 Agent 失败归因与重跑稳定性修复

本轮定位线上最新任务：

- `agent-1783500104352-7eb42766`
- 目标：基础打印新增百度网盘入口
- 状态：`FAILED`
- 失败停在：`RERUN`

问题定位：

- 首次 Runner 执行失败不是设备离线，而是生成 YAML 顶层出现 `android: null`，Runner/修复流程注入 `android.deviceId` 后形成重复顶层 `android`，Midscene 解析报错。
- 自动修复后重跑又把 2 条修复 YAML 同时下发到同一台固定设备 `ecbfd645`；两条任务都包含 `am force-stop com.xbxxhz.box`，并发执行互相清理 App 状态，导致其中一条等待扫描复印页时实际停在手机桌面。

已修改：

- `task_server/services/yaml_service.py`
- `task_server/services/agent_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 新增空平台头清理：生成/修复进入运行时守卫前会移除顶层 `android: null` / `ios: null`。
- YAML 强校验遇到 `android: null` / `ios: null` 与 root `tasks` 共存时直接判不可执行，避免再次进入 Runner 后被设备注入放大成重复平台声明。
- Agent 修复重跑写临时 YAML 前也清理空平台头。
- Agent 安全重跑在固定 runner/device 场景下串行创建并等待 job，避免同一设备上的多个重跑任务互相 `force-stop`。
- 后端静态检查增加空平台头拦截/规范化、同设备重跑串行源码覆盖。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 - <<'PY'
from task_server.services.yaml_service import validate_midscene_yaml_executability, remove_empty_midscene_platform_roots
from task_server.services import agent_service
bad = "android: null\ntasks:\n  - name: demo\n    flow:\n      - aiTap: 首页搜索框\n"
print(validate_midscene_yaml_executability(bad).get("ok"))
fixed = remove_empty_midscene_platform_roots(bad)
print(validate_midscene_yaml_executability(fixed).get("ok"))
print(agent_service._agent_rerun_requires_serial_device({"runnerId":"win-runner-01","deviceId":"ecbfd645","deviceStrategy":"fixed"}))
PY
git diff --check
```

未通过项：

```bash
python3 tests/backend_static_checks.py
```

当前失败在既有的 `check_generated_yaml_uses_single_final_assertion`：实际生成 `aiAssert: 图片建模上传入口、提示文案或空态区域可见`，静态检查精确匹配带双引号的字符串，属于无关格式断言，不是本轮 `android: null` 或同设备并发重跑改动引入。

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

### 2026-07-08 生成 YAML 可执行性增强

本轮已修改：

- `task_server/services/yaml_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

修复点：

- 自然语言步骤转换时，`检查 / 验证 / 确认 / 可见 / 存在 / 展示 / 显示 / 布局 / 同级` 等页面状态检查优先生成 `aiWaitFor`，不再因为包含“按钮 / 选择”等词误生成 `aiTap`。
- 明确点击步骤会补轻量后置稳定等待；百度网盘入口点击后等待授权页、登录页、文件选择页、空状态页或提示页等跳转后信号。
- Agent 本地可执行修复在 AI 重写超时时，对“文档打印页百度网盘入口可见性”类误点用例补文档打印路径，并把修正后的可见性等待补成同语义 `aiAssert`，避免缺路径/缺终态断言导致继续进 draft。
- 后端静态检查增加展示/存在类步骤不误点、百度网盘第三方入口点击后等待跳转后信号的覆盖。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
```

### 2026-07-08 Agent 生成 App 品牌上下文修复

本轮已修改：

- `task_server/services/ai_skill_service.py`
- `task_server/services/yaml_service.py`
- `tests/backend_static_checks.py`
- `CODEX_STATE.md`

问题定位：

- 需求文档明确是“小白学习打印app”的基础打印百度网盘入口需求。
- 本地 Agent 草稿 `server-tasks/AI_Agent_草稿/基础打印新增百度网盘入口-可执行冒烟.yaml` 中出现了“小白扫描王首页已加载完成”。
- 直接原因是 `ai_skill_service.py` 的本地 fallback 步骤缺少 App 上下文，只能硬编码首页等待文案；当 AI 超时/失败走本地兜底时，会把跨 App 品牌词带进当前需求。
- 语义检查里也曾把“小白扫描王”当成 `com.xbxxhz.box` 的识别词，这会让错误品牌被误认为当前 App 语境。

修复点：

- `build_cases_payload_from_skills` / `call_skill_automation_filter` / fallback 自动化筛选透传 `app_package` / `app_name`。
- fallback 首页等待统一由当前 App 上下文生成；`com.xbxxhz.box` 使用小白学习打印入口信号，`com.kfb.model` 使用 3D/AI 建模入口信号，未知 App 使用“当前 App”中性描述。
- 非百度网盘 fallback 也使用同一 App 上下文，避免只修百度网盘路径。
- dry-run 语义检查改为按包名识别当前 App，并使用品牌冲突规则拦截跨 App 文案，例如 `com.xbxxhz.box` 不能出现“小白扫描王 / 智小白3D”，`com.kfb.model` 不能出现“小白学习打印 / 小白扫描王”。
- 移除把“小白扫描王”作为小白学习打印 App 识别词的逻辑。
- 后端静态检查覆盖 fallback steps、fallback YAML、Learning Print 被扫描王污染、3D 包被小白学习污染，以及非百度需求 fallback 的 App 上下文。

已验证：

```bash
python3 -m py_compile task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py task_server/services/ai_skill_service.py tests/backend_static_checks.py
python3 tests/backend_static_checks.py
git diff --check
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

本次部署快速命令：

现在服务端部署执行：

```bash
cd /opt/midscene-task-platform-src
git pull --ff-only
bash deploy/install-server.sh
systemctl restart midscene-task
curl http://127.0.0.1:8091/api/health
curl http://127.0.0.1:8088/api/health
```

本地剩余未提交内容可以之后再看：

```bash
git status
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
