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
