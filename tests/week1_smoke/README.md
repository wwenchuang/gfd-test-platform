# 第 1 周：只读接口与匿名页面冒烟

在服务器独立目录执行；不会提交登录、操作设备、创建业务数据或调用模型。网络必须能访问业务域名，页面服务在服务器本机 `127.0.0.1:8088`。该示例验证反馈分类只读接口和平台登录页，并非登录后业务流程或平台 Runner 验收。请保留原平台成功报告，匿名 `4009` 是鉴权拒绝的预期行为，不是业务码 `0` 的成功结果。

## 准备

需要 Python 3、Node.js 18 或更高版本、npm。进入此目录，在隔离目录安装锁定版本及 Chromium：

```bash
npm ci --ignore-scripts
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers"
npx playwright install chromium
```

若官方浏览器下载暂不可用，可临时用 `W01_BROWSER_EXECUTABLE` 指向服务器已有的可信浏览器二进制；需要记录实际浏览器版本，并将结果标记为兼容性验证，不能称为全新浏览器安装验证。

Linux 若缺少 Chromium 系统库，先联系管理员或在隔离路径提供发行版匹配的运行库；不要擅自改变系统环境。若已有可靠的隔离库目录，可通过本次 shell 的 `LD_LIBRARY_PATH` 指向它。勿将 `.browsers`、`node_modules` 和运行产物提交仓库。

## 执行与检查

在普通 shell 中分别执行。每行后立即检查返回值（`echo $?`），不要用管道覆盖测试程序的退出码：

```bash
# 无凭据的真实请求，确认 HTTP 200 / 业务码 4009。
python3 api_smoke.py --scenario anonymous
echo $?
# 预期 0；不代表有权访问分类数据。

# 针对同一拒绝结果故意写错期望值。
python3 api_smoke.py --scenario anonymous --expected-code 999
echo $?
# 预期 1；这次失败是练习断言，并非业务故障。

# 页面标题应等于“功夫豆测试平台”。
node ui_smoke.js positive
echo $?
# 预期 0。

# 有意断言错误标题，检查进程确实返回失败。
node ui_smoke.js intentional-failure
echo $?
# 预期 1。
```

`2` 表示配置、网络、浏览器或运行环境错误，不可当作预期的断言失败；对照输出中的实际值确认错误确由期望不匹配引起。UI 会在 `artifacts/positive` 和 `artifacts/intentional-failure` 保存各自截图、trace。每次执行会覆盖对应目录中的同名文件；需要留存历史时，在每次执行前将 `W01_UI_ARTIFACTS` 设置为新的专用目录。UI 的初始地址限定为本机 `127.0.0.1` 的 `/task-manager.html`，允许通过 `W01_UI_URL` 指定不同本机端口，不接受地址中的凭据、查询串或锚点。

如果已有获授权的测试账户凭据，可通过服务端**现有安全的环境变量机制**提供完整 Authorization 头值，避免把密钥写入命令行历史或文件，然后运行：

```bash
python3 api_smoke.py --scenario authenticated
echo $?
```

这里检查 HTTP 200、业务码 0 和 `data` 为数组。缺少 `W01_API_AUTHORIZATION` 返回 2，表示认证场景未执行；只有本次带凭据请求真实返回并通过才可作为新一轮成功证据。脚本不会打印凭据、完整响应、账号或错误详情，也禁止重定向以免转发凭据。

不产生业务记录，因此不需要清理业务数据。截图与 trace 可能含页面内容，保存在受控目录并按团队保留期限处理。测试账号、响应体及原始 trace 不应随意公开。结束后记录运行命令、版本、时间、四个退出码和截图／trace 路径；凭据不写入记录。
