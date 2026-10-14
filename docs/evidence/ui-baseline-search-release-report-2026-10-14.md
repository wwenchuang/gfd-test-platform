# 基线搜索与发版用途报告验收

## 用户选择与范围

- 开始测试/用例库默认仅已发布基线；用例库可勾选显示未发布用例。搜索按钮、Enter、300ms输入筛选、清空均支持。
- 开始测试勾选“用于发版”，结束后自动生成正式报告。普通测试仅保存执行结果与证据。飞书通知独立可选，默认关闭。
- 不改Runner、YAML、设备选择、结果真实性和现有执行门禁；不迁移历史报告内容。

## 候选验证

- 搜索4dc3f81：136 Node、80 Python；后端63/前端84；Chrome实际候选页面按钮/Enter/清空/草稿切换和390px布局。
- 报告最终：152 Node、273 Python；后端63/前端84；适用Python/JS语法与git diff检查通过。root独立关键Python71、Node152复跑通过。
- 独立审查修复旧版幂等重试兼容与跨批次截图残留；旧请求需同可信账号/同key/完整旧规范化摘要且当前权限仍有效，禁止借此改变意图。切换测试销毁旧视图并abort图片请求。
- Chrome独立窗口、真实HTTP与服务/SQLite/worker，使用模拟设备事件的隔离fixture：普通批次uib_ffa1008cb8a24f5dafa9238c02954d2d执行完毕及证据入库后，0次DOCX渲染、0报告快照、2附件；发版批次uib_5b4ebfb5d8e0429090287f29b9a82f3b自动完成后，1次DOCX渲染、1报告快照、两批总共2Jobs及4附件。
- Word实际下载38234字节、ZIP可读；含智小白3D、测试版本1.19.7（70）、本地验证执行人、测试时间、场景及结果。图片实际预览，390px页面布局正常；普通证据切换到另一个已就绪报告，旧证据面板已移除。
- 临时证据目录：/tmp/codex-release-report-20261014。以上不是手机Runner实跑，不替代跨端稳定性验收。未发真实飞书。

## 部署保护

- 上线前生产release_revision=c3828e0d93df6a64005fa81ccd256c6de65b1afb。旧部署终端明确GitHub拉取超时，不能以“已部署”回复判断成功。
- SQLite在线备份：/opt/midscene-auth-backups/release-purpose-20261014-155617/ui-executions.sqlite3；schema4，34705408字节，quick_check=ok。备份目录0700/文件0600。
- 本版schema5；旧代码不识别schema5，不能只回滚源码。故障优先向前修复，若数据库恢复必须评估备份后的新执行数据。
- 待提交后使用SHA256与git bundle verify校验的增量包，REMOTE=/上传路径.bundle bash deploy/update-main-server.sh；保留标准安装、服务检查与Sonic保护。

## 线上验收

待部署后补记健康接口精确版本、资源字节、目录数量/草稿筛选、发版默认值与历史截图报告。不得将本地fixture写为实机验收。
