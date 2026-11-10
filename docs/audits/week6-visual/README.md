# W06-05 登录页间距视觉回归

2026-11-10 在本机以仓库版本 `cb79807c` 的真实 `task-manager.html`、CSS、脚本和品牌图片启动匿名登录页。没有登录、提交表单或调用业务 API。页面通过容器回环地址加载，容器关闭外网。

| 场景 | Playwright 结果 | 图像 |
| --- | --- | --- |
| 未改页面 | 1 passed，原始退出码 0。运行截图与参考 PNG 字节一致 | [首次采集的参考图](../../../tests/week6_visual/baselines/anonymous-login.png) |
| 仅将登录标识的下外边距从 40px 调至 52px | 1 failed，原始退出码 1。比较器报告 63,574 个差异像素，占 960 × 720 画面的约 9.2% | [实际图](spacing-actual.png) · [差异图](spacing-diff.png) |

参考图由 Chromium 实际渲染取得。Playwright 1.60.0、Chromium Headless Shell 148.0.7778.96、Ubuntu 24.04 和中文字体随镜像固定。视口 960 × 720、DPR 1、深色、zh-CN、Asia/Shanghai，截图禁用动画并隐藏光标。Playwright 的颜色阈值和容许差异像素均为 0。

在仓库根目录运行，需要 Docker 和 Node.js。Docker 首次拉取镜像需要额外的可用磁盘空间（此镜像本地占用约 3.43 GB）。

```sh
npm ci
tests/week6_visual/compare.sh control
tests/week6_visual/compare.sh spacing
```

第二条应返回 0，第三条演练应返回 1。两组原始截图分别写在 `test-results/week6-visual-control/` 和 `test-results/week6-visual-spacing/`。负控在同一浏览器上下文临时调整一个 CSS 属性，差异图标出了表单重新居中后移动的标题、输入框、按钮和边框。

自动检查禁止写回基线。基线或产品设计变更需要人工查看参考图、实际图和差异图，再决定是否接受新版图片。本次记录了首次采集和差异检出，人工设计验收待办。
