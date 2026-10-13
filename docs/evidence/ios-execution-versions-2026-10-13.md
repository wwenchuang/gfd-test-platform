# iOS 双应用版本与执行验收

## 范围与版本
- 两台USB手机：iPhone 13 / iPhone 15 Pro Max，系统26.6 /26.6.1。
- 两台智小白3D com.kfb.3d 1.19.7(70)、小白学习 com.xbxxhz.box 4.50.2(424)。Runner现在缓存读取两包；Chrome切换小白学习已确认两张卡片显示4.50.2(424)。同屏展示两版本前端已发布，Chrome已核对两台卡片。
- IOS_EXECUTION_ENABLED默认0，保留只预览；启用后仅接明确Runner和UDID的任务，每台WDA ready检查，动态USB转发回收，不读取自动分配队列。语义launch/aiTap/aiWaitFor/aiAssert子集可跨端复用；ADB/坐标/未适配动作明确失败，不静默丢弃。源YAML保留，运行副本仅映射应用与设备连接。

## 实测
- iPhone13本机Midscene1.13.0：启动→我的→断言我的设备/我的收藏→首页→断言新手必学/AI建模/模型导入，24.164s通过。这是本机验收，未经过平台任务下发。
- iPhone13补充冷启动前置后再次执行同一导航断言用例通过，26.80s，报告ios-2026-10-13_12-02-19-85862dd6.html。
- iPhone15第一次已有勋章详情页，launch只激活旧页面，等待底部我的失败。补充与Android一致的关闭指定应用再启动前置，不清应用数据。
- iPhone15冷启动：WDA启动成功，但应用退出回桌面；Xcode devicectl独立启动同样退出。保留两份手机崩溃日志11:58:54/12:00:38，XBThree SIGABRT，栈为FlutterBoost warmUpEngine→FlutterViewController initWithEngine→fml::KillProcess。不能把这项当作坐标/等待错误或通过；需应用构建修复或有效构建后复验。
- 私有证据目录：~/Applications/MidsceneIOS/validation/platform-execution-precheck* 和 iphone15-crashes。未删除手机日志。

## 检查与启动
- 58 Python专项、Node卡片9项、后端静态63、前端静态84、Runner/主链编译、JS语法、diff通过。
- 私有runner.env需MIDSCENE_BIN指向现有node_modules/.bin/midscene，IOS_EXECUTION_ENABLED=1；平台需更新explicit-target-v1握手后才会拉取任务。使用start-preview.command启动同一互斥入口，先停之前PID避免重复心跳。Mac重启后WDA仍需启动，不假定自启。
- 服务端命令：cd /opt/midscene-task-platform-src && bash deploy/update-main-server.sh。
- 服务器已发布3a61aac，双应用版本、WDA ready、明确设备派发均已验收；Mac Runner已更新步骤上报，正常互斥入口PID59708。

## 平台派发补充

42a0e66线上双应用版本和ready已核实。两条平台任务在领取后响应超时：创建人审计查询误用result索引，4GB历史扫描导致派发准备约94/97秒。新增2万无关成功事件回归验证VM指令数，强制既有resource索引后通过。补丁3a61aac于13:26部署完成；两条未送达任务均已取消。修复后三条真实领取响应分别0.128/0.069/0.074秒。

## Chrome + 平台 + Mac Runner 验收终态

复用平台 `AI测试/跨端导航验收20261013.yaml`，源仍为Android语义YAML，运行副本映射com.kfb.model→com.kfb.3d。指定iPhone13，六步为启动、等待我的、点击我的、断言我的设备/我的收藏、点击首页、断言新手必学/AI建模/模型导入。无打印、删除或数据重置。

| job | CLI耗时 | 结果 |
| --- | --- | --- |
| job_1791869402809_00001 | 26.282s | passed，报告上传成功 |
| job_1791869556538_00002 | 24.853s | passed，报告上传成功 |
| job_1791869629013_00003 | 26.140s | passed，报告上传成功 |
| job_1791869952629_00004 | 25.213s | passed，6/6步骤回传 |
| job_1791870134963_00005 | 23.730s | passed，6/6步骤回传 |
| job_1791870214774_00006 | 25.614s | passed，6/6步骤回传 |

前三轮发现Mac缺Windows已有步骤观察器，移植同一1.13 ScriptPlayer只读观察器；500ms本地状态采样、2s上报节流，不改变动作、不猜进度。失败最后一帧、日志协议过滤、环境隔离回归先红后绿。补丁后再跑后三轮，Chrome亲见执行中83%、第6/6步蓝点，终态六勾100%；报告实际打开Passed、6 steps、14 frames，包含页面断言与截图。

最终Mac心跳版本2026.10.13-ios-execution-v1-step-progress-v1。40项相关Python检查、后端63项、主链与Runner编译、diff检查通过。无前端修改，原窗口环境保留。临时claim诊断进程退出，恢复run-preview.py，PID59708，启动仍用start-preview.command；以后更新Runner需先确保空闲再停旧进程，保留私有runner.env。

限制：iPhone15ProMax的WDA/preflight已ready，但智小白3D冷启动有独立应用SIGABRT，未通过业务UI。小白学习仅验证双机版本上报，没有运行其业务用例。Mac重启后WDA仍需启动。执行中卡片短暂出现文本false（stdout归因展示）以及设备下拉框轮询重绘会使选择控件失焦，另列前端跟进，不能称全平台无缺陷。
