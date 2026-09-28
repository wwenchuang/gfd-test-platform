# 新任务审计检查记录

用户于本轮明确历史数据可以不考虑。此次是独立审计接入，不是恢复被撤回的执行快照/可信来源授权实现。历史文件保持原样，权限判断未改。

## 已实现

- create_job/create_pending_job持久化后，在现有JOB_LOCK释放前尝试记录真实ContextVar账号与请求ID；重复job_id拒绝，避免新事件混淆。客户端/任务JSON的created_by、initiator、provenance_version等不进入账号判断。
- YAML最终转换后，对同一个待返回字符串计算SHA256；记录job.dispatch_prepared/accepted，含Runner主体与审计库唯一创建事件中的发起账号。无需读取全部审计日志；空闲轮询、准备失败及APK分支不新增此调用。
- 数据库查询失败时仍通过现有私有spool保存下发事件，标记initiator_lookup_unavailable。数据库和spool均不可写时记录服务端错误，保留已落盘任务，避免让调用方因误判创建失败而重复创建业务任务。
- 操作记录页增加“已创建”“下发内容已准备”中文名称，并保留原始action及内容SHA256详情。

## 验证

- 首次13项用例9失败/4通过，明确复现缺少创建事件、内容指纹和重复ID检查；补实现后通过。
- 新增SQLite不可用用例首次失败，补lookup单独降级后通过。
- 245项后端回归全部通过（30.61秒）：test_job_audit、operation_attribution、operation_http、asset_lineage、job_time_parsing、identity、identity_http、main_access_control。
- test_job_audit共15项，包含两个账号使用真实本地HTTP会话创建任务、模拟Runner领取最终YAML、本人操作记录隔离、伪造body字段不改变归属。身份/业务路由是真实本地服务；设备分配桩不连接真实手机，不是Windows Runner验收。
- 75项录制服务/YAML/协议/Windows证据回归通过（0.87秒）；后端合计320项。
- 25项操作记录Chromium交互/布局检查通过，包括新操作中文标签；新增标签用例在修复前失败。
- 后端静态63项、前端静态84项、主链及变更Python语法检查、git diff --check通过。
- 独立只读审查已复核SQLite降级修复，当前范围无剩余可操作发现；未代替运行测试。
- 测试使用TemporaryDirectory或pytest tmp_path隔离身份/操作/任务数据；最终后端回归的数据库和服务随测试结束清理。未创建生产任务、未操作手机、未删除历史数据。

## 边界和上线状态

- 内容指纹是下发内容证据，不是冻结版本；准备下发不等于Runner收到或成功执行。
- 没有请求ContextVar的后台任务归属仍为未知。创建事件还在spool中、缺失或重复时，下发发起人保持未知，不从旧JSON补猜。完整异步/终态/报告链仍需后续实现。
- 此次新增审计写入有成本；不宣称零性能开销。按用户要求暂停性能专项，空闲轮询没有新查询。
- Chrome平台登录页及堡垒机“用户已退出登录，会话已结束”已现场核对。当前生产f50f487；本轮候选未部署，线上账号页面和新版三轮Runner仍未验收。
