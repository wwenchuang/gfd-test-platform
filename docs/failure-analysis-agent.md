# 测试失败调查 Agent

报告详情里的「AI 分析失败」读取当前报告，由现有千问模型动态选择取证工具。无需重新配置模型 Key，沿用服务端 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`DASHSCOPE_MODEL`，支持原有平台配置的密钥回退。Key 不进入浏览器。

## 同事怎么用

1. 主平台「我的测试」打开一次 UI 执行报告或执行证据；或者在 API 工作台打开执行详情／报告详情。
2. 点击「AI 分析失败」。运行中可看到实际取证动作，或停止调查。
3. 阅读失败概况、带证据引用的事实摘要、候选原因、下一步建议和缺少的证据。点击引用可展开脱敏原始数据并回到原执行。
4. 刷新页面可读取本人的已有调查；网络异常不会清空原报告。重新分析会产生一次新模型调用，失败提交的重试沿用请求编号。

调查完成表示 Agent 已输出调查结论，不代表用例通过或故障已修复。模型摘要验证了引用来自实际工具，语义正确性仍需对照原始数据；程序不会把根因猜测当成已修复结果。

## 自主调查范围

- `get_failed_steps`：读取当前 API 执行／UI 批次的失败项和已记录步骤，最多提供25个失败子记录。
- `get_request_trace`：读取绑定运行内某个子记录的请求、响应、断言或 Runner 日志；不存在的网络轨迹明确缺失。
- `compare_last_pass`：API 以相同项目、用例版本、接口、来源修订、环境修订，选择在本次开始前已完成的最近成功。UI 尚无可证明相同环境的字段，明确不可比。

没有固定工具顺序。模型可选择代表性失败、追加调查、查询上次成功，或者说明证据已足够／仍有缺口并停止。各工具的账号和运行由服务端绑定，每次调用及结果读取都重新核对权限。

首版不执行测试、不改用例、不发送通知、不提单，不把同一分钟的其他 API 运行拼接成 UI 的上游请求。要进一步定位 UI 上游接口，Runner 需要按同一批次、用例和尝试编号保存网络请求关联；该证据未采集时，界面会明确说明。

## 运行与可靠性

- HTTP：POST `/api/failure-analysis/tasks` `{source,run_id,request_id}`；GET 同路径按 `id` 或 `source/run_id` 查询；POST `/api/failure-analysis/cancel` `{id}`。
- 每人同一时间一个调查；共享模型并发限额，独立有界队列复用 `PersistedJobDispatcher`。
- 默认6轮、12次工具、180秒；单次模型网络超时最多45秒。排队超过60秒或进程重启会明确结束，不留假运行。
- 模型预算18000累计 tokens，单次输出最多2000。下一轮输入预留是字符估算，供应商返回的真实 usage 单独累计；缺失 usage 显式标记估算，不代表精确费用上限。
- 原始报告保留。取消后的晚到结果不能覆盖取消终态；中断、超时保留已有证据。
- 私有任务数据库 `failure-analysis.sqlite3` 位于身份库同目录，文件0600；部署账号须与身份库所属服务账号一致。
- 同运行内已知敏感值在裁剪前统一发现和遮蔽。脱敏上下文超过500条／8MiB等读取边界时停止自由文本输出，并提示缺口。

## 检查与发布

```bash
python3 -m pytest -q tests/test_failure_analysis_*.py
node --test tests/failure_analysis_check.js tests/failure_analysis_browser.test.js tests/ui_batch_report_check.js tests/ui_simple_entry_check.js
python3 tests/backend_static_checks.py
python3 tests/frontend_static_checks.py
git diff --check
```

API 前端构建使用 `npm --prefix api-testing-ui run build`；生成的 `api-test` 资源与后端代码一起发布。主平台同时发布 `task-manager.html`、`js`、`css`。使用现有 `deploy/package-server.sh` 和服务器安装流程，部署需要正常的服务重启窗口；此功能不要求修改 Runner 或手机设置。

先拉取包含本功能的版本，再打包：

```bash
git fetch origin
bash deploy/package-server.sh
```

服务器只读真实验收（用现有平台服务账号，在运行目录 `/opt/midscene-task-platform` 执行；如自定义 `APP_DIR` / `VENV_DIR`，相应调整路径）：

```bash
.venv/bin/python tools/failure_agent_acceptance.py --source api --run-id <已有失败执行编号> --username <有权查看该报告的平台账号> --output <新的私有JSON文件路径>
```

`.venv/bin/python tools/failure_agent_acceptance.py --protocol-scenarios --username <平台账号> --output <新路径>` 使用明确标记的合成证据测试真实千问是否会因502和200响应差异选择不同调查。该模式不属于真实业务执行验收。工具结果、模型判断、真实测试执行和人工正确率评估须分别记录。

## 2026-11-18 现场验收状态

已通过本地真实 HTTP / 队列 / ORM 集成、模型协议夹具、组件及浏览器交互检查。服务器使用既有服务账号读取真实历史失败报告并调用已配置千问，第一轮返回 `model_http_401`，尚无工具调用或调查结论。平台直连和网关凭证经仅比较相等性核实一致；未输出凭证、未重启共享服务、未发布本次代码。请先在服务器核实凭证有效性及地域匹配，再运行上面的验收命令。不要把 401 当成测试用例失败，也不要反复重试替代配置修复。

页面已分别说明鉴权失败、无模型权限和限流；失败时保存原报告与此前已有的证据。真实模型正确率、生产页面验收以及定时巡检不属于已完成项。

### 2026-11-18 接口分析可用与模型鉴权失败的区别

使用现有 Gateway 相同的 SDK、provider 地址和凭证发送最小请求，同样返回 HTTP 401、`invalid_api_key`。运行进程中的 Qwen Key、dotenv 解析结果与 Python 选中 Key 在服务器内比较相等；没有输出 Key。公开健康接口与堡垒机进程 PID 一致。此结果将当前故障定位到所配置凭证与上游鉴权，尚不能区分 Key 失效、撤销或地域错误；降级型号不会修复该类鉴权错误。

- 工作台“已有接口分析与证据”重新检查数据库中保存的执行证据，运行17项固定检查；不调用千问。
- “生成基础正向用例”按接口定义构造；“接口合同与风险”只填入提示文本。
- AI 助手“生成测试用例”和报告 AI 失败分析才会发起模型请求；查看已有结果不代表本次请求成功。

现场页面显示的部分完成及3个草稿与11月10日最近保存的AI任务一致。点击重新生成时平台会话过期，新的网页请求尚未验收。不能仅凭用户所说的“接口分析”确定具体按钮，也不能把固定检查成功解释为当前模型调用成功。

按用户要求将 `qwen3.8-max` 降一档，当前代码默认统一回到 `qwen3.7-plus`。原有 `qwen3.8-flash` 轻量阶段和旧报告中的实际模型记录保留。平台文本／视觉、网关 provider、原 Max 视频阶段和细节感知能力检查同步更新；Runner 仍使用 `MIDSCENE_MODEL_FAMILY=qwen3`。官方能力依据：[Qwen3.7 Plus](https://help.aliyun.com/zh/model-studio/qwen3-7-plus)、[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。

同一服务器、同一凭证、同一历史失败报告改用 `qwen3.7-plus` 后再次返回 `model_http_401`，仍没有工具调用。验收记录为 `/var/tmp/failure-analysis-real-qwen37-20261118-r1.json`。此次隔离命令只覆盖该进程的型号，未修改线上进程或重启服务；旧服务器显式模型配置需要部署时一并迁移。

## 旧服务器模型配置迁移

已有 `/opt/midscene.env` 与 `/opt/ai-gateway/config/providers.json` 会被正常发布保留，不能只更新代码默认值。使用本次发布包内的专用迁移工具，先核对变更，再应用：

```bash
# 在解压后的发布包根目录执行。输出只含模型字段，不显示 Key。
sudo python3 deploy/configure_qwen37.py --env-file /opt/midscene.env --providers-file /opt/ai-gateway/config/providers.json
sudo python3 deploy/configure_qwen37.py --env-file /opt/midscene.env --providers-file /opt/ai-gateway/config/providers.json --apply
# 在正常发布窗口部署代码并重启，使环境与新视频阶段规则一起生效。
sudo bash deploy/install-server.sh
```

迁移只替换指定模型字段的精确旧值 `qwen3.8-max`，保留其他自定义型号和 Key。工具在校验全部输入后生成私有备份、保留文件原权限与所有者；遇到格式错误或缺失文件则停止，重复执行不重复变更。此工具不自动调用模型、不自动重启服务；HTTP 401 仍需单独修复凭证或地域匹配。

最终本地验收：Agent与迁移87项、API Vue组件49项、主平台Node/Chromium249项、Gateway221项、三组静态检查通过；视频协议及本地IPC分组回归通过。旧week9受控浏览器故障演练依赖固定Chromium，本机未安装，未计为通过。实际模型推理、真机执行及部署后网页验收仍须单独记录。
