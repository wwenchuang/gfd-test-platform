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

本次补丁尚未部署。堡垒机登录/终端过期，等待用户在本机恢复。代码检查和本地浏览器验收不代替线上验收。恢复后：标准更新脚本部署，确认health SHA与资源版本；在同一真实报告核对两张图片预览/下载后打开、中文文件名和HTML类型说明。无需为此重新运行或删除业务数据。
