# Sonic 2.7.2 Android 远控清晰度

当前远控视频由 **Windows Sonic Agent** 启动手机上的 scrcpy 1.23 编码，再送到 Sonic Web；Linux Sonic 中心服务器和 Task 平台不重新编码。官方 Agent `ScrcpyLocalThread.java` 使用 `max_size=0 max_fps=60`，没有传 `bit_rate`，因而采用 scrcpy 1.23 默认的 8 Mbps。仓库中的 `sonic-agent-video-quality-v2.7.2.patch` 仅把该参数设为 16 Mbps，保持原始分辨率和帧率。

在 Windows Agent 主机安装 JDK 17、Maven 和 Git，确认 `java -version`、`mvn -version` 都能运行。取得本仓库中的补丁文件并放到 `C:\Temp\sonic-agent-video-quality-v2.7.2.patch`，然后在 PowerShell 中执行：

```powershell
git clone --branch v2.7.2 --depth 1 https://github.com/SonicCloudOrg/sonic-agent.git C:\Temp\sonic-agent-v2.7.2-src
cd C:\Temp\sonic-agent-v2.7.2-src
git apply --check C:\Temp\sonic-agent-video-quality-v2.7.2.patch
git apply C:\Temp\sonic-agent-video-quality-v2.7.2.patch
mvn -DskipTests package
```

构建输出应为 `C:\Temp\sonic-agent-v2.7.2-src\target\sonic-agent-windows-x86_64.jar`。只有构建命令退出码为 0 且文件存在时才执行替换。先结束当前 Sonic Agent 进程或服务，再备份并替换现有 JAR：

```powershell
$agentDir = 'D:\sonic\sonic-agent-v2.7.2-windows_x86_64'
$jar = Join-Path $agentDir 'sonic-agent-windows-x86_64.jar'
Copy-Item $jar "$jar.bak" -ErrorAction Stop
Copy-Item 'C:\Temp\sonic-agent-v2.7.2-src\target\sonic-agent-windows-x86_64.jar' $jar -Force -ErrorAction Stop
```

使用原来的启动方式重新启动 Agent；不要同时启动两个实例。重新打开手机远控页以建立新的视频会话。只更新 Task 平台不会改变已安装的 Windows Agent 画质。

验收应在同一手机、同一浏览器缩放下对比远控页的文字边缘和动态画面，并确认点击、滑动延迟没有明显增加；若网络带宽不足，恢复备份 JAR。平台用于步骤识别的 ADB PNG 仍保持原始分辨率，不受此参数影响。
