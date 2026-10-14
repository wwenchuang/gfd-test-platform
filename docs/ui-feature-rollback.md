# UI 新批次准入、证据恢复和通知开关

`UI_SIMPLE_FLOW_ENABLED`（默认关闭）只决定新建、预检、重跑及 queued/blocked 的派发和领取；关闭后已领取的执行不会重新派发。本人或有当前应用权限的管理员仍可访问已有列表、精确版本报告、摘要、下载、附件和通知状态，并取消已有任务或按精确尝试状态确认停止。权限校验和停止证明规则保持有效，人工恢复不会将失败/取消/中断改为通过。

前端 availability 的 `enabled` 表示准入；`evidence_available`、`recovery_available` 表示已有证据与恢复入口可用。关闭准入后保留“我的报告”和通知中的精确报告链接，创建/重跑入口不可用；取消/人工恢复仍要求 `ui.execute`，读取仍要求 `ui.view` 和本人/管理员及当前应用范围。

服务启动时，关闭准入且没有 `TASK_UI_EXECUTION_DB` 对应数据库，不创建 UI 库或启动 UI 后台工作；空列表和不存在的批次读取也不创建库。存在数据库则启动必要协调与报告工作，重放未同步的 Job 状态、检测已领取执行的失联、保留未知停止的租约。已停止的结果可继续生成报告，queued/blocked 不派发；取消中的执行需原凭据确认停止，或超过原 120 秒 ACK 窗口后人工核对进程及子进程已结束再确认。旧凭据在人工恢复后吊销，迟到结果不能释放新租约或变为通过。

`UI_BATCH_NOTIFICATIONS_ENABLED` 独立控制自动通知：未设置时跟随准入，设置 `false` 时暂停，设置 `true` 时明确允许已有待发报告在准入关闭期间发送。两个开关均未设置时，新批次准入和自动通知都关闭。

生产 systemd 服务通过 `MIDSCENE_ENV_FILE=/opt/midscene.env` 加载启动配置。这两个键已进入精确白名单，不会开放所有 `UI_*` 变量。回退时用 `sudoedit /opt/midscene.env` 将已有开关赋值修改为下列内容；如键不存在再添加，避免重复赋值：

```sh
UI_SIMPLE_FLOW_ENABLED=false
UI_BATCH_NOTIFICATIONS_ENABLED=false
```

保存后执行：

```sh
sudo chmod 600 /opt/midscene.env
sudo systemctl restart midscene-task.service
sudo systemctl is-active midscene-task.service
```

启动配置只在进程启动时加载，修改文件后必须重启才会生效。服务进程中已有的非空环境变量优先于文件值，包括 systemd 单元或 drop-in 的 `Environment=` 设置；如存在这些开关的覆盖值，需先修改或移除对应覆盖，再执行 `sudo systemctl daemon-reload` 和上述重启命令。仅修改 `/opt/midscene.env` 不会覆盖既有进程环境。

默认回退重启只恢复证据，不隐式发送 pending 消息。暂停不删除 outbox、冻结通知策略或历史报告；显式允许投递后仍执行现有有界重试及 unknown 送达核对规则。已在网络中的请求可能已经被飞书接收，开关不能撤回远端消息。具有当前 `platform.notify`、应用范围和本人/管理员权限的人工补发可以登记重试，但暂停期间不会投递，必须明确恢复通知开关后才发送。发送前再次核验原执行者身份、账号、当前 `platform.notify`、应用与冻结渠道；原执行者权限撤销时 fail closed，不将人工补发视为委托授权。

无 `platform.notify` 的账号可正常执行不通知；显式请求通知的预检/创建（含幂等重试）拒绝。无该权限时新重跑冻结 `notifications.enabled=false`，原批次报告和策略不变。

保持数据库 schema 4 与私有文件权限，发布前备份数据库；关闭准入是兼容回退方式，不删除库、不降低 schema、不换旧代码绕过租约。重新启用后旧终态批次不会重复执行，尚未取消的排队批次才继续检查原目标和权限。新链路的生产设备、代理下载、真实通知和 Windows 进程停止仍需独立验收。
