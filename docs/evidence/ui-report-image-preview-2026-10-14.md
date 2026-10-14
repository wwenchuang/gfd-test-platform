# 执行证据图片预览与下载修复

## 线上根因证据

2026-10-14，通过当前管理员账号读取第三轮已完成报告，未重新执行手机用例。

- 批次：`uib_b0202cd57fa74ff89bf9380da1c752de`。
- 截图1：`125f5fb5224fd366a70ea446b1389e3517aad0f3181c0b7e27592220b9caf5df`；HTTP200，image/jpeg，392190字节，1170×2532。
- 截图2：`905f7008db8dd2b5515ab879728dbb3f242051c399b7350633de0aec308ef26e`；HTTP200，image/jpeg，395131字节，1170×2532。
- 报告附件：`f8160d3749b8c37766e6ba31bd38ca096cebb32400d7e59a2c335ce0809bfc77`；HTTP200，text/html，617字节，静态清理内容。
- 原响应已有正确JPG后缀，但完整哈希作为文件名；下载反馈裸显哈希。前端没有图片预览入口。两份JPG在Chrome createImageBitmap解码成功，第一份本机view_image显示完整首页。

## 修改与验证

- 图片附件单独展示，添加预览/放大/下载；HTML明确作为网页附件。
- 文件名改为中文类型加短标识，保留实际格式扩展名；不改变附件身份、原始像素、服务端授权。
- 预览使用登录请求，不将token放进URL；只读同源私有图片路由，匹配MIME，限制8MiB和15秒，关闭及换账号释放资源。
- 125项Node、67项Python、63项后端静态、84项前端静态及语法/差异检查通过。
- 独立审查发现并修复：弹窗下载反馈需显示在弹窗内；新报告版本应保留预览按钮焦点。
- 本地Chrome候选页面用线上真实JPG验证完整预览、原宽放大滚动、390像素布局、Escape/焦点恢复。无操作不加载图片，开一次预览只发一次图片请求。已恢复本地验证视口。

本地证据目录：`/tmp/codex-ui-simple-20261013/`，其中 `evidence-preview-local-desktop.png`、`evidence-preview-local-mobile.png`、`evidence-preview-local-zoom.png`、`evidence-node-final.txt`、`evidence-python-final.txt`、`evidence-backend-final.txt`、`evidence-frontend-final.txt`。

## 部署状态

用户部署 `f136af41d26a8a47c8f16ae911af94f48f8c8ab2` 后继续线上复验。现场发现全站 `css/app.css` 的通用 margin:0 重置了原生弹窗默认居中；以 `c3828e0d93df6a64005fa81ccd256c6de65b1afb` 补上 margin:auto，通过 Chrome 堡垒机执行标准更新脚本，终端明确“部署完成：c3828e0”。

最终验收（2026-10-14 12:33–12:36 北京时间）：

- health精确匹配c3828e0；5个HTML/CSS/JS完整字节匹配提交，CSS使用 evidence-preview-v2。
- 两张线上截图预览均正确解码为1170×2532；可放大至原始1170像素宽并滚动。
- Chrome实际下载 `执行截图-125f5fb5224f.jpg`（392190字节）及 `执行截图-905f7008db8d.jpg`（395131字节），与原始下载字节完全一致，文件类型为JPEG且本机可打开。
- `原始报告静态内容-f8160d3749b8.html`下载为HTML，界面明确与图片分区。
- 最终重新加载页面清除候选样式注入后，桌面与390像素窄屏的弹窗中心误差都为0；窄屏弹窗宽356且无横向溢出。Escape关闭恢复预览按钮焦点。
- 打开报告/展开证据不加载图片；点击一次预览产生1次图片请求，经过正常报告轮询和视口变化仍只有1次。关闭后弹窗移除。浏览器恢复1634×1190。
- 未登录访问该私有截图返回401。当前账号实测，不替代全平台两账号验收；未操作手机或重新执行用例。

最终证据：`evidence-live-final-deployment.json`、`evidence-live-downloads.json`、`evidence-live-final-desktop.png`、`evidence-live-final-narrow.png`、`evidence-center-deploy-final.png`，均位于上面的本地证据目录。
