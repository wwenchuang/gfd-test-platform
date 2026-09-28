# Runner 实时步骤进度验证与发布

## 改动

运行中任务先于排队任务，历史与待处理不再挡住正在执行的卡片。展示已结束/通过/失败用例数量、当前用例名、步骤序号、动作名称与可展开步骤列表。状态使用文字+符号+颜色，窄栏换行；自动刷新保留展开状态。

步骤来自真实 ScriptPlayer.taskStatusList/currentStep，非计时模拟。已完成步骤比例是步骤数量比例，不是剩余时间预测；失败/取消不把后续节点画成完成。大于100用例、每例200步或总计1000步时截断展示并说明，不显示可能误导的总体百分比。

## 验证

- Node专项14项：置顶顺序、真实renderJobs顺序、旧Runner降级、失败后续灰色、取消/超时、HTML转义、继续执行下一例、长任务截断、观察器结果/错误/计时器保持。
- Python22项：新增5项（快照白名单与边界、无效帧、取消后迟到回调、真实Node中文及空格目录），并回归Runner状态合并、依赖失败归因、设备截图和TLS默认行为。
- 真实 @midscene/core@1.13.0 内核通过 Python execute_midscene 子进程执行3轮：三步成功（2次快照上报）、第二步失败（最终快照1次）、失败后继续第二例（2次快照）。确认终态、失败步骤、后续用例名称、stdout不混入内部帧。短于2秒的动作可能在下一次上报中直接显示最终状态，未提高已有上报频率。
- 可复现：安装官方npm `@midscene/core@1.13.0` 和 `@midscene/cli@1.13.0` 到独立临时目录后，执行 `python tests/runner_real_core_progress_check.py <该目录>`。本次使用实际npm core和CLI包元数据；设备适配器为本地桩，无手机操作、无模型调用。
- Chrome实际打开本地复用生产CSS/渲染函数的fixture：360px和280px宽度、运行中→失败、前4步绿/第5步红/后3步灰、展开后状态刷新仍展开、当前用例名及步骤4/8可见。没有执行生产业务；临时页与本地服务已关闭。
- 后端静态63项、前端静态84项、Runner/router/services和Agent/YAML主链编译、git diff --check通过。
- 独立只读复审发现并修复：早期失败抢占后续正在运行的用例；截断列表错误接近100%；NODE_OPTIONS中文路径转义导致启动失败。均有回归证据，最终复审无阻塞项。

## 部署与启动

1. 服务器拉取本次提交后沿用 `cd /opt/midscene-task-platform-src`，运行 `bash deploy/update-main-server.sh`。前端CSS/JS版本键已更新。
2. Windows等正在执行的任务结束后，停止旧Runner，备份并替换原目录下的 `windows-midscene-runner.py` 为本提交版本。沿用原有 winRunner.bat、token、平台地址和工作目录启动，不重装/升级CLI，不改凭据。
3. 新Runner版本后缀应为 `step-progress-v1`。观察脚本随Python内嵌，执行任务时写入自己的任务目录，不需要另拷JS，不要求修改Sonic Agent。
4. 支持已核查的Midscene CLI/core 1.13.x；未知版本保留原执行，仅降级显示用例级进度。上线后确认实际CLI版本和步骤快照再验收。
5. 平台单独更新仅生效排序/界面；步骤级实时数据需要新版Windows Runner。新建任务运行时查看当前用例、当前动作、步骤节点；检查成功、中途失败、取消及重新运行。上线真实手机回放尚未做。

## 兼容与性能边界

不修改YAML、不拆分CLI任务、不增加AI调用。500ms只读检查有界状态，变化时才输出快照，通过原2秒HTTP上报节流上传；前端沿用原刷新。存在少量读取/JSON序列化和网络载荷成本，未声称性能零开销，也未展开已暂停的性能专项。

## 官方依据

https://midscenejs.com/yaml-script-runner

源码核查来自官方npm的CLI1.13.0 printer.ts/yaml-batch-executor.ts及core1.13.0 ScriptPlayer。非TTY控制台不持续输出每一步，因此不能用普通日志文案推断真实步骤。
