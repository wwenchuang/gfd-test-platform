# Mac iOS WDA 自动恢复

Mac Runner 的 iOS 模式提供可选恢复管理。已有 WDA 健康时继续使用；健康检查不新增平台轮询。只在 Runner 空闲时对明确配置的 USB UDID 启动本机已签名、已成功构建的 WebDriverAgentRunner。

在本机 Runner 环境文件加入（以下两个 UDID 是当前已授权测试手机；不改为自动选择其他设备）：

```dotenv
IOS_WDA_AUTORECOVER=1
IOS_WDA_PROJECT=/Users/adouceshi/Applications/Sonic/sonic-ios-wda/WebDriverAgent.xcodeproj
IOS_WDA_DEVICE_IDS=00008110-000150CC21C2801E,00008130-001464540A21001C
```

更新 `mac-midscene-runner.py` 后，等待当前任务结束，停止旧 Runner，再运行本机 `~/Applications/MidsceneIOS/start-preview.command`。仅更新这台 Mac 即生效，主服务器和 Windows Runner 不需重启。服务器可下载脚本随常规发布同步。关闭恢复将 `IOS_WDA_AUTORECOVER=0` 后重启 Runner。Runner 正常退出或收到 SIGTERM 时会回收自己创建的 WDA 进程组并释放锁，其他管理者启动的 WDA 不受影响。

## 行为和状态

- 每台手机复用30秒WDA就绪采样，连续两次失败才进入恢复。正常状态下只处理少量内存数据，没有额外手机命令。
- Xcode启动在后台线程进行，不阻塞心跳。用 `test-without-building`，不重新构建、不自动重签名或创建开发者资源。未预先构建时日志明确失败，应先在Xcode完成该设备的WDA构建验证。
- 只启动配置列表中实际通过USB连接的手机。锁屏或无法确认解锁状态时不上拉WDA；解锁/重新连接后再次检查。若手机显示自动化或开发者信任提示，仍需在手机本地完成。
- 已有同UDID的Xcode/Sonic WDA启动进程时不重复启动、不结束该进程。该进程一直无响应时需要原管理者处理；本管理器只回收自己创建的进程组。
- 单次启动最多90秒。失败后至少15秒、45秒退避，第三次失败冷却5分钟后开始新一轮有限尝试。冷却到期仍需通过USB/解锁检查。连续健康60秒才清零失败次数，防止短暂成功反复重启。
- 有业务任务执行时不启动或回收WDA；掉线任务按环境异常结束，之后空闲巡检再恢复，不自动重跑业务操作。
- 卡片沿用preflight reason显示恢复状态、等待或需人工处理的信息；只有WDA实测ready才能恢复可选。USB断开会用空设备心跳同步离线。

## 日志

主日志中 `WDA recovery <UDID>` 记录启动PID与ready；每台设备的Xcode日志在 `$MIDSCENE_RUNNER_WORKSPACE/wda-recovery/<UDID>.log`，本机默认 `~/midscene-runner/wda-recovery/`。每次启动前日志超过2MiB则保留一份 `.previous.log`，不上传完整设备日志或凭据。

若退出原因是 `Timed out while enabling automation mode`，先在手机解锁并保持亮屏、完成提示。若是USB离线，恢复连接。不要用重新部署主服务器处理本机WDA故障。

## 验收

单元测试：

```bash
python3 -m pytest -q tests/test_ios_wda_recovery.py tests/test_ios_case_reuse.py tests/test_mac_ios_device_snapshot.py tests/test_mac_runner_step_progress.py tests/test_runner_tls_defaults.py
python3 -m py_compile mac-midscene-runner.py task_server/services/agent_service.py task_server/services/yaml_service.py task_server/services/yaml_executable_scorer.py
python3 tests/backend_static_checks.py
git diff --check
```

实机验证只对本任务创建的WDA进程做中断，确认三轮blocked→恢复中→ready、实时截图、平台心跳、Chrome可选状态。禁止通过杀掉不明进程或中断正在执行的业务用例来测试。
