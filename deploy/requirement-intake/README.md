# 飞书项目需求接入

同步任务每五分钟读取指定空间已提测的工作项。插件需要该空间的工作项读权限，平台服务账号需要目标应用的 `ui.view` 和 `ui.execute`。同步按工作项类型和空间再次核对每一条返回记录。飞书未返回可读验收条件时，该条保留为同步错误。工作台首页展示已接入需求。批次提交、Runner 执行和缺陷创建分别显示各自结果。[飞书项目工作项筛选接口](https://project.feishu.cn/b/helpcenter/1p8d7djs/19pti4e2)

在服务端现有凭据管理中配置 `FEISHU_PROJECT_PLUGIN_ID`、`FEISHU_PROJECT_PLUGIN_SECRET` 和 `FEISHU_PROJECT_USER_KEY`。在 `/etc/midscene-feishu-requirements.env` 设置 `MIDSCENE_FEISHU_REQUIREMENT_CONFIG` 和 `MIDSCENE_REQUIREMENT_BINDINGS` 为两个服务端 JSON 文件的绝对路径。工作台进程需读取同一个 `MIDSCENE_REQUIREMENT_BINDINGS` 和同一套身份、批次数据库、任务目录及 `UI_SIMPLE_FLOW_ENABLED=1` 环境。用实际部署值替换以下示例。

源配置中的 `project_key` 使用飞书项目空间 ID。`search_group` 使用该空间标记「进入测试」的条件。`acceptance_field` 支持文本或字符串列表。只有租户启用且字段确实存在时才填写可选的 `scenario_field`、`revision_field`。省略 `revision_field` 时使用标题、验收条件和业务场景内容的摘要作为版本，重复轮询和无关字段变更共用同一批次。

```json
{
  "project_key": "example_project",
  "work_item_type_key": "story",
  "app_id": "com.kfb.model",
  "username": "qa-service",
  "user_id": "platform-account-id",
  "acceptance_field": "field_acceptance",
  "scenario_field": "field_scenario",
  "search_group": {
    "conjunction": "AND",
    "search_params": [{"param_key": "work_item_id", "operator": "HAS ANY OF", "value": [123456]}],
    "search_groups": []
  }
}
```

示例条件适合用一个已授权工作项试运行。正式启用时换成实际提测筛选条件。工作台管理的映射可以按场景复用，也可以把 `scenario` 替换为 `requirement_id` 和 `revision` 绑定单个版本。后者优先。通过首页手工录入的需求使用来源 `workbench` 及精确的需求版本映射。缺少映射的需求可在工作台查看，补齐后重新接收同一条需求即可继续。有现有需求设计权限的账号可将原需求标题、版本和验收条件保存为本人的设计材料，在「需求生成测试设计」中继续。保存材料不启动模型或 Runner。

```json
[
  {
    "source": "feishu-project:example_project",
    "app_id": "com.kfb.model",
    "scenario": "navigation",
    "runs": [{
      "execution_policy": "reviewed_non_printing",
      "cases": [{"case_id": "published-case-id", "revision": "sha256-of-reviewed-published-yaml"}],
      "target": {"platform":"ios","runner_id":"registered-runner-id","device_id":"registered-phone-id"}
    }]
  }
]
```

由维护者逐条审阅已发布用例的固定内容，确认不会下发真实打印任务后填写其 SHA-256。每个平台最多三条冒烟。Android 和 iOS 分别填写已验证的版本与手机。同步仅下发映射中列出的这些用例。设备忙、Runner 不支持或修订变化会记录阻断原因。已绑定的请求保存原版本和设备。新的基线快照使用新的需求版本。测试状态可在工作台对应批次查看。

按实际运行目录安装随附的 service 和 timer。配置部署本身不代表接入已验证。

```sh
sudo install -m 0644 deploy/requirement-intake/midscene-feishu-requirements.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start midscene-feishu-requirements.service
sudo journalctl -u midscene-feishu-requirements.service -n 20 --no-pager
sudo systemctl enable --now midscene-feishu-requirements.timer
```

单次启动会按上述范围入库并下发通过预检的冒烟。先用已授权的一个需求核对需求编号、原条款、手机、Runner 报告和飞书项目缺陷，再扩展筛选范围。服务日志只写状态数量及无法接入的工作项编号、原因码。

## 服务器填写入口

工作台服务与需求同步服务可以共用 root 所有、权限 0600 的 `/etc/midscene-feishu-requirements.env`。样例见 [环境文件](feishu-project.env.example)。使用服务管理器的 `EnvironmentFile` 指向此文件，保留工作台原有环境及批次存储配置。实际配置文件需允许服务用户读取。更新凭据后在空闲窗口重启工作台，先对指定的一条需求运行同步服务并核对结果，再启用定时器。

## 按真实节点试运行

空间 `67a45d4c1b9ba3fa23a3ca0a` 的需求类型为 `story`。试运行在开发和转测两项源配置中共用下列范围，把开发和转测的节点 ID 分别放入各自的 `current_node_ids`。两阶段可配置完全相同的 `"title_contains":"【APP】"`，只接入详情标题含该文字标签的需求。搜索命中但详情标题不符时记录 `outside_title_scope`，跳过读取 PRD、生成与派发；已更名出范围的需求会撤销旧的打包上下文。配置后同步按搜索结果批量读取工作项详情，用 `current_nodes[].id` 核对该条需求在本次同步时的节点。开发和转测使用同一搜索条件时，两组节点必须齐全且没有交集。如果工作项同时命中两阶段，平台记录节点冲突并等待下一次同步。

```json
{
  "project_key": "67a45d4c1b9ba3fa23a3ca0a",
  "work_item_type_key": "story",
  "search_group": {
    "conjunction": "AND",
    "search_params": [{"param_key": "work_item_id", "operator": "HAS ANY OF", "value": [7082224050]}],
    "search_groups": []
  }
}
```

上述空间中，Android 开发、iOS 开发、Server 开发的节点 ID 依次为 `waiting_for_android_development`、`state_75`、`waiting_for_ios_development`，公共测试冒烟和 QA 验证为 `state_65` 和 `state_51`。首次试运行时，按业务确认的共同转测节点配置。需求 7082224050 在 2026-11-28 回读为生产验收 `state_62`，在上述两组节点之外，对应单次日志为两个 `outside_current_node`。实际工作项 ID 命中、节点外跳过及设计完成在日志中分别核对。手机版 Figma 链接字段是 `field_4`，文件范围为 `2jTQVOVDORiU3yQlQQBsoJ`。需求文档字段是 `wiki`。专用 QA 包、服务账户与手机 Runner 使用部署时已核实的登记值。尚无可核实的专用 QA 包时，转测项可设置 `"build_source":"packaging_hook","qa_packages":{},"generated_targets":{}`。开发照常同步，命中转测节点时工作台显示等待专用 QA 包，CI 发来的构建被拒绝。

飞书设计字段指向整张 Figma 画布时，可在精确需求的配置中增加 `figma_canvas_frame`。原字段仍提供文件和画布。每轮通过 Figma 文件接口读取指定图层及其祖先，核对文件、画布、画板、图层 ID、图层名和可见性，再按本次文件版本导出画板。图层移到其他画板、隐藏或改名时，工作台暂缓生成并撤销该需求旧的打包上下文。相同正文与画板图复用已有的设计及模型回执。

例如「APP+Pad 全局加载优化」7169491764 的验收文字取自 `description`，手机画布来自 `field_1e9d17`。在独立的 `development_design` 配置中，使用该工作项 ID 的等值范围和实测开发节点，设置 `figma_files` 为单个手机文件，再加入下列绑定。可为现有五分钟轮询的 oneshot 服务追加 `ExecStart=/usr/bin/env MIDSCENE_FEISHU_REQUIREMENT_CONFIG=/etc/midscene-feishu-project/global-loading.json /opt/midscene-task-platform/.venv/bin/python -m task_server.services.feishu_requirement_sync`，沿用服务身份和环境。

```json
{"figma_canvas_frame":{"file_key":"2jTQVOVDORiU3yQlQQBsoJ","canvas_id":"9232:7925","frame_id":"10051:21131","layer_id":"10108:21997","layer_name":"全局加载"}}
```


开发负责人使用飞书角色字段时，缺陷配置的 `developer_assignment` 可填 `{"requirement_type":"story","developer_field":"role_owners","developer_role":"Server","assignee_field":"<缺陷模板的可写单人字段>"}`。按实际责任角色填写 `developer_role`，仅当该角色唯一且含一名成员时指派。目标字段为多人字段时，在 developer_assignment 中增加 `"assignee_field_type":"multi_user"`，写入单一负责人的数组。本空间实测缺陷处理人为 `issue_operator`，应用级 `"requirement_link_field":"_field_linked_story"` 则写入同一空间、本次需求的数值 ID。普通单人开发字段继续沿用三项配置。角色值和角色负责人结构见[官方 SDK](https://github.com/larksuite/project-oapi-sdk-golang/blob/f35d9ad3ad7b362722a54b16ea2697bf733ae8ee/model/field_value.go)。

开发与转测同步使用已激活且完成初次改密的平台身份，需为超级管理员，或具有 `platform.configure`、`ui.view` 及全部 UI 应用范围。转测还需目标应用的 `ui.execute`；飞书缺陷登记另需 `platform.notify`。

## 开发出用例，转测后按 QA 包开测

生命周期接入使用同一个同步服务。将源配置写成两项数组，分别设置 mode 为 development_design 和 qa_build_smoke；两项共用工作空间、需求类型、服务用户、应用、验收字段、版本字段及 Figma 配置，search_group 可分别选择本空间真正的开发节点和提测节点，也可按工作项范围搜索，再用各自的 current_node_ids 限定实际开发与转测节点。原来的单对象配置沿用既有提测冒烟行为。联调先把两个筛选条件都限定到同一条测试需求。

- 开发节点读取验收条款。界面需求配置 figma_field 与 figma_files，前者是需求上保存 Frame 链接的字段，后者是允许读取的文件 key 列表。字段可提供单个链接或最多八个链接。服务需要 FIGMA_TOKEN 或 FIGMA_ACCESS_TOKEN 的目标文件读权限。工作台使用绑定节点的定版 PNG、来源摘要和测试设计模型生成候选。缺少已配置的 Frame 时留下同步错误；只有文字材料时只能验收有文字依据的内容。需求设计库沿用当前单账号 32 份材料额度。
- 首页需求进度可以展开生成的用例。既有设计、历史已跑批次和原始截图均按当时版本保留。同一份 PRD 和同一组设计图重复送达复用结果。转测后再改设计也会生成新稿，先前包对应的材料失配时暂停开测。模型失败先保留原因；同份材料最多自动补试两次（至少间隔五分钟和三十分钟），历史结果保留。
- 转测配置 qa_packages，按 android 或 ios 填写**专用 QA 包或 bundle ID**。设备必须已装对应包。可切生产地址且不能证明实际环境的通用包，暂不接这套自动投放。build_source 为 packaging_hook 时由 CI 同时发飞书卡片和机器回执；为 project_field 时由打包服务独占写入 build_field。普通群成员的消息和截图无法作为构建凭证。
- 对应设计已有人验证过的可执行基线，在既有绑定中将 revision 填为设计材料 source_id。每端的 cases 最多三条，remaining_cases 可选。第一次规划后冻结冒烟、后续用例、设备和各自发布版本；冒烟结束后，在后续同步周期检查首次执行是否全部有明确终态且至少一半通过，满足才继续。每个阶段独立幂等。新增功能仅生成设计、没有已验证脚本时，首页显示待绑定已发布基线。当前自动调度复用 UI Runner，接口和人工项会展示在设计中。
- 缺陷配置可带 developer_assignment，例如 {"requirement_type":"story","developer_field":"field_developer","assignee_field":"field_assignee"}。后一个字段需为可写的单人字段。带 QA 构建的批次必须配置该路由。建单前读取同空间同需求的负责人 user key；空值、多人或无权限时保留调查结果，等待飞书项目补齐再重试。模板启用 required_mode=1 校验。证据规则沿用同一失败步骤及同版本断言。图片上传及原单跨环境复测另见 feishu-defects.md。

### 回调与打包接口

在服务器工作目录部署服务文件，监听端默认仅为 127.0.0.1:18746。外部入口由现有 HTTPS 反向代理仅转发下列路径，保留请求体大小限制。为 systemd 配置独立的 MIDSCENE_FEISHU_HOOK_TOKEN、MIDSCENE_FIGMA_HOOK_PASSCODE 和 MIDSCENE_QA_PACKAGING_TOKEN。环境文件与数据库目录必须与工作台指向同一数据源。在测试空间试通后启用 midscene-requirement-hook.service、midscene-requirement-events.path 与原有 timer。

| 回调路径 | 谁调用 | 结果 |
| --- | --- | --- |
| /hooks/qa/feishu-project | 飞书项目自动化操作，配置节点和字段变更规则及验签 token | 核对空间和 token，唤醒同步器重新读取需求 |
| /hooks/qa/figma | 授权文件的 Figma Webhook | 核对 passcode 和文件范围，合并唤醒；五分钟轮询可补漏 |
| /hooks/qa/build-context | CI 在开始打包时 POST token、project_key、requirement_id | 返回 data 中的 project_key、requirement_id、revision、source_id，把这份材料版本随构建保存 |
| /hooks/qa/build | CI 成功打出 QA 包、发送飞书通知时 POST token 和 build 对象 | 保存同份构建回执，转测先到或打包先到均可；每端取该需求版本下最高的构建序号 |

build 对象在 build-context 返回的四个字段外，需要 platform、environment（qa）、package、version_name、version_code 和 build_id，均为字符串。打包回调额外传正整数 build_sequence，取 CI 对同一端单调递增的构建编号。包名、版本名、版本号须与 Runner 实际安装记录相符。build_id 标识打包流水线；此路径能核对安装元数据，未获取可执行文件摘要。project_field 模式填同一结构化对象，省略 build_sequence。源码去重、构建回执去重、冒烟批次去重和缺陷回执分开保存。

联调需对照一条含高保真和开发负责人的真实需求、对应打包通知、设备安装版本和一次故意失败的 QA 断言。实际检查开发节点生成且不启动设备，PRD 或绑定帧变动引发新设计，无关帧保持原设计，转测与包到齐才启动，冒烟失败拦截后续，原单可在飞书测试空间由目标开发打开。群里既有旧卡片的字段适配和直接订阅群消息要根据打包机器人身份及样例另行验证。Figma FILE_UPDATE 有上游合并延迟。[飞书项目自动化操作](https://www.feishu.cn/content/ef835n76) [Figma 事件](https://developers.figma.com/docs/rest-api/webhooks-events/)

已验证场景也可跨需求版本复用。绑定配置保留 source、app_id、runs，将 requirement_id 和 revision 两项同时省略，改填 coverage。它包含 title、按需求顺序的 clauses（原验收文字）和 frames（每帧的 file_key、node_id、已入库 PNG 的 sha256，无图时为空数组）。平台优先使用精确材料绑定；没有时才查找验收内容完全相同且唯一的规则。设计图或业务断言发生变化后，旧的覆盖规则会停止匹配。

### 新需求的脚本与 QA 冒烟

开发阶段生成用例时，模型同时读取本版需求条款和绑定的 Figma 图稿，为有明确可见入口的移动 UI 用例给出分端导航步骤。工作台在需求的「查看测试用例」里展示每端脚本、可执行性评分或阻塞原因。生成的脚本属于该需求版本。人工用例、服务端断言和会实际打印等副作用步骤单独保留在设计里。

在转测配置的 `generated_targets` 中按应用和端指定 QA Runner 和设备，例如 `{"android":{"runner_id":"<已登记的安卓Runner>","device_id":"<QA手机编号>"}}`。同一项目的后续需求可以复用此配置。构建通知仍须匹配项目、需求、材料版本、QA 包名和机上版本。已有精确匹配的基线优先使用，其他需求读取本次生成并通过静态校验、评分和脚本 dry-run 的版本。首条先冒烟，有通过回执后再派发剩余脚本；设备忙、版本不符或执行未结束会显示在需求收件箱。重复通知复用原批次。

工作台手工登记的需求也可验收已安装的 QA 包。管理员在身份库目录的 `requirement-qa-targets.json` 按应用包名、平台登记专用 QA 测试机，格式为 `{"com.example.app":{"android":{"runner_id":"…","device_id":"…"}}}`。在需求设计详情展开「用已安装的 QA 包验证脚本」，输入机上版本，并保留页面直至冒烟返回。报告标识 `installed-版本号` 表示已安装包核对。打包系统推送的自动回归继续使用原始 CI 构建编号和服务端定时同步。

### 对照 Figma 与真机截图

在「测试工作台 → 需求回归与巡检 → 设计稿与真机对照」选择需求。已绑定设计稿的需求会带出 Figma 链接，也可粘贴飞书需求中「查看设计稿」的目标链接。点击「读取 Figma 画板」，选择画板、本人已有执行报告、对应步骤和手机实际使用的环境，点击「对照两张图并保存分析」。环境由操作人标注。

报告保留当次 Figma 版本、画板、真机步骤截图、原执行报告和视觉模型回执，可以从同一账号重新打开。平板画板和竖屏手机的排版需用相应设备复验。后台复用服务端 Figma 读取凭证和现有视觉模型配置。飞书项目空间授权由租户管理员管理。

### 知识库中的需求正文

当验收字段保存飞书知识库或新版云文档链接时，轮询器从配置的企业域名解析文档，通过飞书开放平台读取当前正文，再按实际内容计算版本。配置 `MIDSCENE_FEISHU_DOCS_HOST`、`FEISHU_DOCS_APP_ID` 和 `FEISHU_DOCS_APP_SECRET`。将目标知识库或单篇文档的阅读权限授予该应用，并开通节点与文档只读权限。文本超过平台设计容量会给出明确错误。拼图需求在工作项的 `wiki` 字段提供知识库链接，`field_4` 对应手机设计。后台定时读取与员工浏览器登录各自鉴权。[飞书知识库授权](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-overview)

个人账号可授权需求同步器读取指定 PRD。服务器使用官方飞书 CLI 的用户身份，凭据保存在服务用户专用的 `/var/lib/midscene-feishu-docs`（权限 0700）。安装时核对官方发布包校验和，将可执行文件放入 `/opt/midscene-feishu-docs/lark-cli`。使用与轮询器相同的系统用户及该专用 HOME，通过官方页面完成配置和个人只读授权。运行 `auth status --json --verify` 检查用户授权，并通过工作项里的真实链接读取节点和正文验收。持续同步需要可续期的用户授权。

在轮询器环境中设置 `MIDSCENE_FEISHU_DOCS_IDENTITY=user`，并将授权读取的文档标识以空格分隔配置在 `MIDSCENE_FEISHU_DOCS_USER_DOCUMENTS`，例如 `wiki/<节点标识>`。链接须同时匹配原有企业域名。撤销个人授权后同步器会记录读取错误。请勿将个人密码、浏览器 Cookie 或用户令牌填入项目配置。

### Android 打包群与蒲公英网页安装

在设备所在的 Mac 上运行 `python3 scripts/feishu_android_build.py PROFILE --mode upgrade`。默认 lookup 只查询。new 要求设备未安装应用，upgrade 保留数据，reinstall 要求设备已安装、Android SDK 的 aapt 和 apksigner 可用，并显式传 --confirm-clear-data。脚本仅处理 Android 单 APK。Mac 需要可用的 Chrome、Node.js、Playwright 和原有 Python Runner 运行环境。

PROFILE 为管理员在 Runner 本机保存的 JSON，包含 environment（qa 或 production）、title（通知标题）、chat_id、sender_id、version、package、device、smoke_yaml（当前环境已验证的脚本）、runner_script 和 reports_dir。可用 runner_env_file 加载 Runner 配置，node_modules 指定 Playwright 的模块目录。飞书凭据由服务环境 FEISHU_BUILD_APP_ID 和 FEISHU_BUILD_APP_SECRET 提供。脚本只查询该群及指定发送者；QA 包通知须明确标记 QA 或测试环境。群通知和详情页的构建、版本须一致，网页直接下载无需蒲公英 API Key。正式版手动联调可传 --page、--build 和 --code。

服务器凭证任务使用本目录的 midscene-zxb-environment-renew.service 和 .timer。在已部署 scripts/renew_zxb_environment.py 及对应 EnvironmentService 条件更新后，通过 systemctl enable --now midscene-zxb-environment-renew.timer 启用。service 使用原有服务器授权配置。令牌无效时终止该环境更新，可从日志核对环境和业务码。
