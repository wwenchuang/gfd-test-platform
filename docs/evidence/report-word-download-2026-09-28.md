# 2026-09-28 测试报告 Word 下载检查

## 确认的问题

Chrome 对报告 tpr_1790586894692_00002 点击“下载 Word”后，实际文件是 HTML（起始为 doctype/html），旧实现将其写成 report.doc，并以 application/msword 返回。前后端均无条件追加“_测试报告”，导致标题已有后缀时重复。

此次浏览器另将下载放入 Playwright 临时 artifacts 目录并显示 UUID；下载提示的业务文件名正常。这是浏览器遗留下载设置，尚未恢复/验证，不能把 UUID 问题归为文件生成逻辑。

## 修改

- 用既有 python-docx 依赖生成真正的 OOXML 文件；Word 响应按二进制发送，使用 DOCX MIME 和扩展名。
- 保留 format=doc/word 旧链接兼容，旧报告从保存的 Markdown 快照转换，不修改历史数据或执行结论。
- Word/HTML/Markdown 标题已有“测试报告”时不重复追加。
- 中文字体、分页表头、列宽、转义竖线和各节编号处理；不依赖外部资源加载正文。

## 验证证据

- 专项 Python 34 项、Node 9 项通过。
- 真实本地 HTTP 使用生产下载函数和 send_attachment：检查 MIME、Content-Disposition 中文名、Content-Length、下载字节等于保存文件、DOCX 可解析。该适配器不覆盖完整登录鉴权。
- Chrome 下载的实际报告转换为 DOCX，用捆绑 LibreOffice 渲染成三页，逐页核对中文、统计表、执行证据及质量结论，无裁切或乱码。为了渲染环境识别 macOS 中文字体，仅设置临时 fontconfig，不改系统配置。
- 独立只读复审两项排版问题已修复并复核无明确阻塞。
- 后端静态 63、前端静态 84、相关 Python 编译、git diff --check 作为提交门禁。

## 边界

没有使用 Microsoft Word/WPS 桌面应用打开，也没有部署补丁后的生产按钮验收。发现共享 Chrome 页面被操作后停止切换。没有创建或改写生产报告/用例/设备数据，没有控制手机或输入部署终端。

## 18:38 Chrome 下载命名恢复及 WPS 复验

- 用户再次指出 UUID 文件名，授权独占 Chrome 几分钟；明确不操作部署终端。
- 生产报告 `tpr_1790588705162_00003`（多色打印流程优化），旧下载 `5768f5f2-1197-41a5-8af5-2b4593383d25`、`43b6c58b-7fdb-4423-8911-386900ac6a2e` 位于临时 `playwright-artifacts-O1a0FX`，均为 38936 字节 OOXML，ZIP CRC 与 XML 解析通过。根因与 Chromium `allowAndName` 的 GUID 命名一致。
- 原生 Chrome UI 打开临时 about:blank 的 DevTools Protocol Monitor。Tab 目标不支持 Browser.setDownloadBehavior；切到 Main 页面后发送 `Page.setDownloadBehavior`、参数 `{"behavior":"default"}`，筛选返回 `{}`（1 ms）。该恢复不依赖生产代码上线。
- 从原生产报告点击下载 Word，正常弹出“保存”框。保存到默认下载目录；Chrome 下载记录新增 `多色打印流程优化-测试报告.docx`，Finder 种类为 Microsoft Word document (.docx)。旧 UUID 行保留，没有改名或删除掩盖问题。
- 新文件 `/Users/adouceshi/Downloads/多色打印流程优化-测试报告.docx` 为 38936 字节，ZIP integrity 无错误，64段文本，标题及末尾发布建议与报告一致。
- Finder 打开新文件至 WPS，实际两页、1226字；检查首页标题/基本信息、跨页正文、用例统计7/7/0、缺陷2一般+1轻微=3和发布结论，能正常加载与阅读。WPS 提示缺少字体，警告emoji旁的变体选择符显示方框，字体兼容性尚未完全验收；未验证 Microsoft Word。
- 关闭临时 Protocol Monitor 实验及诊断标签；未关闭用户其他标签/文档，未改业务报告。
- 平台成功 toast 仅表示请求已发起，修改为“已发起下载：文件名，请在浏览器下载记录中确认结果。”；4项下载交互、84项前端静态、JS语法、diff检查通过。此文字改动待部署，不影响已经恢复的浏览器下载行为。

官方参考：
- https://chromedevtools.github.io/devtools-protocol/tot/Browser/#method-setDownloadBehavior
- https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-setDownloadBehavior
- https://developer.chrome.com/docs/devtools/protocol-monitor
