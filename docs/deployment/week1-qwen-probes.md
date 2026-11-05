# 第 1 周千问固定探针

探针由 Task Server 直连服务端已配置的 DashScope，使用 `DASHSCOPE_API_KEY`（或平台既有兼容密钥配置）、`DASHSCOPE_BASE_URL`、`DASHSCOPE_MODEL` 和 `DASHSCOPE_VL_MODEL`。只支持指定的 DashScope HTTPS 地域主机与 `/compatible-mode/v1`，不跟随重定向；图片和提示词由服务端固定，用户不能指定地址、文件或模型。真实调用消耗模型额度。输出中的请求模型不等同服务端返回的模型标识；`status` 表示已收到非空模型回答，文本需另外检查 `text_check`，视觉必须独立人工核对。

记录存储在身份库旁的 `week1-probes.sqlite3`，例如默认身份库 `/opt/midscene-auth/identity.sqlite3` 时为 `/opt/midscene-auth/week1-probes.sqlite3`。设置 `TASK_AUTH_DB` 会同时更改其存放目录；该目录必须可由 Task Server 服务用户写入并在部署中持久保留。数据库文件限定为服务用户所有并设置 0600；现有认证目录应保持私有。记录按登录用户 ID 隔离，最多返回最新 50 条；记录保存脱敏且限长的固定问答摘要、耗时、状态和确认人及时间，不存密钥或原始异常。全局数据库租约防止并发调用并对完成后的重复点击进行冷却，异常退出后的租约最长 5 分钟过期。更改进程或服务器架构时，所有服务实例需要共享同一数据库，且 SQLite 文件系统须支持正常的文件锁。

接口需要 `platform.configure`、完整 UI 应用范围和个人会话；固定图通过同权限 GET `/api/platform/week1-probes/fixture` 获取。发布后应在页面分别实际检查文本、视觉与人工核对，模型服务连通性不能仅靠模拟测试宣称通过。
