# iOS 应用版本和平台执行闭环

**目标：** 两台已授权 iPhone 同时报小白学习/智小白3D真实版本，从现有平台创建任务并由 Mac Runner 执行、回传证据。
**设计：** 复用现有 Mac Runner 结果/报告链路；缓存应用元数据；明确 UDID 的临时 USB 转发贯穿单次执行。只转换运行副本的应用启动包和连接，源资产不改。不接自动分配、APK安装或不支持的 Android 专用动作。每台设备独立 WDA 就绪检查，失败明确显示并阻止执行。
**备选取舍：** 仅改标签会掩盖缺失执行链路；另写一套 iOS 用例不满足复用要求，故接现有任务分发与结果回传。

- [ ] 版本：tests/test_mac_ios_device_snapshot.py 先复现只报单应用；配置映射并缓存读取两应用；js/app.js 两行显示，Node回归。
- [ ] 执行：tests/test_ios_case_reuse.py 验证语义动作保留、拒绝未知平台动作、精确设备调度、WDA不可用、超时/取消清理；实现执行副本、动态绑定转发、真实就绪状态。
- [ ] 检查：Python专项、主链/Runner语法、backend_static_checks.py、frontend_static_checks.py、Node卡片及git diff --check。
- [ ] 发布：推送当前隔离工作树，原部署脚本发布，更新本机Runner；健康版本、两台双应用版本和Chrome状态验收。
- [ ] 实机：先使用无打印/删除的智小白3D导航断言用例，平台创建任务→指定iPhone→Runner结果→报告证据；记录失败原因，不伪报通过。
