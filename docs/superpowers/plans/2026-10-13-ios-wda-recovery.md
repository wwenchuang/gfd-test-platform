# iOS WDA 自动恢复实施计划

**目标：** 已授权且配置的 iPhone 在 WDA 断开后，Mac Runner 自动有限恢复；不干扰正在执行的用例和其他管理者的 WDA。
**架构：** 在现有 Mac Runner 空闲设备巡检中集成按 UDID 状态机，复用30秒readiness采样；xcodebuild子进程异步运行。不开新常驻服务，不发额外服务器轮询，不自动重签名、登记设备或更改手机权限。
**技术：** Python标准库、Xcode test-without-building、USB精确转发、既有preflight reason展示。
**取舍：** 系统KeepAlive只能恢复进程退出，无法处理WDA无响应；独立守护进程容易与Runner/Sonic争抢，因此复用Runner空闲巡检。

- [x] 新增tests/test_ios_wda_recovery.py：健康无动作、连续失败阈值、配置设备白名单、锁屏/状态未知拒绝启动、重复进程、任务执行保护、启动超时与仅回收自身进程、退避/冷却、断开重连、就绪后稳定窗口、异常信息分类。
- [x] mac-midscene-runner.py：新增WDA恢复配置与状态机；持有每设备文件锁；检查Xcode已有精确UDID进程；只启动签好名且构建过的工程，使用test-without-building，不加自动签名授权参数。上报正在恢复/等待重试/环境异常，不伪装ready。
- [x] 新增docs/ios-wda-recovery.md：配置开关、工程路径、设备列表、日志位置、失败等待/人工处理方法、启动方式。本机备份脚本，确认无执行任务后更新已有Runner并配置开关；不停止外部WDA。
- [x] 必跑Python专项、主链/Runner语法、后端静态、diff检查。逐项记录，不以mock通过代替实机。
- [x] 真机只对iPhone13执行三轮：停止本任务此前启动的WDA，或当前恢复管理器拥有的WDA，验证状态转blocked→recovering→ready、截图、线上心跳与Chrome。确认已有业务脚本可回放，保留结果证据。
- [x] 更新CODEX_STATE.md、提交推送；本机Runner部署即生效，主服务器无需为此重启，公开下载脚本随后续服务器发布同步。

验收结果：四轮恢复通过；业务首轮失败、随后三轮通过，首次未跳转保留待定位。详见 docs/evidence/ios-wda-autorecovery-2026-10-13.md。
