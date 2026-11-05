# 第 1 周网页体检与冒烟部署说明

“AI 测试实战”的操作由 Task Server 执行固定的环境检查、反馈分类匿名只读检查和本地匿名登录页检查。每次 API/UI 运行均包含正常预期和故意错误预期；匿名返回 4009 是拒绝未认证访问，不代表认证业务请求成功。UI 不提交登录或操作手机。模型文本和视觉验证仍由独立千问探针处理，真实模型调用可能产生费用。

仅有平台配置权限、完整 UI 应用范围且使用个人账号的用户可运行或查看结果。结果按用户隔离；截图和 trace 位于服务账号私有目录，只能通过固定 ID 与证据类型的受限接口读取。页面和接口不接受自定义命令、地址、浏览器路径或凭据。运行证据会随使用累积；请按团队证据保留制度评估空间，不要未核实就删除历史。

## 运行依赖

服务端固定脚本随 `task_server/fixtures/week1_smoke/` 发布。UI 检查另外需要服务账号可读取的隔离 Node.js、锁定的 Playwright 1.55.1、与该版本匹配的 Chromium Headless Shell、以及发行版匹配的动态库。本次服务器的隔离副本位于 `/opt/midscene-week1-runtime-20261105`，不包含密钥；安装或迁移时应按 `tests/week1_smoke/package-lock.json` 和官方浏览器分发源重新准备、验证权限和版本。

可以通过独立 systemd drop-in 给 Task Server 设置以下服务端配置；这些值不是用户输入，不应指向用户可写、未经验证的程序或目录：

```ini
[Service]
Environment=TASK_WEEK1_NODE=/usr/bin/node
Environment=TASK_WEEK1_NODE_PATH=/opt/midscene-week1-runtime-20261105/node_modules
Environment=TASK_WEEK1_BROWSERS_PATH=/opt/midscene-week1-runtime-20261105/browsers
Environment=TASK_WEEK1_LIBRARY_PATH=/opt/midscene-week1-runtime-20261105/libs
```

修改 systemd 配置后需要 `systemctl daemon-reload` 和安全窗口重启 `midscene-task`；重启会中断该服务正在处理的请求和任务。发布前检查平台重任务、API worker、Runner 及并行发布状态，保留正在进行的工作。缺少依赖或无权限应显示阻塞；不能把浏览器没有运行算作 UI 通过。不要把 Key、认证头或模型配置写入此运行时目录。

## 验收边界

以具有相应权限的个人账号打开“AI 测试实战 → 第 1 周入门步骤 → 服务器体检、API / UI 冒烟与结果”，分别运行三项。检查结果状态、时间、预期和实际值；API/UI 故意失败的真实退出码应为 1，异常或未启动不能冒充预期失败。UI 正反向截图与 trace 应可由同一账号预览或下载，其他账号不应能读取。环境网络检查仅表明相应 HTTP 响应，不代替千问模型鉴权与视觉人工核对。

历史数据库默认存于身份数据库同级的 `week1-smoke.sqlite3`，证据在同级 `week1-smoke-artifacts/`，权限需保持私有，部署不得覆盖既有数据库或清理其他任务产物。接口结果和浏览器状态属于实际执行记录；历史摘要不应显示为实时结果。手工计时、视觉人工确认及账单核对仍分别待办。
