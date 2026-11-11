# 第 10 周 · 用 MCP 调用平台目录冒烟

在 QA 主机上，本目录的 MCP 客户端通过标准输入输出启动独立服务进程。服务把三个工具转交给平台的鉴权接口。套件检查真实网页上已发布用例的显示数量，平台保存运行状态、截图和 trace。MCP 进程使用调用者的个人会话，执行记录遵循平台账号和应用范围。

## 隔离安装和协议检查

在 QA 部署目录使用 Node.js 20+ 与 npm。依赖安装在本目录。

```sh
cd scripts/week10_mcp
npm ci --ignore-scripts --no-fund
npm test
```

本地测试使用官方 SDK 的 Client 和 StdioClientTransport 完成握手，校验闭合工具参数、身份、幂等和证据引用。HTTP 平台和模型回包使用测试夹具，真实业务验收使用下面的宿主命令。

## 用个人会话运行

从仓库根目录执行。下面的示例通过终端隐藏输入取得当前平台个人会话，读取已加载的服务端百炼配置，将两个凭证通过匿名管道传递给宿主。请为本次请求选择一个 UUID，在故障重试时输入相同值。脚本默认请求为“请在平台测试环境运行已发布目录冒烟套件，查询本次结果和执行证据。”

```sh
umask 077
python3 -c '
import getpass, json, sys, uuid
from task_server.config import dashscope_api_key, dashscope_base_url, dashscope_text_model
token = getpass.getpass("平台个人会话: ")
with open("/dev/tty") as tty:
    print("本次请求 UUID（留空生成）: ", end="", file=sys.stderr, flush=True)
    request_id = tty.readline().strip() or str(uuid.uuid4())
print("request_id =", request_id, file=sys.stderr)
print(json.dumps(dict(session_token=token, request_id=request_id, model_key=dashscope_api_key(),
                      base_url=dashscope_base_url(), model=dashscope_text_model())))
' | node scripts/week10_mcp/host.mjs --qwen > week10-receipt.json
```

受保护输入也可由现有会话工具提供。输入字段为 `session_token`、`model_key`、可选的 `request_id`、自然语言 `request`、服务端 `model` 与 `base_url`。区域支持百炼国内、国际、美国的官方兼容接口，默认国内端点和 `qwen3.8-flash`。每次模型验收最多三次调用，每次最多 512 个输出 token。宿主发给模型的数据包括自然语言请求、公开工具描述、操作编号和状态。模型回包中实际选择的工具及参数须与该轮目标匹配后才执行。

`node scripts/week10_mcp/host.mjs --deterministic` 接收相同的管道输入，仅需 `session_token`。此模式执行固定的默认中文请求，回执标记 `deterministic` 并记录零次模型调用。若要接入其他标准 MCP 宿主，将启动程序指定为 `node /绝对路径/scripts/week10_mcp/server.mjs`，由宿主的私密环境注入 `PLATFORM_SESSION_TOKEN`。标准输出专用于 MCP 协议。

## 验收证据

宿主发现 `list_suites`、`run_suite`、`get_run`，先验证参数校验拒绝 production，再按已发现的 `published-catalog / platform` 执行。相同请求 ID 的第二次提交须返回同一运行 ID。随后按该 ID 查询、轮询到终态。通过时须有非负整数的目录数量和同一网页执行记录的截图、trace 鉴权引用。

退出码 0 表示该次业务执行通过。完整 JSON 包含请求编号、发现的工具、重复提交结果、运行编号、状态变化、目录数量、证据相对地址和模型用量。模型模式还记录请求模型、供应方回传模型、调用回执 ID 和耗时。网页预览或鉴权下载可用于复核证据文件。

工作台读取身份库同目录的 `mcp-acceptance.json`。由部署者核查真实回执后，将其中的 `publication` 对象用权限 0600 的临时文件在目标目录原子替换。平台对照执行归属和应用范围再展示。保留原完整回执以便复核。

普通同事可直接在测试工作台的“工具执行”选择“已发布目录”或“反馈分类”，开始检查，刷新后从“我的运行记录”找回结果并下载 JUnit。反馈分类是 Python 执行的真实只读 API 契约校验。标准 MCP 客户端也可用同一个 `run_suite` 提交 `feedback-categories / platform`；模型示例继续固定运行已发布目录。故意失败演练仅用于网页验收，外部 MCP 工具不提供该选项。
