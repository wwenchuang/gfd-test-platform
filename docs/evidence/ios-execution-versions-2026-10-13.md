# iOS 双应用版本与执行验收

## 范围与版本
- 两台USB手机：iPhone 13 / iPhone 15 Pro Max，系统26.6 /26.6.1。
- 两台智小白3D com.kfb.3d 1.19.7(70)、小白学习 com.xbxxhz.box 4.50.2(424)。Runner现在缓存读取两包；Chrome切换小白学习已确认两张卡片显示4.50.2(424)。同屏展示两版本前端待服务器发布。
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
- 堡垒机终端会话已结束，用户登录恢复待完成。平台创建任务→Runner领取→报告回传尚未验收，不把本机CLI成功当作平台闭环。
