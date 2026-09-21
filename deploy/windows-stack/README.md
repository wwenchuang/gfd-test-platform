# Windows 手机测试一键启动

把本目录完整复制到连接手机的 Windows 电脑，先解压再运行。需要现有 Python 3.8+（Windows Runner 已依赖 Python）、Windows 自带 PowerShell，以及原来已能运行的 Sonic Agent、Windows Runner 和 FRP；本工具不下载软件、不修改密码、不改变 Windows 执行策略（只用系统 PowerShell 查询进程和管理现有服务，不执行 .ps1）。

## 第一次使用

双击 `start.cmd`。已按你提供的截图预填以下路径；全部存在且 Java 可用时直接使用，文件移位或缺失时才打开中文选择窗口：

- Sonic：`D:\sonic\sonic-agent-v2.7.2-windows_x86_64\sonic-agent-windows-x86_64.jar`
- Runner：`D:\sonic\midscene_run\windows-midscene-runner.py`

录制功能要求 Windows Runner 使用仓库内带 `recording-evidence-v1` 版本标识的脚本。它只在收到平台心跳中的录制证据请求后，对用户明确选择且当前由本 Runner 上报在线的 Android 手机采集一张截图和一次页面结构；不会重放 Sonic 已执行的点击、滑动或输入。业务打印机编号不能作为录制手机。替换脚本后需重启 `MidsceneWindowsRunner` 服务，并在平台“执行环境”确认版本号已更新。
- FRP：`C:\frp\frpc.exe` 和 `C:\frp\frpc.toml`

需要选择时依次确认：

1. 原 Sonic Agent 的 `.jar`（推荐）或 `.bat` / `.cmd` 启动文件。JAR 使用现有 Java；PATH 中没有 Java 时会询问 java.exe 位置。
2. `windows-midscene-runner.py`，或原来设置运行环境的 `.bat` / `.cmd` 文件。如果已有 `MidsceneWindowsRunner` 服务，则直接复用该服务，不询问脚本。
3. `frpc.exe`。
4. 与它配套的现有 `frpc.toml` / `frpc.ini` / YAML / JSON 配置文件。

直接运行 Python 文件时使用当前 Python 和继承的本机环境，不会从旧批处理或服务注册表提取令牌。如果现有 Runner 服务存在，优先复用服务中保存的环境；否则直接运行前检查 `MIDSCENE_RUNNER_TOKEN` 是否存在且不是弱默认值。缺少时会提示，可以改选原来配置环境的 `winRunner.bat`。不覆盖原程序，也不运行安装脚本或强制重启脚本。所选文件应是你此前使用的可信文件。

默认检查已核实的远控入口 `101.34.197.12:17789`。如果换了服务器，运行 `configure.cmd` 后按实际入口修改 `stack.local.json` 中的 `RemoteHost` / `RemotePort`，这里应为 Sonic Agent 中登记的远控地址，不是 Sonic 网页端口 3000。

选择后配置保存在同目录 `stack.local.json`，只有文件路径和远控检查地址，不复制原配置的令牌。取消任一选择不会保存不完整配置或启动程序。文件移动以后双击 `configure.cmd` 重新选择。

## 以后使用

- `start.cmd`：检查文件，启动缺少的组件，已有实例则跳过。启动过程按 Sonic → FRP → Runner 进行；单项失败会继续检查其余项并返回非零结果。
- `status.cmd`：只检查，不启动、不重启，检查进程和远控 TCP 入口。
- `configure.cmd`：重新选择文件，不启动程序。

直接启动的 Sonic JAR、Runner Python 和 FRP 均后台运行，标准输出/错误分别保存在 `logs/sonic-时间.*.log`、`logs/runner-时间.*.log`、`logs/frp-时间.*.log`。关闭本次启动工具窗口不会主动关闭这些程序。若选择旧批处理，则保留它们原来的窗口和日志方式；已有 Runner 服务继续用服务自己的日志。日志可能包含业务/网络信息，分享前自行遮盖敏感内容；工具不把日志上传到任何地方。

看到“进程已运行”不等于服务已就绪。远控 TCP 检查只证明入口可连接，最终应在 Safari 打开 Sonic，选择手机并确认真实画面显示。本工具不会替你点击手机、领取任务或调用 Runner 的取任务接口。

## 常见结果

- **FRP 已运行，远控端口不通**：检查 FRP 日志和原配置。认证不匹配需要核对两端现有认证信息，工具不会自动替换令牌。端口也可能正在注册或被网络阻断。
- **已有其他 FRP 配置 / 无法读取进程信息**：为避免重复占用端口，停止新增该组件；确认原进程或用具有读取权限的账户重新检查。
- **Runner 服务无法启动**：需要有启动该服务的权限；工具不会自动提权。若已有运行服务，保持它运行。
- **文件路径包含 `%`、`!` 或双引号**：CMD 会展开这些字符，工具拒绝启动；将启动文件放到不含这些字符的目录。中文和空格路径可以使用。
- **无法读取 Windows 进程/服务状态**：确认当前账户能使用系统 PowerShell 和 CIM 查询。工具会停止启动，不能把查询失败当成没有进程；不会绕过安全策略或自动提权。

本版为按需一键启动，不安装自启服务，也不自动杀进程。电脑重启或注销后需重新运行。长期开机自启应在这套启动方式通过真机验收后，再按原运行账户配置，避免改变用户环境导致 ADB/Node 不可用。

本地逻辑和语法检查不等于 Windows 真机验收。首次运行后保留中文结果窗口，核对 FRP 日志，再在 Safari 验证手机画面。
