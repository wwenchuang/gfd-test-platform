# UI 批次不可变报告飞书投递（T6，本地实现，未部署）

## 行为与边界

- 创建批次仍只接受 `notifications={enabled:boolean}` 用户意图，不接受地址、渠道或执行人覆盖。渠道 identity/来源/中文目标标签由应用配置和既有默认规则解析并冻结；幂等 hash 保留用户意图，配置变化后的相同请求仍返回原批次。用例库返回已授权应用的非敏感通知目标，开始测试页在有配置时默认开启，支持本次关闭。
- 私有 SQLite v3 为 outbox 增加 claim/result/manual retry 字段与 `(batch, report_revision, channel)` 唯一索引。发送前重查账号、应用范围、应用启用状态及同一配置指纹；无配置、无效配置、改渠道、禁用应用均不改发其他群。不保存 URL、远端文本或异常原文。
- 报告就绪后由独立后台线程入队和发送。领取、网络发送、结果保存分别进行；网络调用期间不持 SQLite 写锁，不阻塞设备 reconciler/领取/心跳。新 UI 批次不进入独立 Sonic 套件通知注册，旧独立套件保留行为。
- 明确拒收只重试 30 秒、120 秒、600 秒，第四次仍拒收为 failed。403 等明确客户端 HTTP 拒收按此重试；超时、响应丢失、5xx、非确认回包、worker 中断均 unknown，不自动再发。超时领取及 v2 无 timestamp 的 in-flight 迁移同样 unknown。
- 卡片仅用该 revision 的冻结 Chinese presentation：测试批次标记、执行人、测试时间、应用版本/构建、手机环境、分项结果。链接只含批次及该 revision，需平台 shell 登录和当前批次/应用权限。下载继承已有中文文件名和私有响应头，摘要直接复制同一 snapshot。
- 补发要求平台通知权限和批次/应用访问权限，仍须当前配置匹配原渠道。unknown 补发先显示群内核对及可能重复提示，操作者明确确认未收到后才入队。已发送/正在发送/已入队不能重复点击补发；不宣称严格一次送达。

## 版本与性能

- 报告 GET、下载、通知状态请求均引用实际显示的 revision。前后端分别过滤该版本的 delivery，旧报告不会展示新版已发送状态或操作新版补发。旧 delivery 完成也不能覆盖当前报告的通知状态。
- 批次 `pending` 表示报告待入通知队列，`queued` 表示已有 pending outbox；之后为 sending/retry_wait/sent/failed/unknown。报告发布新 revision 时重设 pending；重访已存在 digest 复用该精确 delivery 状态，不额外发。
- 新部分索引 `batches_notify_ready` 只覆盖 execution_settled=1、report_status=ready、delivery_status=pending 的记录。队列 discovery 显式使用这组条件；LIMIT 并非性能证据。
- 专项加入 2,001 条已通知历史批次的实际 SQLite query-plan / VM 回归。修复前约 86,000 VM instructions，修复后查询计划使用 `batches_notify_ready` 且低于 3,000 instructions 测试预算。此结果验证本地查询边界；上线 HTTP、CPU、设备心跳对照仍待集成验收。

## 本地验证

- TDD：最初 14 项新行为失败；追加精确版本、HTTP 5xx、访问分类、旧完成状态覆盖、schema 升级、显式 revision 和历史扫描用例先红后绿。
- 最终通知专项 25 项；较广 Python 249 项通过（batch/dispatch/report/catalog/contract/access/Feishu/presentation）。Node 63 项通过（入口、登录返回、选择/opt-out、报告 pin/retry、执行卡片）。
- 主链及所有改动 Python 模块编译、五个 JS 语法检查通过；backend static 63、frontend static 84、git diff --check 通过。
- 扩大回归最初旧 Feishu draft 测试试图使用 `/opt/midscene-auth` 而缺本地权限；改用 `/tmp/codex-ui-simple-20261013/t6-auth-fixture/identity.sqlite3` 隔离测试 DB 后 249 项完整通过。未改旧 draft 实现，也未访问生产身份 DB。
- 本子任务未部署、操作浏览器/设备、读取凭据或发消息；真实测试群、飞书内置浏览器打开/下载/复制由主任务在明确授权渠道验收。机器人原生 DOCX 附件适配留为独立可选后续；当前 Word 下载不依赖机器人附件权限。

## 集成接口

- `GET /api/ui-executions/{batch}/deliveries?revision={sha256}`：鉴权状态和 frozen policy，省略 revision 可审计该批次最近 50 个 delivery；前端使用精确 revision。
- `POST /api/ui-executions/{batch}/deliveries/{delivery_key}/retry`，body `{"confirmed_not_received":false}`；unknown 经核对用 true。成功 202，权限 403、非本人/URL 批次不匹配 404、配置变更或非可补发状态 409。
- 卡片深链 `{配置的public base}/task-manager.html?ui_batch=uib_{32hex}&report_revision={64hex}`。登录后优先打开我的报告及该 revision，挂载后仅移除这两个参数；不接受外部返回地址/泛化 latest。
- 常规部署由主任务统一执行：`cd /opt/midscene-task-platform-src && bash deploy/update-main-server.sh`。新后台线程随主服务启动；私有数据库首次打开自动 v3 迁移。无需为此更新 Runner。
