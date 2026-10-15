# 私有上传录屏服务部署

本功能与 Sonic 连接手机操作录制相互独立。复用现有认证、AI Gateway、中文草稿和试跑/发布门禁，无需更新 Runner。

## 服务器依赖与部署

服务账户必须能执行 ffmpeg 与 ffprobe（建议同版本；兼容实际部署 FFmpeg 4.4.2 与本机测试 FFmpeg 9。解码前读取真实 ffmpeg -h full 能力：4.4 使用 -vsync vfr，支持 -fps_mode 的版本使用 -fps_mode vfr，避免FFmpeg9已移除旧参数的问题）。Ubuntu/Debian：

```sh
sudo apt-get update
sudo apt-get install -y ffmpeg
ffmpeg -version
ffprobe -version
```

依赖 Python Pillow（仓库现有 requirements）。AI Gateway 必须部署本次 ai_skills/prompts/ui_video_steps.v1.md 与 ai_skills/schemas/ui_video_steps.schema.json；采用已有视觉路由、账户凭证及并发限制。平台 run_ai_skill 按 AI_VISION_IMAGE_LIMIT 分批，至少需要每批2张图片；推荐维持既有配置。无需新增模型密钥。

在既有平台服务的环境配置中可设置：

```sh
UI_VIDEO_FFMPEG=/usr/bin/ffmpeg
UI_VIDEO_FFPROBE=/usr/bin/ffprobe
TASK_UI_VIDEO_ROOT=/var/lib/midscene/ui-video-sources
```

未指定工具路径时从 PATH 查找；本机开发另兼容 /opt/homebrew/bin。未指定存储根时位于 UI 草稿数据库同级 ui-video-sources。服务账户独占目录，目录700、SQLite600，建议所在卷保留至少3GB空间。存储不得配置为Web静态目录。备份应同时包含私有来源目录、草稿SQLite和既有正式用例元数据。

按标准流程更新代码并重启平台服务和 AI Gateway；本次只新增视频服务，不改变 Runner、设备选择或普通/发版执行语义。入口受 UI_SIMPLE_FLOW_ENABLED 与既有 ui.view/ui.edit 权限控制。

## 有界工作与恢复

上传以 raw binary 串流写入，不经过200MB JSON/base64。单视频200MB/10分钟；一个服务器进程内最多一个解码/模型工作任务，队列总计最多4条；最多100个保留来源、总磁盘预算2GB（接纳时计实际文件及活动解码预留）。上传串流同时仅一个、总时长120秒，每次网络读取30秒超时，超限拒绝后关闭连接。

部署使用既有单个主服务器进程；不能启动多个视频消费者进程共享该目录。任务状态保存在SQLite。重启后的 queued/decoding/analyzing 变为 interrupted，用户明确重试才继续；不自动派发手机。取消会中止解码进程组，模型请求已经发送时允许既有网关请求结束，但取消版本不会提交结果。重试是新识别版本，已导入草稿禁止覆盖重试。

每个解码子进程CPU120秒、墙钟150秒（probe/末帧20秒）、单线程、输出文件32MB上限。Linux虚拟内存2GB；macOS开发环境通过RSS监测512MB上限。两遍扫描完整时间线，先合并相邻变化帧为不超过2.5秒的短转场，再保留前帧/首帧/稳定末帧与视频结尾。60帧预算、持续快速变化无法可靠覆盖时提示分段，不返回前半段冒充全部。黑屏和静止不会生成动作。模型只给待核对建议；可见检查候选不能代替业务预期。

未关联草稿的终态视频7天后清理；崩溃上传孤儿目录24小时后清理。已开始导入的来源固定保留，包含正式用例引用的原视频/帧不会被临时清理删除。保留来源达到预算时明确拒绝新上传，需要管理员在核对引用并备份后归档，不能直接 rm 已引用素材。

## 媒体预览

视频、帧、详情、取消、重试和导入都检查当前账号、应用及资源归属。浏览器用认证 fetch 获取完整视频 Blob 后播放；当前没有服务端HTTP Range接口，不宣称渐进播放或边下边拖动。离页和账号/应用切换会销毁对象URL并忽略迟到结果。任务列表读取不会启动每卡轮询，仅当前可见任务每5秒一次，最多120次，之后手动刷新。

## 部署后验收

先核对 ffmpeg/ffprobe 服务账户可执行与资源版本，再真实上传原生录屏、观察分析终态、预览媒体、核对时间片段、保存编辑草稿后重开导入。三类真实素材（无触点导航、确认/取消弹窗、输入/滚动/快速转场）还需真实视觉模型和平台手机试跑，另做两账号/跨应用、取消/重启、窄宽屏与并发p50/p95及CPU/内存对照。合成解码及mock模型单测不能代替这些验收。
