# Apifox 资产发现与中文名称设计

## 背景

API 资产页当前把 Apifox 来源配置成一组底层字段：

- 来源名称由用户手工填写；
- Project ID、Branch ID 和 Environment ID 由用户手工查找并填写；
- 项目切换器固定显示“来源名称 · Project ID”；
- 同步得到的 OpenAPI `info.title` 只用于 revision/asset 标题，没有回写来源展示元数据。

这造成同一页面里有的项目显示中文名称、有的显示 ID，新建来源还要求用户离开平台查找
多个 ID。2026-07-23 的工作区设计曾明确不调用未公开的项目发现接口，因此当时的手填
方案是合理的。Apifox CLI 2.2.6 起已正式提供 `project list/get`、`branch list` 和
`environment list` 等只读命令，并输出结构化 JSON；该前提已经变化。

本设计仅替换旧设计中“项目必须由用户提供 Project ID”的限制，不改变既有 OpenAPI
导出、不可变 revision、模块范围、AI 用例生成、MeterSphere 执行或 UI Agent 链路。

## 目标

- 新增 Apifox 来源时，默认只要求输入一次访问令牌。
- 通过 Apifox 官方 CLI 读取可访问项目，并以远端项目名称作为主展示文案。
- 选中项目后读取分支和环境，按名称选择，平台自动保存对应稳定 ID。
- Project ID、Branch ID、Environment ID 只在技术详情或手动连接兜底中展示。
- 已有来源在刷新或同步后逐步补齐远端项目、分支和环境名称。
- Token 不进入命令参数、日志、浏览器响应、错误信息或全局 CLI 登录态。
- CLI 不可用时保留现有手动配置和 OpenAPI 同步能力。

## 非目标

- 不调用或复制 Apifox CLI 内部未公开 HTTP 接口。
- 不让平台修改 Apifox 项目、分支、环境或其他远端资产。
- 不把 OpenAPI `info.title` 强制改写成 Apifox 项目名；两者可能合法地不同。
- 不引入团队/RBAC、多租户或新的凭据系统。
- 不重构 `task_server/router.py`，不改变现有来源 ID 和 revision 所有权。
- 不修改 Agent、Runner、Sonic、Figma 或历史 YAML。

## 官方能力与版本

平台依赖 Apifox CLI 的公开命令契约：

- `apifox project list`
- `apifox project get <projectId>`
- `apifox branch list --project <projectId>`
- `apifox environment list --project <projectId>`

最低支持版本为 `2.2.6`。部署默认安装并验证当前已检查版本 `2.2.8`，同时允许通过
`APIFOX_CLI_BIN` 指向兼容的新版本或自定义安装位置。CLI 输出必须按 JSON 解析，不能
依赖终端表格、中文提示或正则抓取。

官方依据：

- [Apifox CLI 命令选项](https://docs.apifox.com/doc-5637756)
- [使用 Apifox CLI 搭配 AI Agent](https://docs.apifox.com/9212297m0)
- [新版 CLI 与 Skill 发布说明](https://www.apifox.cn/blog/apifox-cli/)

## 用户流程

### 新增来源

1. 用户点击“新增 Apifox 项目”。
2. 页面只展示 Token 输入框和主操作“读取 Apifox 资产”。
3. 平台返回该 Token 可访问的项目列表。项目卡片/选项显示：
   - 项目名称；
   - 团队名称；
   - 项目描述（存在时）。
4. 用户选择项目后，平台读取该项目的分支和环境。
5. 分支默认选择“主分支（默认）”，其保存值为空；用户也可按名称选择具体分支。
6. 环境默认选择“不绑定环境”，其保存值为空；用户也可按名称选择具体环境。
7. 用户设置同步周期和同步范围后保存。来源名称默认采用远端项目名称，不要求再次填写。
8. 保存沿用现有行为：配置变化且自动同步已开启时立即排队一次真实同步。

项目、分支和环境 ID 作为选项的内部值保存。默认界面不把 ID 拼入主文案。

### 已有来源

- 项目切换器优先显示 `provider_metadata.project_name`。
- 没有远端项目名时，依次使用活动 revision 的 OpenAPI `info.title`、现有 `name`、
  `source_id` 兜底；Project ID 不进入主标签。
- 设置面板显示已解析的项目、分支和环境名称，并提供“重新读取 Apifox 资产”。
- 重新读取可使用服务端已保存的 Token，不要求用户再次输入。
- 用户明确更换 Token 时，页面重新执行项目发现，不能把旧 Token 的选项沿用到新 Token。

### 手动兜底

“无法读取？手动连接”是折叠的次级入口。展开后保留现有来源名称、Project ID、
Branch ID 和 Environment ID 输入框。以下情况自动提示该入口：

- CLI 未安装或版本低于最低版本；
- Token 无效或权限不足；
- CLI 调用超时；
- 返回 JSON 无法验证；
- 目标项目已不可访问。

手动连接不是静默降级。页面必须显示可理解的失败原因，但不能显示 CLI 原始堆栈或任何
可能包含 Token 的文本。

## 架构

### 独立发现服务

新增 `task_server/services/apifox_discovery_service.py`，职责仅包括：

- 检查 CLI 路径和版本；
- 在隔离环境中执行只读命令；
- 解析并规范化项目、分支和环境；
- 把 CLI 错误映射成稳定错误码；
- 清理临时凭据目录。

该服务不保存来源、不触发同步、不读取或修改 OpenAPI revision。现有
`api_source_service.py` 继续负责持久化，`apifox_service.py` 继续负责官方 OpenAPI
导出。

公开服务接口：

```python
def discover_projects(
    access_token: str,
    *,
    base_url: str = "https://api.apifox.com",
    timeout_seconds: float = 20.0,
) -> dict:
    """Return normalized projects and CLI capability metadata."""


def discover_project_context(
    access_token: str,
    project_id: str,
    *,
    base_url: str = "https://api.apifox.com",
    timeout_seconds: float = 25.0,
) -> dict:
    """Return one project plus normalized branches and environments."""
```

规范化结果只包含非秘密字段：

```json
{
  "capability": {
    "available": true,
    "version": "2.2.8",
    "minimum_version": "2.2.6"
  },
  "projects": [
    {
      "id": "5904970",
      "name": "3D 接口",
      "description": "",
      "team": {"id": "123", "name": "打印业务"}
    }
  ]
}
```

项目上下文结果：

```json
{
  "project": {
    "id": "5904970",
    "name": "3D 接口",
    "description": "",
    "team": {"id": "123", "name": "打印业务"}
  },
  "branches": [
    {"id": "", "name": "主分支（默认）", "is_default": true},
    {"id": "456", "name": "测试分支", "is_default": false}
  ],
  "environments": [
    {"id": "", "name": "不绑定环境", "is_default": true},
    {"id": "789", "name": "APP 测试环境", "is_default": false}
  ]
}
```

### CLI 凭据隔离

Apifox 官方登录会把 Token 持久化到本地，因此服务端不能复用系统用户的全局登录态。
每次发现调用必须：

1. 创建权限为 `0700` 的临时目录；
2. 将 `HOME`、`XDG_CONFIG_HOME` 和相关配置路径指向该目录；
3. 启动 `apifox auth login`，通过标准输入写入 Token；
4. 执行只读发现命令并解析 stdout JSON；
5. 对 stdout/stderr 和异常文本执行 Token 脱敏；
6. 在 `finally` 中删除临时目录；
7. 达到整体超时时终止并回收所有子进程。

Token 不得出现在 shell 字符串或 CLI argv 中。子进程使用参数数组启动，不经 shell。
禁用 CLI telemetry，继承环境采用最小白名单。发现服务不得把原始 CLI 输出写入业务日志。

### HTTP 接口

路由只做鉴权、参数校验和状态码映射：

```text
POST /api/api-testing/apifox/discovery/projects
POST /api/api-testing/apifox/discovery/project-context
```

项目列表请求接受以下二选一凭据来源：

```json
{
  "access_token": "write-only",
  "base_url": "https://api.apifox.com"
}
```

```json
{
  "source_id": "api_source_xxx"
}
```

项目上下文请求接受以下二选一凭据来源：

```json
{
  "access_token": "write-only",
  "project_id": "5904970"
}
```

```json
{
  "source_id": "api_source_xxx",
  "project_id": "5904970"
}
```

`source_id` 方式仅在用户已登录平台且来源存在时由服务端读取该来源未脱敏 Token，
并沿用来源自己的 `base_url`。HTTP 响应不得返回 Token、Token fingerprint、CLI
配置路径或原始命令。

### 来源元数据

来源 JSON 增加兼容字段：

```json
{
  "provider_metadata": {
    "project_name": "3D 接口",
    "project_description": "",
    "team_id": "123",
    "team_name": "打印业务",
    "branch_name": "主分支（默认）",
    "environment_name": "APP 测试环境",
    "discovered_at": "2026-07-29 10:00:00",
    "discovery_source": "apifox_cli"
  }
}
```

规则：

1. `project_id`、`branch_id`、`environment_id` 继续是行为和同步的稳定标识。
2. `provider_metadata` 只用于展示，不参与 revision 所有权或配置 fingerprint。
3. 新来源的 `name` 默认等于发现到的项目名，保留旧消费者兼容性。
4. CLI 发现结果优先级高于 OpenAPI 标题。
5. 同步成功时，如果缺少 CLI 项目名，可从 OpenAPI `info.title` 写入
   `project_name`，并标记 `discovery_source=openapi_info`。
6. OpenAPI 回填不能覆盖已有 `apifox_cli` 项目名。
7. 列表接口继续返回脱敏后的 `provider_metadata`，不得混入任何凭据字段。

## 前端状态

新增来源状态机：

```text
token_input
  -> loading_projects
  -> project_selection
  -> loading_context
  -> source_configuration
  -> saving
```

任一步失败进入 `discovery_error`，保留 Token 输入和已选择内容，并显示重试及手动连接。
切换 Token、项目或关闭草稿时必须清理下游选择，避免跨项目保存旧分支/环境 ID。

项目列表支持名称搜索。项目、分支和环境选项具有稳定高度，加载、空结果和错误状态不能
让设置区域跳动。主操作在请求期间禁用，避免重复 CLI 调用。

## 错误模型

发现服务只返回以下稳定错误码：

- `CLI_UNAVAILABLE`
- `CLI_VERSION_UNSUPPORTED`
- `AUTH_FAILED`
- `PERMISSION_DENIED`
- `TIMEOUT`
- `PROJECT_NOT_FOUND`
- `INVALID_RESPONSE`
- `DISCOVERY_FAILED`

HTTP 状态规则：

- 参数错误：`400`
- Token 无效：`401`
- 权限不足：`403`
- 项目不存在：`404`
- CLI 缺失、版本不支持或上游不可用：`503`
- 超时：`504`

错误响应包含 `code`、安全中文 `error` 和 `manual_fallback=true`。Token 必须同时从异常
对象、stdout、stderr 和日志参数中脱敏。

## 部署

`deploy/install-server.sh` 在存在 Node.js 14+ 和 npm 时安装或升级
`apifox-cli@2.2.8`，随后运行 `apifox --version` 验证。若 Node/npm 不存在或安装失败：

- 主服务安装继续；
- 安装输出明确提示自动发现不可用；
- API health/capability 返回 CLI 不可用；
- 前端自动提供手动连接兜底。

不把 Apifox CLI 打包进仓库，不在每次用户点击时执行 `npx` 在线安装。

## 测试与验收

### 自动测试

- 使用假的 CLI 可执行文件验证 Token 从 stdin 输入、argv 不含 Token、临时 HOME 被清理。
- 验证 CLI 版本检查、成功 JSON 规范化、超时终止和全部错误码映射。
- 验证任何错误和响应都不包含测试 Token。
- 验证来源持久化保留 `provider_metadata`，公共来源响应不包含凭据。
- 验证 OpenAPI `info.title` 只在缺少 CLI 名称时回填。
- 验证两个发现路由的登录鉴权、参数校验、状态码和 `source_id` 凭据读取。
- 验证前端默认不展示 Project/Branch/Environment ID 输入框。
- 验证发现成功后的项目、分支、环境名称选择和保存 payload。
- 验证 CLI 失败后手动连接仍能保存并触发现有同步。
- 运行后端、前端静态检查、相关 API 工作区测试和桌面/移动端视觉检查。

### 真实只读验收

使用用户提供的 Token 只执行以下读取操作：

1. 读取可访问项目；
2. 确认项目列表包含实际中文项目名且 ID 未作为主文案；
3. 选择一个项目并读取分支、环境名称；
4. 保存来源并触发一次现有 OpenAPI 同步；
5. 刷新页面确认项目切换器仍显示中文名称；
6. 检查服务日志、来源公共响应和浏览器网络响应均不含 Token。

真实验收不创建、修改或删除任何 Apifox 远端资产。

## 成功标准

- 正常路径新增来源不需要用户查找或输入任何 ID。
- 项目切换器和设置摘要以 Apifox 返回的名称为主，不再混合显示裸 ID。
- 分支和环境按名称选择，保存后同步仍使用原稳定 ID。
- CLI 异常不会破坏现有来源、活动 revision 或手动同步能力。
- Token 不出现在 argv、日志、响应、截图、git diff 或全局 CLI 配置中。
- 现有 API 来源、OpenAPI 导入、AI 计划、MeterSphere 执行和报告回归通过。
