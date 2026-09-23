# Sonic 2.7.2 Android 远控清晰度

当前远控视频由 **Windows Sonic Agent** 启动手机上的 scrcpy 1.23 编码，再送到 Sonic Web；Linux Sonic 中心服务器和 Task 平台不重新编码。官方 Agent `ScrcpyLocalThread.java` 使用 `max_size=0 max_fps=60`，没有传 `bit_rate`，因而采用 scrcpy 1.23 默认的 8 Mbps。仓库中的 `sonic-agent-video-quality-v2.7.2.patch` 仅把该参数设为 16 Mbps，保持原始分辨率和帧率。

Windows 未安装 Git 时，直接用浏览器打开 `https://github.com/SonicCloudOrg/sonic-agent/archive/refs/tags/v2.7.2.zip`，把 ZIP 解压到 `C:\Temp`。应看到 `C:\Temp\sonic-agent-2.7.2\pom.xml`。另需安装 [Temurin JDK 17](https://adoptium.net/temurin/releases/?version=17&os=windows&arch=x64) 和 [Apache Maven](https://maven.apache.org/download.cgi)。安装 JDK 时启用 PATH 和 JAVA_HOME；Maven 下载 Binary zip，解压后将其 `bin` 目录加入 PATH。重新打开 PowerShell 后确认 `java -version`、`javac -version` 和 `mvn -version` 都能运行，且 Java 为 17。

在 PowerShell 中执行下面的源码修改。它只替换唯一一处 scrcpy 启动参数；找不到原文或出现多处匹配就停止，不写文件：

```powershell
& {
  $src = 'C:\Temp\sonic-agent-2.7.2\src\main\java\org\cloud\sonic\agent\tests\android\scrcpy\ScrcpyLocalThread.java'
  $old = 'max_size=0 max_fps=60 tunnel_forward=true'
  $new = 'max_size=0 max_fps=60 bit_rate=16000000 tunnel_forward=true'
  $content = [System.IO.File]::ReadAllText($src)
  if ($content.IndexOf($old) -lt 0 -or $content.IndexOf($old) -ne $content.LastIndexOf($old)) { throw '源码版本或参数不匹配，未修改文件' }
  [System.IO.File]::WriteAllText($src, $content.Replace($old, $new), [System.Text.UTF8Encoding]::new($false))
  Select-String -Path $src -Pattern 'bit_rate=16000000'
}
```

看到 `bit_rate=16000000` 后再单独执行构建：

```powershell
cd C:\Temp\sonic-agent-2.7.2
mvn -DskipTests package
```

构建输出应为 `C:\Temp\sonic-agent-2.7.2\target\sonic-agent-windows-x86_64.jar`。只有 Maven 显示 `BUILD SUCCESS` 且文件存在时才执行替换。先结束当前 Sonic Agent 进程或服务，再备份并替换现有 JAR：

```powershell
$agentDir = 'D:\sonic\sonic-agent-v2.7.2-windows_x86_64'
$jar = Join-Path $agentDir 'sonic-agent-windows-x86_64.jar'
$built = 'C:\Temp\sonic-agent-2.7.2\target\sonic-agent-windows-x86_64.jar'
if (-not (Test-Path $built)) { throw '构建 JAR 不存在，未替换 Agent' }
Copy-Item $jar "$jar.bak.$(Get-Date -Format yyyyMMddHHmmss)" -ErrorAction Stop
Copy-Item $built $jar -Force -ErrorAction Stop
```

使用原来的启动方式重新启动 Agent；不要同时启动两个实例。重新打开手机远控页以建立新的视频会话。只更新 Task 平台不会改变已安装的 Windows Agent 画质。

验收应在同一手机、同一浏览器缩放下对比远控页的文字边缘和动态画面，并确认点击、滑动延迟没有明显增加；若网络带宽不足，恢复备份 JAR。平台用于步骤识别的 ADB PNG 仍保持原始分辨率，不受此参数影响。
