# 平台 API 与浏览器冒烟

PR 执行一次。夜间任务在 UTC 18:15（北京时间次日 02:15）对默认分支先运行前后端静态检查，再连续执行三次冒烟，每次新建 Chromium 进程与上下文，任何一轮失败即阻断该 job。手动选择 `wrong-title` 可复现单一错误断言，预期工作流显示失败。PR 合并拦截需仓库管理员将 Platform smoke / smoke 加入受保护分支的必需检查。

每次从 checkout 的 revision 安装 `requirements.lock` 内锁定的 Python 包、根 `package-lock.json` 的 Playwright 及配套 Chromium headless shell。真实 TaskHTTPHandler 仅绑定回环随机端口，身份和操作存储指向临时目录。API 检查健康响应状态、ok 与本轮 revision，以及未登录访问 /api/auth/me 被拒绝的状态和业务码。浏览器通过同一个 HTTP 服务访问仓库登录页，读取可见标题并记录截图与 trace。服务没有运行调度器、设备、业务数据、真实账号或模型调用。

本地等价复现使用 macOS 或已有 Chromium 运行库的 Ubuntu、Python 3.10、Node 22+、npm。当操作系统缺少浏览器运行库时，先按 Playwright 的 Chromium 安装说明配置。证据目录必须全新，放在 checkout 外。将 REVISION 换为要检验的 Git 提交。

```bash
git worktree add --detach /tmp/week6-checkout REVISION
cd /tmp/week6-checkout
W06_PYTHON=python3.10 bash tests/week6_ci/run.sh /tmp/week6-pass pr
W06_PYTHON=python3.10 bash tests/week6_ci/run.sh /tmp/week6-fail wrong-title
```

运行目录包含 `run.txt`、`pipeline.log`、`status.txt` 和逐轮的 `api.json`、`ui.log`、场景截图与 trace。安装失败时保留已产生的日志。正常完成退出 0，错误断言退出 1，浏览器或证据异常在 smoke 阶段退出 2，安装失败保留工具原始非零退出码。GitHub Actions 总是尝试上传现有证据，运行详情页可下载对应 artifact，保留 14 天；checkout 或 setup 尚未进入脚本时查该步骤的 Actions 日志。本地执行后直接查看指定目录。

Python 冒烟只安装本处理器导入和证据验证所需的包。锁文件可用 `uv pip compile --python-version 3.10 --universal --no-annotate --no-header tests/week6_ci/requirements.in -o tests/week6_ci/requirements.lock` 更新，再对新锁执行正反两次验收。
