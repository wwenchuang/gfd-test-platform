# iOS 设备卡片与当前画面

## 原因和范围

- 原 Mac Runner 未持续注册 iOS 设备，也未消费平台 snapshot_requests；Sonic 在线不会自动产生平台 Runner 卡片。
- 服务端 normalize_device_list 丢弃 platform/ios_version，前端系统文案固定为 Android。
- 设备发现曾误用单个执行目标白名单，只显示 iPhone13，遗漏另一个 USB 连接设备。展示现已覆盖所有 USB iOS 设备；本补丁的 iOS 模式仅预览，不领取任务。
- iPhone14,5 和 iPhone16,2 是硬件产品编号。通过本机 Xcode devicectl 查询 marketingName，实际为 iPhone 13、iPhone 15 Pro Max；标题使用正式名称，保留硬件编号 model。

## 数据与性能

使用 idevice_id 发现 USB 设备，ideviceinfo 读取系统版本、电量，ideviceinstaller 只查询配置的应用。元数据缓存60秒，断开即删除缓存；心跳不获取模型密钥、不发起推理。平台按需刷新截图，WDA 通过每次独立、指定 UDID 的 loopback USB 转发读取 PNG 和前台应用；转发结束即回收，不连接默认设备，不修改手机页面。失败上报明确错误，保留旧图及其原始采集时间。

现有源应用 com.kfb.model 仅用来匹配平台应用；iOS 实际安装版本从 com.kfb.3d 读取，不冒充 Android 包。

## 验证边界

已读到两台手机的信息：iPhone13/iOS26.6、iPhone15ProMax/iOS26.6.1，两台智小白3D均1.19.7(70)。iPhone13实际取得1170x2532截图；生产 Chrome 点击刷新后已显示其真实截图、时间、电量和前台包名，旧生产尚把它显示成“安卓”。第二台首次WDA初始化超时，用户解锁后重试成功，WDA返回有效截图（Base64长度2875480）。不能把截图上传成功当作自动化用例回放通过。

## 部署

本批包含服务端字段保留、只预览Runner分发保护及前端系统标签，需要服务端与静态文件同时更新；Windows Runner不用替换。Mac使用更新后的mac-midscene-runner.py，私有环境配置保留现有TASK_SERVER、RUNNER_ID、MIDSCENE_RUNNER_TOKEN，并设置MIDSCENE_RUNNER_PLATFORM=ios、IOS_APP_BUNDLE_ID=com.kfb.3d、IOS_SOURCE_APP_PACKAGE=com.kfb.model。依赖idevice_id/ideviceinfo/ideviceinstaller/iproxy/xcrun可在PATH找到。WDA必须已在对应手机上运行；预览失败不会在心跳中反复构建或启动WDA。

从包含该提交的main更新服务器：`cd /opt/midscene-task-platform-src && bash deploy/update-main-server.sh`。部署前不要把iOS预览Runner作为可执行设备上线。未来iOS回放适配通过独立验收后才能解除job_execution=false，本补丁没有交付iOS回放能力。
