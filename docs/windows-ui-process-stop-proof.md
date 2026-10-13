# Windows UI 批次执行进程停止证明（T4b1）

新 UI 批次要求 Runner 心跳显式上报 `ui_process_stop_proof: 1`，与 `ui_attempt_events: 1`、`ui_private_reports: 1` 一同检查。服务器在预检、派发和领取时检查能力，缺失会阻止执行并提示 Runner 升级；不依据版本字符串猜测能力。Mac Runner 保留原有私有进程组边界，并修复 macOS 在退出过渡期短暂 EPERM 的探测竞争：有界重查，仅在组消失且直接子进程结束后确认，持续无权限保持 false。

Windows Runner 使用 Python 标准库 `ctypes` 调用 Win32 Job Object，不安装 pywin32 或额外运行库。先创建私有、不可继承、未命名 Job，并仅设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`，不允许 breakaway；再启动内联 Python guard。guard 使用隔离 Python 启动，等待 stdin 闸门，Runner 用 Popen 保留的进程句柄完成 Job 分配后才允许 guard 启动真正 CLI。参数以 JSON 数组传给 guard，再以 argv 列表调用 Popen，不新增 shell 拼接。CLI 继承 stdout/stderr 和任务环境，原有步骤观察器继续工作；guard 使用 `ExitProcess(UINT)`保留完整 Windows DWORD 返回码。

取消、超时和正常退出都检查 Job，而不是只看父 PID。仍有活动进程则显式终止 Job，再在 10 秒内查询 `JobObjectBasicAccountingInformation.ActiveProcesses`，仅为零且父进程已回收、Job 句柄成功关闭时才发送 `execution_stopped=true`。失败或证明未确认保持 false；即使 CLI 返回 0，清理未确认也不会报告通过，原 CLI 返回码和已收集输出仍保留。启动分配失败不会放行 CLI，回调保持停止未确认。关闭句柄是异常兜底，不能单独作为停止证明。

Job 句柄仅存在 Runner 中，guard 和 CLI 不继承。Runner 崩溃时操作系统关闭最后 Job 句柄，可终止该 Job 所有进程。此项不恢复丢失的 claim token，不建立持久恢复日志，不解决启动时租约恢复；这些仍属于 T4b 范围。

## 更新及启动步骤（说明；本次未部署）

1. 先保持 `UI_SIMPLE_FLOW_ENABLED` 关闭；在维护窗口确认当前执行状态，再更新服务器和 Runner。新批次要等待真实 Windows 验收。
2. 将审核后的服务器提交发布到原有分支后，在主服务器源码目录使用现有命令：

   ```bash
   bash deploy/update-main-server.sh
   ```

3. 替换 Windows 服务实际使用的 `windows-midscene-runner.py` 单文件，保留原服务账户、工作目录和环境。若原安装位置是 `D:\sonic\midscene_run`，在已有服务管理权限的 PowerShell 中执行：

   ```powershell
   python -m py_compile D:\sonic\midscene_run\windows-midscene-runner.py
   Restart-Service -Name MidsceneWindowsRunner
   Get-Service -Name MidsceneWindowsRunner
   ```

   若服务使用其他路径，以实际配置为准。直接启动则使用原已配置环境的命令 `python windows-midscene-runner.py`，避免与现有服务重复启动；不新增配置或打印凭据。

4. Mac 部署副本须同步本次完整已审查的 `mac-midscene-runner.py`，包含能力标记和 EPERM 停止复核修复，不能只手工添加能力标记；沿用原启动方式。在服务器可信 Runner 心跳中确认上述三项能力与明确设备在线状态；服务运行和心跳能力不是设备功能验收。

## 真实 Windows 验收待完成

本次开发主机是 macOS，Win32 ABI 和生命周期通过模拟验证，真实父子进程测试只验证本地 guard 闸门和私有进程组模拟的 Job 清理，不能声称 Windows Job Object 实测通过。上线前仍需在原 Windows 服务账户和实际 Python/Node/Midscene 环境检查：

- 正常成功、真实步骤进度和报告；超时和取消的停止回调。
- guard/CLI 已退出但后代仍在运行，及忽略普通停止信号的子进程；停止后 Job 活动进程数为零。
- 所有权分配失败时真实 CLI 从未启动，包括已有外层 Job 的 NSSM/服务环境中的嵌套 Job 约束。
- Runner 强制终止时无 Job 句柄继承，所有自有后代停止；其他 Runner/设备会话不受影响。
- 中文及空格路径、原有 `midscene.cmd` 启动方式、参数与真实退出码保持一致。
- 终止/查询/关闭失败时回调 false，服务器保留不确定租约；不伪造通过、不修改 YAML 或断言。

Win32 依据：[Job Object](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[分配进程](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)、[扩展限制结构](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information)、[基本限制](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)、[基本记账结构](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_accounting_information)、[查询 Job](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject)、[终止 Job](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)。
